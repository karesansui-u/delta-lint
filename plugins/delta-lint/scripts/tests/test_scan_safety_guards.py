from pathlib import Path
from types import SimpleNamespace

import pytest

import llm


def test_claude_cli_auth_failure_stdout_is_not_returned(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            stdout="Not logged in · Please run /login\n",
            stderr="",
            returncode=1,
        )

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="authentication failed"):
        llm.ClaudeCLI().complete("system", "user", model="ignored", timeout=1)


def test_batched_scopes_propagate_dry_run_to_child_scans():
    source = (Path(__file__).resolve().parents[1] / "cmd_scan.py").read_text(encoding="utf-8")

    # PR, wide, smart, and oversized diff scans all shell out to child scans.
    # Each path must pass --dry-run through, otherwise a parent dry-run can make
    # real LLM calls.
    assert source.count('cmd.append("--dry-run")') >= 4
