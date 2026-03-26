"""scanner.py — Core detection pipeline (IO-free except LLM calls).

Extracted from cmd_scan.py to enable reuse from CLI, GitHub Actions, and skills.
All file identification, batching, persistence, and UI concerns stay in the caller.

Design: architecture-integration.md §3.2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class ScanResult:
    """Structured output from a single scan pass."""

    # Post-filter findings
    shown: list[dict] = field(default_factory=list)
    filtered: list[dict] = field(default_factory=list)
    suppressed: list[dict] = field(default_factory=list)
    expired: list[dict] = field(default_factory=list)
    expired_entries: list = field(default_factory=list)  # SuppressEntry objects

    # Metadata
    raw_count: int = 0
    verification_meta: Optional[dict] = None
    rejected_findings: list[dict] = field(default_factory=list)
    cache_hit: bool = False
    context: object = None  # retrieval.BuildResult


def scan(
    repo_path: str,
    source_files: list[str],
    *,
    model: str,
    backend: str = "cli",
    lang: str = "en",
    severity: str = "high",
    scope: str = "diff",
    depth: str = "default",
    lens: str = "default",
    diff_target: str = "HEAD",
    semantic: bool = False,
    no_verify: bool = False,
    no_cache: bool = False,
    verbose: bool = False,
    # Pre-loaded config (caller is responsible for loading/merging)
    constraints: Optional[list] = None,
    policy: Optional[dict] = None,
    config: Optional[dict] = None,
    retrieval_config: Optional[dict] = None,
    doc_files: Optional[list[str]] = None,
    diff_text: str = "",
    on_finding: Optional[Callable[[dict], None]] = None,
) -> ScanResult:
    """Run the core detection pipeline on a set of source files.

    This function is pure pipeline logic: context → detect → verify → filter.
    IO concerns (JSONL persistence, dashboard, subprocess batching) are the
    caller's responsibility.

    Args:
        on_finding: Called for each finding that passes all filters.
            Local CLI passes a function that saves to JSONL immediately
            (crash resilience). CI/Action passes None (batch processing).
    """
    import sys
    from retrieval import build_context
    from detector import detect, load_constraints as _load_constraints, load_policy as _load_policy
    from output import filter_findings, filter_diff_only, SEVERITY_ORDER
    from suppress import load_suppressions
    from cache import compute_context_hash, get_cached_findings, save_cached_findings

    repo_name = Path(repo_path).name
    result = ScanResult()

    # --- Step 1: Build context ---
    _hops = 3 if (depth == "deep" or scope == "pr") else 1
    context = build_context(
        repo_path, source_files,
        retrieval_config=retrieval_config,
        doc_files=doc_files,
        max_hops=_hops,
    )
    result.context = context

    if not context.target_files:
        if verbose:
            print("No readable source files in context.", file=sys.stderr)
        return result

    if verbose:
        print(f"  Target files: {len(context.target_files)}", file=sys.stderr)
        print(f"  Dependency files: {len(context.dep_files)}", file=sys.stderr)
        if context.doc_files:
            print(f"  Document files: {len(context.doc_files)}", file=sys.stderr)
        print(f"  Total context: {context.total_chars} chars", file=sys.stderr)
        for w in context.warnings:
            print(f"  WARNING: {w}", file=sys.stderr)

    # --- Step 2: Semantic expansion ---
    if semantic:
        from semantic import expand_context_semantic
        context = expand_context_semantic(
            repo_path, source_files, context,
            diff_target=diff_target,
            verbose=verbose,
        )
        result.context = context

    # --- Step 3: Cache check ---
    context_hash = compute_context_hash(
        context.target_files, context.dep_files, context.doc_files,
    )
    findings = []

    if not no_cache:
        cached = get_cached_findings(repo_path, context_hash)
        if cached is not None:
            findings = cached
            result.cache_hit = True
            if verbose:
                print(f"  Cache hit ({context_hash[:8]}...) — {len(findings)} finding(s)",
                      file=sys.stderr)

    # --- Step 4: Detection ---
    if not result.cache_hit:
        if verbose:
            print(f"Running detection with {model}...", file=sys.stderr)

        # Load constraints/policy if not provided by caller
        if constraints is None:
            target_paths = [f.path for f in context.target_files]
            constraints = _load_constraints(repo_path, target_paths)
        if policy is None:
            policy = _load_policy(repo_path) or {}

        architecture = policy.get("architecture")
        project_rules = policy.get("project_rules")
        prompt_append = policy.get("prompt_append", "")
        disabled_patterns = (config or {}).get("disabled_patterns")

        # Custom detection prompt
        detect_prompt_override = ""
        raw_detect_prompt = policy.get("detect_prompt", "")
        if raw_detect_prompt:
            prompt_file = Path(repo_path) / raw_detect_prompt
            if prompt_file.exists() and prompt_file.is_file():
                detect_prompt_override = prompt_file.read_text(encoding="utf-8")
            else:
                detect_prompt_override = raw_detect_prompt

        # Get diff text if not provided
        if not diff_text:
            from retrieval import get_diff_content, get_pr_diff_content
            if scope == "pr":
                diff_text = get_pr_diff_content(repo_path, None)
            else:
                diff_text = get_diff_content(repo_path, diff_target)

        findings = detect(
            context, repo_name=repo_name, model=model,
            backend=backend, lang=lang,
            constraints=constraints or None,
            architecture=architecture,
            diff_text=diff_text,
            project_rules=project_rules,
            repo_path=repo_path,
            prompt_append=prompt_append,
            disabled_patterns=disabled_patterns,
            detect_prompt=detect_prompt_override,
            lens=lens,
        )
        result.raw_count = len(findings)

        if verbose:
            print(f"  Raw findings: {len(findings)}", file=sys.stderr)

    # --- Step 5: Verification ---
    if not no_verify and findings:
        if verbose:
            print(f"Verifying {len(findings)} finding(s)...", file=sys.stderr)
        from verifier import verify_findings as verify
        findings, rejected, verification_meta = verify(
            findings, context,
            model=model, backend=backend,
            verbose=verbose,
        )
        result.verification_meta = verification_meta
        result.rejected_findings = rejected
        if verbose and verification_meta:
            print(f"  Verified: {verification_meta['confirmed']} confirmed, "
                  f"{verification_meta['rejected']} rejected", file=sys.stderr)

    # --- Step 6: Cache save ---
    if not result.cache_hit and not no_cache:
        save_cached_findings(repo_path, context_hash, findings, model=model)

    # --- Step 7: Filter ---
    suppressions = load_suppressions(repo_path)
    filter_result = filter_findings(
        findings, min_severity=severity,
        suppressions=suppressions, repo_path=repo_path,
    )
    result.shown = filter_result.shown
    result.filtered = filter_result.filtered
    result.suppressed = filter_result.suppressed
    result.expired = filter_result.expired
    result.expired_entries = filter_result.expired_entries

    # --- Step 7.1: Policy filter ---
    if policy and (policy.get("accepted") or policy.get("severity_overrides")):
        from findings import apply_policy
        result.shown = apply_policy(result.shown, policy)

    # --- Step 7.2: Category severity boost ---
    categories = (config or {}).get("categories", {})
    if categories:
        from cli_utils import _apply_category_severity_boost
        _apply_category_severity_boost(result.shown, categories, verbose=verbose)
        threshold = SEVERITY_ORDER.get(severity, 0)
        result.shown = [
            f for f in result.shown
            if SEVERITY_ORDER.get(f.get("severity", "low").lower(), 1) <= threshold
        ]

    # --- Step 7.3: Diff-only filter ---
    if scope == "diff" and source_files:
        # Note: diff_only is opt-in from caller via source_files context.
        # The caller decides whether to apply diff-only filtering.
        pass

    # --- Step 8: on_finding callback ---
    if on_finding and result.shown:
        for f in result.shown:
            try:
                on_finding(f)
            except Exception:
                pass  # callback errors must not break the pipeline

    return result
