#!/usr/bin/env python3
"""
delta-lint GitHub App — Webhook server.

Receives GitHub webhook events (pull_request, issue_comment) and runs
delta-lint scan, posting results as PR inline review comments + summary.

Usage:
    uvicorn webhook:app --port 3000
    # or: python webhook.py  (dev mode with auto-reload)
"""

import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Config (environment variables)
# ---------------------------------------------------------------------------

GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")
GITHUB_PRIVATE_KEY_PATH = os.environ.get("GITHUB_PRIVATE_KEY_PATH", "")
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Defaults (zero-config)
DEFAULT_SEVERITY = "high"
DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_DIFF_FILES = 20
DEFAULT_SCOPE = "pr"
DEFAULT_LENS = "default"

# Paths
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

log = logging.getLogger("delta-lint-app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="delta-lint", version="0.1.0")


# ---------------------------------------------------------------------------
# GitHub App authentication (JWT → installation token)
# ---------------------------------------------------------------------------

def _load_private_key() -> str:
    """Load GitHub App private key from file or env."""
    key_path = GITHUB_PRIVATE_KEY_PATH
    if key_path and Path(key_path).exists():
        return Path(key_path).read_text()
    key_env = os.environ.get("GITHUB_PRIVATE_KEY", "")
    if key_env:
        return key_env
    raise RuntimeError("No GitHub App private key configured")


def _create_jwt() -> str:
    """Create a JWT for GitHub App authentication."""
    import jwt as pyjwt

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": GITHUB_APP_ID,
    }
    private_key = _load_private_key()
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(installation_id: int) -> str:
    """Exchange JWT for an installation access token."""
    import httpx

    jwt_token = _create_jwt()
    resp = httpx.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["token"]


# ---------------------------------------------------------------------------
# GitHub API client (uses installation token, not gh CLI)
# ---------------------------------------------------------------------------

class GitHubClient:
    """Minimal GitHub API client for a specific installation."""

    def __init__(self, token: str, repo: str):
        import httpx
        self.client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=60,
        )
        self.repo = repo

    def get_pr_files(self, pr_number: int) -> list[str]:
        """Get list of changed file paths in a PR."""
        files = []
        page = 1
        while True:
            resp = self.client.get(
                f"/repos/{self.repo}/pulls/{pr_number}/files",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            files.extend(f["filename"] for f in batch)
            page += 1
        return files

    def get_pr_diff(self, pr_number: int) -> str:
        """Get PR diff as text."""
        resp = self.client.get(
            f"/repos/{self.repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        resp.raise_for_status()
        return resp.text

    def get_repo_info(self) -> dict:
        """Get repository info (for visibility check)."""
        resp = self.client.get(f"/repos/{self.repo}")
        resp.raise_for_status()
        return resp.json()

    def post_review(self, pr_number: int, body: str,
                    comments: list[dict] | None = None):
        """Post a PR review with optional inline comments."""
        payload = {
            "body": body,
            "event": "COMMENT",
        }
        if comments:
            payload["comments"] = comments
        resp = self.client.post(
            f"/repos/{self.repo}/pulls/{pr_number}/reviews",
            json=payload,
        )
        if resp.status_code >= 400:
            log.warning("Failed to post review: %s %s", resp.status_code, resp.text)
            # Fallback: post as issue comment
            self.post_comment(pr_number, body)
        return resp

    def post_comment(self, pr_number: int, body: str) -> dict | None:
        """Post or update a PR comment (identified by marker)."""
        marker = "<!-- delta-lint-comment -->"
        body_with_marker = f"{marker}\n{body}"

        # Check for existing comment
        resp = self.client.get(
            f"/repos/{self.repo}/issues/{pr_number}/comments",
            params={"per_page": 100},
        )
        existing_id = None
        if resp.status_code == 200:
            for c in resp.json():
                if c.get("body", "").startswith(marker):
                    existing_id = c["id"]
                    break

        if existing_id:
            resp = self.client.patch(
                f"/repos/{self.repo}/issues/comments/{existing_id}",
                json={"body": body_with_marker},
            )
        else:
            resp = self.client.post(
                f"/repos/{self.repo}/issues/{pr_number}/comments",
                json={"body": body_with_marker},
            )
        if resp.status_code < 300:
            return resp.json()
        log.warning("Failed to post comment: %s", resp.text)
        return None

    def post_check_run(self, head_sha: str, title: str, summary: str,
                       conclusion: str, annotations: list[dict]):
        """Create a Check Run with annotations."""
        batch_size = 50
        payload = {
            "name": "delta-lint",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": title,
                "summary": summary,
                "annotations": annotations[:batch_size],
            },
        }
        resp = self.client.post(
            f"/repos/{self.repo}/check-runs",
            json=payload,
        )
        if resp.status_code >= 400:
            log.warning("Failed to create check run: %s", resp.text)
            return

        # Post remaining annotations in batches
        if len(annotations) > batch_size:
            check_run_id = resp.json().get("id")
            for i in range(batch_size, len(annotations), batch_size):
                batch = annotations[i:i + batch_size]
                self.client.patch(
                    f"/repos/{self.repo}/check-runs/{check_run_id}",
                    json={
                        "output": {
                            "title": title,
                            "summary": summary,
                            "annotations": batch,
                        },
                    },
                )

    def add_reaction(self, comment_id: int, reaction: str = "eyes"):
        """Add a reaction to a comment (best-effort)."""
        try:
            self.client.post(
                f"/repos/{self.repo}/issues/comments/{comment_id}/reactions",
                json={"content": reaction},
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Billing / OSS check
# ---------------------------------------------------------------------------

def is_oss_repo(gh: GitHubClient) -> bool:
    """Check if the repository is public (OSS = free)."""
    try:
        info = gh.get_repo_info()
        return info.get("visibility") == "public" or not info.get("private", True)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Config loading (zero-config with optional override)
# ---------------------------------------------------------------------------

def load_repo_config(repo_path: str) -> dict:
    """Load .delta-lint.yml from repo root, or return defaults."""
    config_path = Path(repo_path) / ".delta-lint.yml"
    if config_path.exists():
        try:
            import yaml
            return yaml.safe_load(config_path.read_text()) or {}
        except Exception:
            pass
    return {}


def get_config(repo_path: str) -> dict:
    """Merge repo config with defaults."""
    repo_config = load_repo_config(repo_path)
    return {
        "severity": repo_config.get("severity", DEFAULT_SEVERITY),
        "model": repo_config.get("model", DEFAULT_MODEL),
        "max_diff_files": repo_config.get("max_diff_files", DEFAULT_MAX_DIFF_FILES),
        "scope": repo_config.get("scope", DEFAULT_SCOPE),
        "lens": repo_config.get("lens", DEFAULT_LENS),
        "fail_severity": repo_config.get("fail_severity", "none"),
    }


# ---------------------------------------------------------------------------
# Core: clone + scan + post results
# ---------------------------------------------------------------------------

def clone_repo(clone_url: str, ref: str, token: str) -> str:
    """Shallow clone a repo to a temp directory. Returns the path."""
    tmpdir = tempfile.mkdtemp(prefix="delta-lint-")
    # Inject token into clone URL for private repos
    authed_url = clone_url.replace("https://", f"https://x-access-token:{token}@")
    subprocess.run(
        ["git", "clone", "--depth", "50", "--branch", ref, authed_url, tmpdir],
        capture_output=True, text=True, timeout=120,
        check=True,
    )
    return tmpdir


def filter_scannable(files: list[str]) -> list[str]:
    """Filter to source files that delta-lint can scan."""
    from retrieval import filter_source_files
    return filter_source_files(files)


def run_scan(repo_path: str, files: list[str], config: dict,
             diff_text: str = ""):
    """Run scanner.scan() and return ScanResult."""
    from scanner import scan as engine_scan

    # Use CLI backend (claude -p) to avoid API key costs.
    # Falls back to api if CLI is unavailable.
    backend = "cli" if not os.environ.get("ANTHROPIC_API_KEY") else "api"

    return engine_scan(
        repo_path, files,
        model=config["model"],
        backend=backend,
        severity=config["severity"],
        scope=config["scope"],
        lens=config["lens"],
        no_cache=True,
        on_finding=None,
        diff_text=diff_text,
    )


def build_inline_comments(findings: list[dict]) -> list[dict]:
    """Build PR review inline comments from findings.

    Each finding becomes a review comment on the relevant line in the diff.
    Includes fix direction as text (Step 1 — no code generation).
    """
    comments = []
    for f in findings:
        if f.get("parse_error"):
            continue

        loc = f.get("location", {})
        file_a = loc.get("file_a", "")
        detail_a = loc.get("detail_a", "")

        # Extract line number from detail (e.g., "config.py:42")
        line = _extract_line(detail_a, file_a)
        if not file_a or not line:
            continue

        pattern = f.get("pattern", "?")
        severity = f.get("severity", "medium")
        contradiction = f.get("contradiction", "")
        impact = f.get("impact", "")
        fix_direction = f.get("fix_direction", "") or f.get("recommendation", "")

        sev_icon = {"high": ":red_circle:", "medium": ":orange_circle:",
                     "low": ":white_circle:"}.get(severity.lower(), ":white_circle:")

        body = f"{sev_icon} **delta-lint** — Pattern {pattern} ({severity})\n\n"
        body += f"**Contradiction:** {contradiction}\n\n"
        if impact:
            body += f"**Impact:** {impact}\n\n"
        if fix_direction:
            body += f"**Fix direction:** {fix_direction}\n"

        comments.append({
            "path": file_a,
            "line": line,
            "body": body,
        })

        # Also comment on file_b if different
        file_b = loc.get("file_b", "")
        detail_b = loc.get("detail_b", "")
        if file_b and file_b != file_a:
            line_b = _extract_line(detail_b, file_b)
            if line_b:
                ref_body = (
                    f"{sev_icon} **delta-lint** — Pattern {pattern} ({severity})\n\n"
                    f"Related to `{file_a}:{line}` — see main comment there.\n\n"
                    f"**Contradiction:** {contradiction}\n"
                )
                comments.append({
                    "path": file_b,
                    "line": line_b,
                    "body": ref_body,
                })

    return comments


def _extract_line(detail: str, filepath: str) -> int | None:
    """Extract line number from finding detail string."""
    import re
    if not detail:
        return None
    # Try "file:line" format
    m = re.search(r":(\d+)", detail)
    if m:
        return int(m.group(1))
    # Try "line N" or "L42" format
    m = re.search(r"(?:line\s*|L)(\d+)", detail, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


async def handle_pr_event(payload: dict, installation_id: int):
    """Handle pull_request opened/synchronize events."""
    try:
        await _handle_pr_event_inner(payload, installation_id)
    except Exception:
        log.exception("handle_pr_event failed")


async def _handle_pr_event_inner(payload: dict, installation_id: int):
    pr = payload["pull_request"]
    pr_number = pr["number"]
    repo_full = payload["repository"]["full_name"]
    clone_url = payload["repository"]["clone_url"]
    head_ref = pr["head"]["ref"]
    head_sha = pr["head"]["sha"]

    log.info("PR #%d on %s — scanning...", pr_number, repo_full)

    # 1. Get installation token
    token = get_installation_token(installation_id)
    gh = GitHubClient(token, repo_full)

    # 2. Get changed files
    changed_files = gh.get_pr_files(pr_number)
    source_files = filter_scannable(changed_files)

    if not source_files:
        log.info("PR #%d — no scannable files", pr_number)
        return

    # 3. Clone repo
    repo_path = clone_repo(clone_url, head_ref, token)

    try:
        # 4. Load config (zero-config or .delta-lint.yml)
        config = get_config(repo_path)

        if len(changed_files) > config["max_diff_files"]:
            log.info("PR #%d — %d files exceeds limit (%d), skipping",
                     pr_number, len(changed_files), config["max_diff_files"])
            return

        # 5. Get diff
        diff_text = gh.get_pr_diff(pr_number)

        # 6. Run scan
        scan_result = run_scan(repo_path, source_files, config, diff_text)
        findings = scan_result.shown

        log.info("PR #%d — %d finding(s)", pr_number, len(findings))

        # 7. Post results
        from output_formats import format_pr_markdown, format_annotations

        # Summary comment
        summary = format_pr_markdown(scan_result, repo_name=repo_full)
        gh.post_comment(pr_number, summary)

        # Inline review comments (one per finding on the diff line)
        if findings:
            inline_comments = build_inline_comments(findings)
            if inline_comments:
                review_body = (
                    f"delta-lint found {len(findings)} structural contradiction(s). "
                    "See inline comments below."
                )
                gh.post_review(pr_number, review_body, inline_comments)

        # Check Run annotations
        annotations = format_annotations(scan_result)
        n = len(findings)
        conclusion = "success" if n == 0 else "neutral"
        title = f"delta-lint: {n} finding(s)" if n else "delta-lint: clean"
        ann_summary = (f"{n} structural contradiction(s) detected." if n
                       else "No contradictions found.")
        gh.post_check_run(head_sha, title, ann_summary, conclusion, annotations)

        # Severity-based status (for merge blocking)
        fail_sev = config["fail_severity"]
        if fail_sev != "none":
            sev_order = {"high": 1, "medium": 2, "low": 3}
            threshold = sev_order.get(fail_sev, 0)
            blocking = [f for f in findings
                        if sev_order.get(
                            f.get("severity", "low").lower(), 3) <= threshold]
            if blocking:
                conclusion = "failure"
                gh.post_check_run(
                    head_sha,
                    f"delta-lint: {len(blocking)} blocking finding(s)",
                    f"{len(blocking)} finding(s) at {fail_sev}+ severity",
                    conclusion, annotations,
                )

    finally:
        # Cleanup temp clone
        import shutil
        shutil.rmtree(repo_path, ignore_errors=True)


async def handle_comment_event(payload: dict, installation_id: int):
    """Handle issue_comment events (slash commands like /delta-scan)."""
    comment_body = payload.get("comment", {}).get("body", "")

    # Only respond to delta-lint commands
    if not any(cmd in comment_body.lower()
               for cmd in ["/delta-scan", "/delta-review", "/delta-lint"]):
        return

    issue = payload.get("issue", {})
    if not issue.get("pull_request"):
        return  # Not a PR comment

    pr_number = issue["number"]
    repo_full = payload["repository"]["full_name"]
    comment_id = payload["comment"]["id"]

    log.info("Command in PR #%d on %s: %s", pr_number, repo_full,
             comment_body.split("\n")[0][:80])

    token = get_installation_token(installation_id)
    gh = GitHubClient(token, repo_full)

    # React to show we're working
    gh.add_reaction(comment_id, "eyes")

    # Get PR details
    resp = gh.client.get(f"/repos/{repo_full}/pulls/{pr_number}")
    resp.raise_for_status()
    pr = resp.json()

    # Construct a minimal PR payload and delegate
    pr_payload = {
        "pull_request": pr,
        "repository": payload["repository"],
    }
    await handle_pr_event(pr_payload, installation_id)

    # React with checkmark when done
    gh.add_reaction(comment_id, "rocket")


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

def verify_signature(payload_body: bytes, signature: str) -> bool:
    """Verify GitHub webhook signature (HMAC-SHA256)."""
    if not GITHUB_WEBHOOK_SECRET:
        return True  # Skip verification in dev mode
    if not signature:
        log.warning("No signature provided — skipping verification (dev/smee mode)")
        return True  # smee.io does not forward signatures
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header("", alias="X-Hub-Signature-256"),
):
    body = await request.body()

    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    installation_id = payload.get("installation", {}).get("id")

    log.info("Webhook received: event=%s, action=%s, installation=%s",
             x_github_event, payload.get("action"), installation_id)

    if not installation_id:
        return JSONResponse({"status": "ignored", "reason": "no installation"})

    action = payload.get("action", "")

    if x_github_event == "pull_request" and action in ("opened", "synchronize"):
        # Run scan in background (don't block webhook response)
        import asyncio
        asyncio.create_task(handle_pr_event(payload, installation_id))
        return JSONResponse({"status": "scanning"})

    if x_github_event == "issue_comment" and action == "created":
        import asyncio
        asyncio.create_task(handle_comment_event(payload, installation_id))
        return JSONResponse({"status": "processing"})

    return JSONResponse({"status": "ignored", "event": x_github_event})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "delta-lint"}


# ---------------------------------------------------------------------------
# Dev server
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webhook:app", host="0.0.0.0", port=3000, reload=True)
