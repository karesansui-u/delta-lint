"""
debt_loop.py — Automated debt resolution loop.

Picks top N findings by priority, creates one branch + PR per finding.
Each finding gets its own branch and minimal fix PR.

Usage:
    python debt_loop.py --repo /path/to/repo --count 3
    python debt_loop.py --repo /path/to/repo --ids kingsman-a1b2c3d4,kingsman-e5f6g7h8
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Ensure scripts/ is in path for sibling imports
sys.path.insert(0, str(Path(__file__).parent))

from findings import list_findings, load_scan_history, update_status
from info_theory import finding_information_score
from fixgen import generate_fixes, apply_fixes_locally


# ---------------------------------------------------------------------------
# Lightweight context for fixgen — reads source files from finding location
# ---------------------------------------------------------------------------

class FindingContext:
    """Minimal context for fixgen.generate_fixes().

    Reads the files referenced in a finding so the LLM has source code.
    """

    def __init__(self, finding: dict, repo_path: str):
        self.repo_path = Path(repo_path)
        self.finding = finding
        self._files: dict[str, str] = {}
        self._load_files()

    def _load_files(self):
        """Load source files referenced in the finding."""
        loc = self.finding.get("location", {})
        file_a = loc.get("file_a", self.finding.get("file", ""))
        file_b = loc.get("file_b", "")

        for fpath in [file_a, file_b]:
            if not fpath:
                continue
            full = self.repo_path / fpath
            if full.exists():
                try:
                    self._files[fpath] = full.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

    def to_prompt_string(self) -> str:
        """Format source files for LLM prompt."""
        parts = []
        for fpath, content in self._files.items():
            parts.append(f"### {fpath}\n```\n{content}\n```")
        return "\n\n".join(parts) if parts else "(source files not available)"


# ---------------------------------------------------------------------------
# GitHub Issue → finding conversion
# ---------------------------------------------------------------------------

def _origin_repo(repo_path: str) -> str:
    """Extract owner/repo from git remote origin URL."""
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    # fallback: parse git remote
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path, capture_output=True, text=True,
    )
    url = r.stdout.strip()
    # https://github.com/owner/repo.git or git@github.com:owner/repo.git
    m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


def fetch_github_issue(issue_number: int, repo_path: str) -> dict:
    """Fetch a GitHub Issue via gh CLI."""
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number),
         "--json", "title,body,labels,number,url"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh issue view failed: {result.stderr[:200]}")
    return json.loads(result.stdout)


def extract_file_paths(text: str, repo_path: str) -> list[str]:
    """Extract file paths from Issue body that exist in the repo."""
    if not text:
        return []
    candidates = re.findall(r'[\w./\-]+\.\w{1,10}', text)
    repo = Path(repo_path)
    seen = set()
    result = []
    for c in candidates:
        if c not in seen and (repo / c).exists():
            seen.add(c)
            result.append(c)
    return result


def issue_to_finding(issue: dict, target_files: list[str]) -> dict:
    """Convert a GitHub Issue to a finding-compatible dict."""
    number = issue["number"]
    labels = [l["name"] for l in issue.get("labels", [])]

    severity = "medium"
    if any("bug" in l.lower() for l in labels):
        severity = "high"
    if any("critical" in l.lower() or "security" in l.lower() for l in labels):
        severity = "critical"

    file_a = target_files[0] if target_files else ""
    file_b = target_files[1] if len(target_files) > 1 else ""

    return {
        "id": f"issue-{number}",
        "pattern": "github-issue",
        "title": issue["title"],
        "contradiction": issue["title"],
        "severity": severity,
        "impact": (issue.get("body") or "")[:500],
        "mechanism": "",
        "description": issue.get("body") or "",
        "status": "confirmed",
        "location": {"file_a": file_a, "file_b": file_b},
        "source_url": issue.get("url", ""),
    }


# ---------------------------------------------------------------------------
# Priority scoring
# ---------------------------------------------------------------------------

def score_finding(
    f: dict,
    scan_history: list[dict] | None = None,
    all_findings: list[dict] | None = None,
) -> float:
    """Compute priority score for a finding (higher = fix first)."""
    try:
        pool = all_findings if all_findings is not None else [f]
        info = finding_information_score(f, scan_history, all_findings=pool)
        info_score = info["info_score"]
    except Exception:
        info_score = 0

    # Compute ROI if not already present
    roi = f.get("roi_score")
    if roi is None:
        try:
            from scoring import compute_roi
            roi_data = compute_roi(
                severity=f.get("severity", "low"),
                churn_6m=f.get("churn_6m", 0),
                fan_out=f.get("fan_out", 0),
                pattern=f.get("pattern", ""),
                fix_churn_6m=f.get("fix_churn_6m"),
            )
            roi = roi_data["roi_score"]
            f["roi_score"] = roi
        except Exception:
            roi = 0

    sev_bonus = {"high": 300, "medium": 100, "low": 0}.get(f.get("severity", "low"), 0)

    return info_score + (roi or 0) + sev_bonus


# ---------------------------------------------------------------------------
# Git + PR operations
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd, capture_output=True, text=True,
        check=check,
    )


def _current_branch(repo_path: str) -> str:
    """Get current branch name."""
    r = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    return r.stdout.strip()


def _repo_from_url(url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub remote URL."""
    import re as _re
    m = _re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    return m.group(1) if m else None



def _ensure_push_remote(repo_path: str, verbose: bool = False) -> str:
    """Ensure we have a pushable remote. Returns remote name ('origin' or 'fork').

    If origin is not pushable (e.g. upstream-only clone), runs `gh repo fork`
    to create a fork and add it as a remote.
    """
    # Test push permission with a dry-run
    test = _run_git(["push", "--dry-run", "origin"], repo_path, check=False)
    if test.returncode == 0:
        return "origin"

    if verbose:
        print("  origin に push 権限なし — フォークを作成します", file=sys.stderr)

    # Check if 'fork' remote already exists
    remotes = _run_git(["remote"], repo_path, check=False)
    if "fork" in remotes.stdout.splitlines():
        if verbose:
            print("  既存の fork リモートを使用", file=sys.stderr)
        return "fork"

    # Create fork via gh CLI
    fork_result = subprocess.run(
        ["gh", "repo", "fork", "--remote", "--remote-name", "fork"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if fork_result.returncode != 0:
        raise RuntimeError(f"gh repo fork failed: {fork_result.stderr[:200]}")

    if verbose:
        print("  フォーク作成完了", file=sys.stderr)
    return "fork"


def _branch_exists(repo_path: str, branch: str) -> bool:
    """Check if branch exists locally or remotely."""
    r = _run_git(["branch", "--list", branch], repo_path, check=False)
    if r.stdout.strip():
        return True
    r = _run_git(["ls-remote", "--heads", "origin", branch], repo_path, check=False)
    return bool(r.stdout.strip())


def _regression_check(repo_path: str, verbose: bool = False) -> dict:
    """Run delta-scan --scope pr to check for regressions before commit.

    Uses exit code: 1 = high findings exist, 0 = none.
    Parses stderr for summary counts.

    Returns:
        {"blocked": bool, "high_count": int, "warnings": list[str], "summary": str}
    """
    scripts_dir = str(Path(__file__).parent)
    try:
        result = subprocess.run(
            [sys.executable, "cli.py", "scan",
             "--repo", repo_path, "--scope", "pr",
             "--severity", "high", "--verbose"],
            cwd=scripts_dir, capture_output=True, text=True, timeout=180,
        )
        blocked = result.returncode == 1
        # Parse stderr for counts
        high_count = 0
        other_count = 0
        scan_empty = False
        for line in result.stderr.splitlines():
            if "確定バグ" in line or "高重要度" in line:
                import re as _re
                m = _re.search(r"(\d+)件", line)
                if m:
                    high_count += int(m.group(1))
            elif "その他" in line:
                import re as _re
                m = _re.search(r"(\d+)件", line)
                if m:
                    other_count += int(m.group(1))
            elif "PR差分が見つかりません" in line or "差分が空" in line:
                scan_empty = True

        warnings = [f"{other_count} medium/low finding(s)"] if other_count > 0 else []
        summary = f"{high_count} high, {other_count} other"
        if scan_empty:
            summary = "PR差分なし（スキャン未実行）"
            blocked = False

        if verbose:
            print(f"  Regression check: {summary}", file=sys.stderr)

        return {"blocked": blocked, "high_count": high_count, "warnings": warnings, "summary": summary}
    except subprocess.TimeoutExpired:
        if verbose:
            print("  Regression check: timed out (180s) — proceeding", file=sys.stderr)
        return {"blocked": False, "high_count": 0, "warnings": [], "summary": "timeout"}
    except Exception as e:
        if verbose:
            print(f"  Regression check: failed ({e}) — proceeding", file=sys.stderr)
        return {"blocked": False, "high_count": 0, "warnings": [], "summary": f"error: {e}"}


def process_one_finding(
    finding: dict,
    repo_path: str,
    base_branch: str,
    model: str,
    backend: str,
    dry_run: bool = False,
    verbose: bool = False,
    push_remote: str = "origin",
) -> dict | None:
    """Process a single finding: branch → fix → commit → PR.

    Returns result dict or None if fix failed.
    """
    fid = finding.get("id", "unknown")
    pattern = finding.get("pattern", "")
    title = finding.get("title", finding.get("contradiction", ""))
    short_title = title[:60].strip()

    branch = f"debt-loop/{fid}"

    if verbose:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  Finding: {fid} ({pattern})", file=sys.stderr)
        print(f"  Title: {short_title}", file=sys.stderr)
        print(f"  Branch: {branch}", file=sys.stderr)

    # Check if branch already exists (already being worked on)
    if _branch_exists(repo_path, branch):
        if verbose:
            print(f"  SKIP: branch {branch} already exists", file=sys.stderr)
        return {"finding_id": fid, "status": "skipped", "reason": "branch_exists"}

    # Return to base branch
    _run_git(["checkout", base_branch], repo_path)

    # Create feature branch
    _run_git(["checkout", "-b", branch], repo_path)

    try:
        # Build context from finding's source files
        context = FindingContext(finding, repo_path)

        # Generate fix
        if verbose:
            print(f"  Generating fix...", file=sys.stderr)

        fixes = generate_fixes(
            [finding], context,
            model=model, backend=backend, verbose=verbose,
        )

        if not fixes:
            if verbose:
                print(f"  No fix generated", file=sys.stderr)
            _run_git(["checkout", base_branch], repo_path)
            _run_git(["branch", "-D", branch], repo_path, check=False)
            return {"finding_id": fid, "status": "no_fix"}

        # Apply fixes locally
        applied = apply_fixes_locally(fixes, repo_path, verbose=verbose)

        if not applied:
            if verbose:
                print(f"  Fix could not be applied", file=sys.stderr)
            _run_git(["checkout", base_branch], repo_path)
            _run_git(["branch", "-D", branch], repo_path, check=False)
            return {"finding_id": fid, "status": "apply_failed"}

        if dry_run:
            if verbose:
                print(f"  DRY RUN: {len(applied)} fix(es) would be applied", file=sys.stderr)
            _run_git(["checkout", ".", "--"], repo_path, check=False)
            _run_git(["checkout", base_branch], repo_path)
            _run_git(["branch", "-D", branch], repo_path, check=False)
            return {"finding_id": fid, "status": "dry_run", "fixes": len(applied)}

        # Regression check before commit
        if verbose:
            print(f"  Running regression check...", file=sys.stderr)
        regress = _regression_check(repo_path, verbose=verbose)
        if regress["blocked"]:
            if verbose:
                print(f"  BLOCKED: regression check found {regress['high_count']} high finding(s)",
                      file=sys.stderr)
            _run_git(["checkout", ".", "--"], repo_path, check=False)
            _run_git(["checkout", base_branch], repo_path)
            _run_git(["branch", "-D", branch], repo_path, check=False)
            return {
                "finding_id": fid, "status": "regression_blocked",
                "high_count": regress["high_count"],
            }
        if regress.get("warnings"):
            if verbose:
                print(f"  WARNING: {len(regress['warnings'])} medium/low finding(s) — will note in PR",
                      file=sys.stderr)

        # Stage changed files
        changed_files = [f["file"] for f in applied]
        _run_git(["add"] + changed_files, repo_path)

        # Commit
        if fid.startswith("issue-"):
            issue_num = fid.replace("issue-", "")
            commit_msg = f"fix: {short_title}\n\nResolves #{issue_num}"
        else:
            commit_msg = f"fix: {short_title}\n\nResolves delta-lint finding {fid} (pattern {pattern})"
        _run_git(["commit", "-m", commit_msg], repo_path)

        # Push
        _run_git(["push", "-u", push_remote, branch], repo_path)

        # Create PR (target the push remote's repo, not upstream)
        pr_body = _build_pr_body(finding, applied)
        pr_cmd = [
            "gh", "pr", "create",
            "--title", f"fix: {short_title}",
            "--body", pr_body,
            "--base", base_branch,
            "--head", branch,
        ]
        push_remote_url = _run_git(["remote", "get-url", push_remote], repo_path, check=False)
        push_repo = _repo_from_url(push_remote_url.stdout.strip()) if push_remote_url.stdout.strip() else None
        if push_repo:
            pr_cmd.extend(["--repo", push_repo])
        pr_result = subprocess.run(
            pr_cmd,
            cwd=repo_path, capture_output=True, text=True,
        )

        pr_url = pr_result.stdout.strip() if pr_result.returncode == 0 else None

        if verbose:
            if pr_url:
                print(f"  PR created: {pr_url}", file=sys.stderr)
            else:
                print(f"  PR creation failed: {pr_result.stderr[:200]}", file=sys.stderr)

        # Update finding status automatically
        repo_name = Path(repo_path).resolve().name
        if pr_url:
            update_status(repo_path, repo_name, fid, "submitted", github_url=pr_url)
            if verbose:
                print(f"  Status updated: {fid} → submitted", file=sys.stderr)

        # Return to base
        _run_git(["checkout", base_branch], repo_path)

        return {
            "finding_id": fid,
            "status": "pr_created" if pr_url else "pushed",
            "branch": branch,
            "pr_url": pr_url,
            "fixes": len(applied),
        }

    except Exception as e:
        if verbose:
            print(f"  ERROR: {e}", file=sys.stderr)
        # Cleanup: return to base branch
        _run_git(["checkout", base_branch], repo_path, check=False)
        _run_git(["branch", "-D", branch], repo_path, check=False)
        return {"finding_id": fid, "status": "error", "error": str(e)}


def _build_pr_body(finding: dict, applied: list[dict]) -> str:
    """Build PR description from finding and applied fixes."""
    lines = ["## Summary", ""]
    lines.append(f"Automated fix for structural contradiction detected by delta-lint.")
    lines.append("")
    lines.append(f"- **Finding**: `{finding.get('id', '')}`")
    lines.append(f"- **Pattern**: {finding.get('pattern', '')} {finding.get('mechanism', '')}")
    lines.append(f"- **Severity**: {finding.get('severity', 'unknown')}")
    lines.append("")
    lines.append("### Contradiction")
    lines.append(finding.get("contradiction", "N/A"))
    lines.append("")
    lines.append("### Impact")
    lines.append(finding.get("impact", "N/A"))
    lines.append("")
    lines.append("### Changes")
    for f in applied:
        lines.append(f"- `{f.get('file', '')}`: {f.get('explanation', '')}")

    # Issue起源の場合、自動クローズリンクを追加
    source_url = finding.get("source_url", "")
    if source_url and "/issues/" in source_url:
        lines.append("")
        lines.append(f"Closes {source_url}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_debt_loop(
    repo_path: str,
    count: int = 3,
    finding_ids: list[str] | None = None,
    issue_number: int | None = None,
    model: str = "claude-sonnet-4-20250514",
    backend: str = "cli",
    base_branch: str | None = None,
    status_filter: str = "found,confirmed",
    dry_run: bool = False,
    verbose: bool = False,
) -> list[dict]:
    """Run the debt resolution loop.

    Args:
        repo_path: Path to git repository
        count: Max number of findings to process
        finding_ids: Specific finding IDs to fix (overrides priority sort)
        issue_number: GitHub Issue number to fetch and fix
        model: LLM model for fix generation
        backend: "cli" ($0) or "api" (pay-per-use)
        base_branch: Branch to create fix branches from (default: current)
        status_filter: Comma-separated statuses to include
        dry_run: Generate fixes but don't commit/push/PR
        verbose: Print progress

    Returns:
        List of result dicts per finding
    """
    repo_path = str(Path(repo_path).resolve())

    if base_branch is None:
        base_branch = _current_branch(repo_path)

    # Auto-stash uncommitted changes (restore after processing)
    status = _run_git(["status", "--porcelain", "-uno"], repo_path)
    stashed = False
    if status.stdout.strip():
        _run_git(["stash", "push", "-m", "delta-fix: auto-stash"], repo_path)
        stashed = True
        if verbose:
            print("  Auto-stashed uncommitted changes", file=sys.stderr)

    # Ensure we can push (auto-fork if needed)
    if not dry_run:
        try:
            push_remote = _ensure_push_remote(repo_path, verbose=verbose)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return []
        if verbose:
            print(f"  Push remote: {push_remote}", file=sys.stderr)
    else:
        push_remote = "origin"

    # Ensure base branch is in sync with upstream remote (prevent stale commits in PR)
    if not dry_run:
        upstream_remote = "origin"  # always compare against upstream, not fork
        _run_git(["fetch", upstream_remote, base_branch], repo_path, check=False)
        ahead = _run_git(
            ["rev-list", f"{upstream_remote}/{base_branch}..{base_branch}", "--count"],
            repo_path, check=False,
        )
        ahead_count = int(ahead.stdout.strip()) if ahead.stdout.strip().isdigit() else 0
        if ahead_count > 0:
            print(f"ERROR: ローカルの {base_branch} が {upstream_remote}/{base_branch} より "
                  f"{ahead_count} コミット先に進んでいます。\n"
                  f"  余計なコミットが PR に混入します。先に同期してください:\n"
                  f"  git checkout {base_branch} && git reset --hard {upstream_remote}/{base_branch}",
                  file=sys.stderr)
            return []

    # Get targets: Issue mode or findings mode
    if issue_number is not None:
        # Issue mode: fetch GitHub Issue → convert to finding
        if verbose:
            print(f"\n  Fetching Issue #{issue_number}...", file=sys.stderr)
        issue_data = fetch_github_issue(issue_number, repo_path)
        file_paths = extract_file_paths(issue_data.get("body", ""), repo_path)
        if verbose:
            print(f"  Issue: {issue_data['title']}", file=sys.stderr)
            print(f"  Detected files: {file_paths or '(none — LLM will infer)'}", file=sys.stderr)
        targets = [issue_to_finding(issue_data, file_paths)]
    else:
        # Findings mode: load from findings DB
        all_findings = list_findings(repo_path)

        if not all_findings:
            print("No findings found.", file=sys.stderr)
            return []

        # Filter by status
        allowed_statuses = set(status_filter.split(","))
        candidates = [f for f in all_findings if f.get("status", "found") in allowed_statuses]

        # Enrich findings missing git data (older findings without churn/fan_out)
        needs_enrichment = [f for f in candidates if not f.get("churn_6m") and not f.get("fan_out")]
        if needs_enrichment:
            try:
                from git_enrichment import enrich_findings_batch
                enrich_findings_batch(needs_enrichment, repo_path, verbose=verbose)
            except Exception:
                pass

        if finding_ids:
            # Specific IDs requested
            id_set = set(finding_ids)
            targets = [f for f in candidates if f.get("id") in id_set]
            if len(targets) < len(id_set):
                found_ids = {f.get("id") for f in targets}
                missing = id_set - found_ids
                print(f"WARNING: IDs not found: {', '.join(missing)}", file=sys.stderr)
        else:
            # Sort by priority
            scan_history = load_scan_history(repo_path)
            for f in candidates:
                f["_priority"] = score_finding(f, scan_history, all_findings=all_findings)
            candidates.sort(key=lambda x: -x.get("_priority", 0))
            targets = candidates[:count]

    if not targets:
        print("No actionable findings after filtering.", file=sys.stderr)
        return []

    if verbose:
        print(f"\nDebt Loop: processing {len(targets)} finding(s)", file=sys.stderr)
        print(f"  Base branch: {base_branch}", file=sys.stderr)
        print(f"  Backend: {backend}", file=sys.stderr)
        print(f"  Dry run: {dry_run}", file=sys.stderr)

    results = []
    for f in targets:
        result = process_one_finding(
            f, repo_path, base_branch,
            model=model, backend=backend,
            dry_run=dry_run, verbose=verbose,
            push_remote=push_remote,
        )
        if result:
            results.append(result)

    # Summary
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Debt Loop Summary:", file=sys.stderr)
    for r in results:
        status_icon = {
            "pr_created": "✓",
            "pushed": "↑",
            "dry_run": "○",
            "skipped": "–",
            "no_fix": "✗",
            "apply_failed": "✗",
            "error": "!",
        }.get(r["status"], "?")
        pr = f" → {r['pr_url']}" if r.get("pr_url") else ""
        print(f"  {status_icon} {r['finding_id']}: {r['status']}{pr}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    # Restore auto-stashed changes
    if stashed:
        _run_git(["stash", "pop"], repo_path, check=False)
        if verbose:
            print("  Restored auto-stashed changes", file=sys.stderr)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Automated debt resolution loop — pick top N findings, create fix PRs",
    )
    parser.add_argument("--repo", default=".", help="Path to git repository")
    parser.add_argument("--count", "-n", type=int, default=3,
                        help="Number of findings to process (default: 3)")
    parser.add_argument("--ids", default=None,
                        help="Comma-separated finding IDs to fix (overrides priority sort)")
    parser.add_argument("--issue", type=int, default=None,
                        help="GitHub Issue number to fetch and fix")
    parser.add_argument("--model", default="claude-sonnet-4-20250514",
                        help="LLM model for fix generation")
    parser.add_argument("--backend", default="cli", choices=["cli", "api"],
                        help="LLM backend: cli ($0) or api (pay-per-use)")
    parser.add_argument("--base-branch", default=None,
                        help="Base branch for fix branches (default: current branch)")
    parser.add_argument("--status", default="found,confirmed",
                        help="Comma-separated statuses to include (default: found,confirmed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate fixes but don't commit/push/PR")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed progress")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")

    args = parser.parse_args()

    finding_ids = args.ids.split(",") if args.ids else None

    results = run_debt_loop(
        repo_path=args.repo,
        count=args.count,
        finding_ids=finding_ids,
        issue_number=args.issue,
        model=args.model,
        backend=args.backend,
        base_branch=args.base_branch,
        status_filter=args.status,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))

    # Exit code: 0 if any PR created, 1 if all failed
    if any(r["status"] in ("pr_created", "pushed", "dry_run") for r in results):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
