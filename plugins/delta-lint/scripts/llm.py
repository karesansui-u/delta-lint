"""LLM backend abstraction — single source of truth for all LLM calls.

All detection/verification/fix-generation calls go through call_llm().
Backends: ClaudeCLI ($0, subscription) → AnthropicAPI (SDK) → HTTPFallback (requests).
         CodexCLI (OpenAI Codex CLI, per-user opt-in via ~/.delta-lint/config.json).

Thread-safe: safe to call from ThreadPoolExecutor (deep_verifier).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from typing import Protocol


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_TIMEOUT = 600  # seconds


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------

class LLMBackend(Protocol):
    """Any LLM backend must implement this."""

    def complete(self, system: str, user: str, model: str, timeout: int,
                 temperature: float = 0.0) -> str: ...


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------

class ClaudeCLI:
    """claude -p (subscription CLI, $0 cost). Default for local use."""

    def complete(self, system: str, user: str, model: str, timeout: int,
                 temperature: float = 0.0) -> str:
        prompt = system + "\n\n" + user
        result = subprocess.run(
            ["claude", "-p"],
            input=prompt,
            capture_output=True, text=True, timeout=timeout,
        )
        # Hook failures (e.g. SessionEnd) cause non-zero exit even when output is valid
        if result.stdout.strip():
            return result.stdout
        if result.returncode != 0:
            raise RuntimeError(f"claude -p failed: {result.stderr[:300]}")
        return result.stdout


class CodexCLI:
    """codex exec (OpenAI Codex CLI, per-user opt-in). Reads model from ~/.codex/config.toml."""

    def complete(self, system: str, user: str, model: str, timeout: int,
                 temperature: float = 0.0) -> str:
        prompt = system + "\n\n" + user
        with tempfile.NamedTemporaryFile(mode="r", suffix=".txt", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                ["codex", "exec", "--sandbox", "read-only",
                 "--output-last-message", tmp_path, "-"],
                input=prompt,
                capture_output=True, text=True, timeout=timeout,
            )
            with open(tmp_path) as f:
                response = f.read()
            if response.strip():
                return response
            if result.returncode != 0:
                raise RuntimeError(f"codex exec failed: {result.stderr[:300]}")
            return response
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class AnthropicAPI:
    """Anthropic SDK (API key required). Default for CI."""

    def complete(self, system: str, user: str, model: str, timeout: int,
                 temperature: float = 0.0) -> str:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            )

        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
        client = (
            anthropic.Anthropic(api_key=api_key, timeout=float(timeout))
            if api_key
            else anthropic.Anthropic(timeout=float(timeout))
        )
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text


class HTTPFallback:
    """Raw HTTP via requests (fallback if SDK not installed)."""

    def complete(self, system: str, user: str, model: str, timeout: int,
                 temperature: float = 0.0) -> str:
        try:
            import requests as req_lib
        except ImportError:
            raise RuntimeError(
                "Neither anthropic SDK nor requests installed. "
                "Install one: pip install anthropic"
            )

        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY or CLAUDE_API_KEY not set")

        resp = req_lib.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 4096,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=timeout,
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Claude API error {resp.status_code}: {resp.text[:300]}")

        return resp.json()["content"][0]["text"]


# ---------------------------------------------------------------------------
# CLI availability check
# ---------------------------------------------------------------------------

def _cli_available() -> bool:
    """Check if claude CLI is available on PATH."""
    from cli_utils import cli_available
    return cli_available()


def _codex_cli_available() -> bool:
    """Check if codex CLI is available on PATH."""
    import shutil
    return bool(shutil.which("codex"))


def _sdk_available() -> bool:
    """Check if anthropic SDK is importable."""
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _requests_available() -> bool:
    """Check if requests library is importable."""
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def get_backend(preference: str = "auto") -> LLMBackend:
    """Select LLM backend.

    preference:
      "auto"      — CLI available → ClaudeCLI, else AnthropicAPI, else HTTPFallback
      "cli"       — ClaudeCLI (fail if unavailable)
      "api"       — AnthropicAPI (require SDK or fall back to HTTP)
      "codex-cli" — CodexCLI (fail if codex CLI unavailable)
    """
    if preference == "codex-cli":
        if not _codex_cli_available():
            raise RuntimeError(
                "codex CLI not available on PATH. "
                "Install: npm install -g @openai/codex"
            )
        return CodexCLI()

    if preference == "cli":
        if not _cli_available():
            raise RuntimeError("claude CLI not available on PATH")
        return ClaudeCLI()

    if preference == "api":
        if _sdk_available():
            return AnthropicAPI()
        if _requests_available():
            return HTTPFallback()
        raise RuntimeError(
            "No API backend available. Install: pip install anthropic"
        )

    # auto: CLI → SDK → HTTP
    if _cli_available():
        return ClaudeCLI()
    if _sdk_available():
        return AnthropicAPI()
    if _requests_available():
        return HTTPFallback()

    raise RuntimeError(
        "No LLM backend available. Install 'anthropic' package or "
        "ensure 'claude' CLI is on PATH."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def call_llm(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_MODEL,
    backend: str = "auto",
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 0,
    temperature: float = 0.0,
) -> str:
    """Call LLM. Single entry point for all delta-lint LLM calls.

    Args:
        system: System prompt.
        user: User prompt.
        model: Model identifier.
        backend: "auto" (CLI→API), "cli", or "api".
        timeout: Seconds per attempt.
        retries: Retry count on failure (exponential backoff: 1s, 2s, 4s...).
                 detector uses retries=2. Others default to 0.
        temperature: Sampling temperature (0.0 = deterministic).

    Returns:
        LLM response text.

    Raises:
        RuntimeError: If all backends/retries exhausted.
    """
    be = get_backend(backend)

    for attempt in range(retries + 1):
        try:
            return be.complete(system, user, model, timeout, temperature)
        except Exception as exc:
            if attempt < retries:
                wait = 2 ** attempt  # 1s, 2s, 4s...
                print(
                    f"  [retry] LLM call failed ({exc}), "
                    f"retrying in {wait}s... ({attempt + 1}/{retries})",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                raise
