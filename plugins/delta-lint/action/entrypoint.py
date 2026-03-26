#!/usr/bin/env python3
"""
delta-lint GitHub Action entrypoint.

Modes:
  review   — Post findings as a PR comment (default)
  suggest  — Post findings with GitHub Suggested Changes inline
  autofix  — Generate fixes, commit, and push to the PR branch
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# delta-lint scripts directory (sibling to action/)
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def get_event() -> dict:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise RuntimeError("GITHUB_EVENT_PATH not set — not running in GitHub Actions?")
    with open(event_path) as f:
        return json.load(f)


def get_pr_number() -> int:
    event = get_event()
    if "pull_request" in event:
        return event["pull_request"]["number"]
    # issue_comment event: PR number is in issue.number
    if "issue" in event:
        return event["issue"]["number"]
    raise RuntimeError("Cannot determine PR number from event")


def get_pr_head_ref() -> str:
    event = get_event()
    if "pull_request" in event:
        return event["pull_request"]["head"]["ref"]
    # issue_comment event: fetch PR details to get head ref
    repo = get_repo()
    pr_number = event["issue"]["number"]
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr_number}", "--jq", ".head.ref"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get PR head ref: {result.stderr}")
    return result.stdout.strip()


def get_repo() -> str:
    return os.environ["GITHUB_REPOSITORY"]


def gh_api(method: str, endpoint: str, **fields) -> dict | str:
    """Call GitHub API via gh cli."""
    cmd = ["gh", "api", "--method", method, endpoint]
    for k, v in fields.items():
        cmd.extend(["-f", f"{k}={v}"])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"gh api {method} {endpoint} failed: {result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout


def get_pr_changed_files() -> list[str]:
    """Get changed files from the PR."""
    repo = get_repo()
    pr_number = get_pr_number()
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr_number}/files",
         "--paginate", "--jq", ".[].filename"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get PR files: {result.stderr}")
    return [f for f in result.stdout.strip().split("\n") if f]


def add_reaction(comment_id: int, reaction: str = "eyes"):
    """Add a reaction to a comment to signal the bot is working."""
    repo = get_repo()
    try:
        subprocess.run(
            ["gh", "api", "--method", "POST",
             f"repos/{repo}/issues/comments/{comment_id}/reactions",
             "-f", f"content={reaction}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        pass  # reaction is best-effort


def set_output(name: str, value: str):
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{name}={value}\n")


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def filter_scannable_files(files: list[str]) -> list[str]:
    from retrieval import filter_source_files
    return filter_source_files(files)


def get_pr_diff_text() -> str:
    """Get PR diff via gh api (works even when base branch is not fetched)."""
    repo = get_repo()
    pr_number = get_pr_number()
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr_number}",
         "-H", "Accept: application/vnd.github.v3.diff"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"  Warning: could not fetch PR diff via API: {result.stderr}",
              file=sys.stderr)
        return ""
    return result.stdout


def run_scan(files: list[str], severity: str, model: str):
    """Run scan via scanner.scan() — returns ScanResult."""
    from scanner import scan as engine_scan

    repo_path = os.environ.get("GITHUB_WORKSPACE", ".")
    diff_text = get_pr_diff_text()

    return engine_scan(
        repo_path, files,
        model=model,
        backend="api",
        severity=severity,
        scope="pr",
        no_cache=True,
        on_finding=None,
        diff_text=diff_text,
    )


# ---------------------------------------------------------------------------
# Fix generation (for suggest + autofix modes)
# — delegates to fixgen.py (single source of truth)
# ---------------------------------------------------------------------------

from fixgen import generate_fixes  # noqa: E402 — imported after sys.path setup


# ---------------------------------------------------------------------------
# Mode: review (comment only)
# ---------------------------------------------------------------------------

def _render_heatmap(findings: list[dict]) -> str:
    """Render affected files heatmap (CI-specific, depends on finding locations)."""
    file_hits: dict[str, dict] = {}
    for f in findings:
        if f.get("parse_error"):
            continue
        loc = f.get("location", {})
        sev = f.get("severity", "medium").lower()
        for key in ("file_a", "file_b"):
            fp = loc.get(key, "")
            if fp and fp != "?":
                if fp not in file_hits:
                    file_hits[fp] = {"high": 0, "medium": 0, "low": 0}
                file_hits[fp][sev] = file_hits[fp].get(sev, 0) + 1
    if not file_hits:
        return ""
    sorted_files = sorted(
        file_hits.items(),
        key=lambda x: (x[1].get("high", 0), sum(x[1].values())),
        reverse=True,
    )
    lines = ["<details><summary>Affected files heatmap</summary>\n"]
    lines.append("| File | :red_circle: | :orange_circle: | :white_circle: | Total |")
    lines.append("|------|-----|-----|-----|-------|")
    for fp, counts in sorted_files[:15]:
        total = sum(counts.values())
        h = counts.get("high", 0) or ""
        m = counts.get("medium", 0) or ""
        lo = counts.get("low", 0) or ""
        lines.append(f"| `{fp}` | {h} | {m} | {lo} | {total} |")
    if len(sorted_files) > 15:
        lines.append(f"| *... +{len(sorted_files) - 15} more* | | | | |")
    lines.append("\n</details>\n")
    return "\n".join(lines)


def _render_pattern_dist(findings: list[dict]) -> str:
    """Render pattern distribution table (CI-specific)."""
    pattern_counts: dict[str, int] = {}
    for f in findings:
        if not f.get("parse_error"):
            p = f.get("pattern", "?")
            pattern_counts[p] = pattern_counts.get(p, 0) + 1
    if len(pattern_counts) <= 1:
        return ""
    lines = ["<details><summary>Pattern distribution</summary>\n"]
    lines.append("| Pattern | Count |")
    lines.append("|---------|-------|")
    for p, c in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {p} | {c} |")
    lines.append("\n</details>\n")
    return "\n".join(lines)


def format_review_comment(scan_result, files: list[str],
                          severity: str, mode: str) -> str:
    """Format PR comment using output_formats.format_pr_markdown() as base,
    with CI-specific heatmap and pattern distribution appended."""
    from output_formats import format_pr_markdown

    base = format_pr_markdown(scan_result, repo_name=get_repo())

    if not scan_result.shown:
        return base

    # Insert heatmap and pattern distribution before the footer
    heatmap = _render_heatmap(scan_result.shown)
    pattern_dist = _render_pattern_dist(scan_result.shown)

    # Append CI-specific sections
    extra = ""
    if heatmap:
        extra += "\n" + heatmap
    if pattern_dist:
        extra += "\n" + pattern_dist

    if extra:
        # Insert before the footer line (---) if present, else append
        if "\n---\n" in base:
            parts = base.split("\n---\n", 1)
            base = parts[0] + extra + "\n---\n" + parts[1]
        else:
            base = base.rstrip("\n") + "\n" + extra

    return base


def post_or_update_comment(body: str) -> int | None:
    repo = get_repo()
    pr_number = get_pr_number()
    marker = "<!-- delta-lint-comment -->"
    body_with_marker = f"{marker}\n{body}"

    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{pr_number}/comments",
         "--paginate", "--jq",
         f'.[] | select(.body | startswith("{marker}")) | .id'],
        capture_output=True, text=True, timeout=30,
    )
    existing_id = result.stdout.strip().split("\n")[0] if result.stdout.strip() else None

    if existing_id:
        resp = gh_api("PATCH", f"repos/{repo}/issues/comments/{existing_id}",
                       body=body_with_marker)
        print(f"Updated existing comment {existing_id}", file=sys.stderr)
        return int(existing_id)
    else:
        resp = gh_api("POST", f"repos/{repo}/issues/{pr_number}/comments",
                       body=body_with_marker)
        comment_id = resp.get("id") if isinstance(resp, dict) else None
        print(f"Created comment {comment_id}", file=sys.stderr)
        return comment_id


# ---------------------------------------------------------------------------
# Check Run annotations
# ---------------------------------------------------------------------------

def get_head_sha() -> str:
    """Get HEAD commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


def post_check_annotations(scan_result):
    """Post Check Run with annotations via GitHub Checks API."""
    from output_formats import format_annotations

    annotations = format_annotations(scan_result)
    repo = get_repo()
    head_sha = get_head_sha()

    n_findings = len(scan_result.shown)
    conclusion = "success" if n_findings == 0 else "neutral"
    title = f"delta-lint: {n_findings} finding(s)" if n_findings else "delta-lint: clean"
    summary = f"{n_findings} structural contradiction(s) detected." if n_findings else "No contradictions found."

    # GitHub Checks API limits to 50 annotations per request
    batch_size = 50
    first_batch = annotations[:batch_size]

    check_body = {
        "name": "delta-lint",
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": title,
            "summary": summary,
            "annotations": first_batch,
        },
    }

    try:
        result = subprocess.run(
            ["gh", "api", "--method", "POST",
             f"repos/{repo}/check-runs",
             "--input", "-"],
            input=json.dumps(check_body),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"  Check Run created: {title}", file=sys.stderr)
            # Post remaining annotations in batches via update
            if len(annotations) > batch_size:
                check_run = json.loads(result.stdout)
                check_run_id = check_run.get("id")
                for i in range(batch_size, len(annotations), batch_size):
                    batch = annotations[i:i + batch_size]
                    update_body = {
                        "output": {
                            "title": title,
                            "summary": summary,
                            "annotations": batch,
                        },
                    }
                    subprocess.run(
                        ["gh", "api", "--method", "PATCH",
                         f"repos/{repo}/check-runs/{check_run_id}",
                         "--input", "-"],
                        input=json.dumps(update_body),
                        capture_output=True, text=True, timeout=30,
                    )
        else:
            print(f"  Warning: Check Run creation failed: {result.stderr}",
                  file=sys.stderr)
    except Exception as e:
        print(f"  Warning: Check Run creation failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Mode: suggest (PR review with suggested changes)
# ---------------------------------------------------------------------------

def post_suggestions(findings: list[dict], fixes: list[dict], scan_result: dict,
                     files: list[str], severity: str):
    """Post a PR review with inline suggested changes."""
    repo = get_repo()
    pr_number = get_pr_number()

    # Build review comments with suggestions
    review_comments = []
    for fix in fixes:
        file_path = fix.get("file", "")
        line = fix.get("line", 1)
        new_code = fix.get("new_code", "")
        explanation = fix.get("explanation", "")
        finding = fix.get("_finding", {})

        pattern = finding.get("pattern", "?")
        sev = finding.get("severity", "?")
        contradiction = finding.get("contradiction", "")

        body = (
            f"**delta-lint** Pattern {pattern} ({sev})\n\n"
            f"{contradiction}\n\n"
            f"{explanation}\n\n"
            f"```suggestion\n{new_code}\n```"
        )

        comment = {
            "path": file_path,
            "line": line,
            "body": body,
        }

        # Multi-line suggestion: add start_line if old_code spans multiple lines
        old_code = fix.get("old_code", "")
        old_lines = old_code.strip().split("\n")
        if len(old_lines) > 1:
            comment["start_line"] = max(1, line - len(old_lines) + 1)

        review_comments.append(comment)

    if not review_comments:
        # No fixes generated — fall back to review comment
        comment_body = format_review_comment(scan_result, files, severity, "suggest")
        comment_body += "\n\n> ⚠️ Could not generate suggested changes for these findings."
        post_or_update_comment(comment_body)
        return

    # Also post summary comment
    summary = format_review_comment(scan_result, files, severity, "suggest")
    n_fixes = len(review_comments)
    summary += f"\n\n✏️ **{n_fixes} suggested change(s)** posted as inline review comments."
    post_or_update_comment(summary)

    # Post PR review with suggestions
    review_body = {
        "body": f"delta-lint found {len(findings)} contradiction(s). "
                f"Suggested {n_fixes} fix(es) below.",
        "event": "COMMENT",
        "comments": review_comments,
    }

    # Use gh api with raw JSON input
    result = subprocess.run(
        ["gh", "api", "--method", "POST",
         f"repos/{repo}/pulls/{pr_number}/reviews",
         "--input", "-"],
        input=json.dumps(review_body),
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode == 0:
        print(f"Posted PR review with {n_fixes} suggestions", file=sys.stderr)
    else:
        print(f"Failed to post PR review: {result.stderr}", file=sys.stderr)
        # Fallback: post fixes in the summary comment
        fallback = summary + "\n\n**Inline suggestions failed. Fixes below:**\n\n"
        for fix in fixes:
            fallback += f"**`{fix['file']}` line {fix.get('line', '?')}**:\n"
            fallback += f"```diff\n- {fix.get('old_code', '')}\n+ {fix.get('new_code', '')}\n```\n"
            fallback += f"{fix.get('explanation', '')}\n\n"
        post_or_update_comment(fallback)


# ---------------------------------------------------------------------------
# Mode: autofix (commit fixes to PR branch)
# ---------------------------------------------------------------------------

def apply_and_push_fixes(fixes: list[dict], scan_result: dict,
                         files: list[str], severity: str) -> int:
    """Apply fixes to files, commit, and push to the PR branch."""
    repo_path = os.environ.get("GITHUB_WORKSPACE", ".")
    applied = []

    for fix in fixes:
        file_path = fix.get("file", "")
        old_code = fix.get("old_code", "")
        new_code = fix.get("new_code", "")

        if not file_path or not old_code or not new_code:
            continue

        full_path = Path(repo_path) / file_path
        if not full_path.exists():
            print(f"  Skip: {file_path} not found", file=sys.stderr)
            continue

        content = full_path.read_text(encoding="utf-8")
        if old_code not in content:
            # Try with trailing whitespace stripped (match only; preserve original content)
            old_lines = "\n".join(l.rstrip() for l in old_code.splitlines())
            content_stripped = "\n".join(l.rstrip() for l in content.splitlines())
            if old_lines not in content_stripped:
                print(f"  Skip: old_code not found in {file_path}", file=sys.stderr)
                continue
            # Compute line range in stripped content, then splice into original
            idx = content_stripped.index(old_lines)
            line_start = content_stripped[:idx].count("\n")
            line_end = line_start + old_lines.count("\n")
            lines = content.splitlines(True)
            new_content = "".join(lines[:line_start]) + new_code + "".join(lines[line_end + 1:])
            full_path.write_text(new_content, encoding="utf-8")
        else:
            new_content = content.replace(old_code, new_code, 1)
            full_path.write_text(new_content, encoding="utf-8")

        applied.append(fix)
        print(f"  Applied fix: {file_path} line {fix.get('line', '?')}", file=sys.stderr)

    if not applied:
        # No fixes applied — fall back to review comment
        comment_body = format_review_comment(scan_result, files, severity, "autofix")
        comment_body += "\n\n> ⚠️ Autofix could not apply changes. Manual review required."
        post_or_update_comment(comment_body)
        return 0

    # Configure git
    subprocess.run(
        ["git", "config", "user.name", "delta-lint[bot]"],
        cwd=repo_path, capture_output=True, timeout=10,
    )
    subprocess.run(
        ["git", "config", "user.email", "delta-lint[bot]@users.noreply.github.com"],
        cwd=repo_path, capture_output=True, timeout=10,
    )

    # Stage and commit
    for fix in applied:
        subprocess.run(
            ["git", "add", fix["file"]],
            cwd=repo_path, capture_output=True, timeout=10,
        )

    fix_summary = ", ".join(
        f"{f['file']}:{f.get('line', '?')}" for f in applied
    )
    commit_msg = f"fix: resolve structural contradictions detected by delta-lint\n\n{fix_summary}"

    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=repo_path, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"  Commit failed: {result.stderr}", file=sys.stderr)
        return 0

    # Push to PR branch
    head_ref = get_pr_head_ref()
    result = subprocess.run(
        ["git", "push", "origin", f"HEAD:{head_ref}"],
        cwd=repo_path, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"  Push failed: {result.stderr}", file=sys.stderr)
        return 0

    print(f"  Pushed {len(applied)} fix(es) to {head_ref}", file=sys.stderr)

    # Post summary comment
    comment_body = format_review_comment(scan_result, files, severity, "autofix")
    comment_body += (
        f"\n\n✅ **Autofix applied {len(applied)} change(s)** and pushed to `{head_ref}`.\n\n"
        "| File | Line | Fix |\n|------|------|-----|\n"
    )
    for fix in applied:
        comment_body += (
            f"| `{fix['file']}` | {fix.get('line', '?')} "
            f"| {fix.get('explanation', '')} |\n"
        )
    post_or_update_comment(comment_body)

    return len(applied)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="delta-lint GitHub Action")
    parser.add_argument("--mode", default="review",
                        choices=["review", "suggest", "autofix"])
    parser.add_argument("--severity", default="high")
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--max-diff-files", type=int, default=20)
    parser.add_argument("--comment-on-clean", default="false")
    parser.add_argument("--fail-on-findings", default="false")
    parser.add_argument("--fail-severity", default="none",
                        choices=["high", "medium", "low", "none"])
    args = parser.parse_args()

    # 0. If triggered by comment, add 👀 reaction to acknowledge
    event = get_event()
    if "comment" in event:
        add_reaction(event["comment"]["id"], "eyes")
        # Override mode from comment text if specified: /delta-review suggest
        comment_body = event["comment"].get("body", "")
        for m in ["autofix", "suggest", "review"]:
            if m in comment_body.lower():
                args.mode = m
                print(f"  Mode override from comment: {m}", file=sys.stderr)
                break

    # 1. Get PR changed files
    print("Getting PR changed files...", file=sys.stderr)
    all_files = get_pr_changed_files()
    print(f"  PR has {len(all_files)} changed file(s)", file=sys.stderr)

    if len(all_files) > args.max_diff_files:
        print(f"  Skipping: {len(all_files)} files exceeds limit ({args.max_diff_files})",
              file=sys.stderr)
        set_output("findings_count", "0")
        set_output("fixed_count", "0")
        return

    # 2. Filter to scannable source files
    source_files = filter_scannable_files(all_files)
    print(f"  {len(source_files)} source file(s) to scan", file=sys.stderr)

    if not source_files:
        print("  No source files to scan", file=sys.stderr)
        set_output("findings_count", "0")
        set_output("fixed_count", "0")
        return

    # 3. Run scan
    print(f"Running delta-lint scan (mode={args.mode}, model={args.model}, "
          f"severity>={args.severity})...", file=sys.stderr)
    scan_result = run_scan(source_files, args.severity, args.model)
    findings = scan_result.shown
    findings_count = len(findings)
    print(f"  {findings_count} finding(s)", file=sys.stderr)
    set_output("findings_count", str(findings_count))

    # 3.5. Post Check Run annotations (always, even if 0 findings)
    post_check_annotations(scan_result)

    # 4. No findings — optional clean comment
    if findings_count == 0:
        set_output("fixed_count", "0")
        if args.comment_on_clean == "true":
            body = format_review_comment(scan_result, source_files, args.severity, args.mode)
            post_or_update_comment(body)
        return

    # 5. Mode dispatch
    fixed_count = 0

    if args.mode == "review":
        body = format_review_comment(scan_result, source_files, args.severity, "review")
        comment_id = post_or_update_comment(body)
        if comment_id:
            set_output("comment_id", str(comment_id))

    elif args.mode == "suggest":
        print("Generating fixes for suggested changes...", file=sys.stderr)
        fixes = generate_fixes(findings, scan_result.context, args.model)
        print(f"  {len(fixes)} fix(es) generated", file=sys.stderr)
        post_suggestions(findings, fixes, scan_result, source_files, args.severity)
        fixed_count = len(fixes)

    elif args.mode == "autofix":
        print("Generating fixes for autofix...", file=sys.stderr)
        fixes = generate_fixes(findings, scan_result.context, args.model)
        print(f"  {len(fixes)} fix(es) generated", file=sys.stderr)
        fixed_count = apply_and_push_fixes(fixes, scan_result, source_files, args.severity)

    set_output("fixed_count", str(fixed_count))

    # 6. Exit code — severity-based merge block
    fail_sev = args.fail_severity
    if fail_sev != "none":
        sev_order = {"high": 1, "medium": 2, "low": 3}
        threshold = sev_order.get(fail_sev, 0)
        blocking = [f for f in findings
                    if sev_order.get(f.get("severity", "low").lower(), 3) <= threshold]
        if blocking:
            print(f"  {len(blocking)} finding(s) at {fail_sev}+ severity -> exit 1",
                  file=sys.stderr)
            sys.exit(1)
    elif args.fail_on_findings == "true" and findings_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
