"""output_formats.py — CI/CD output formatters for ScanResult.

Supplements output.py (which handles CLI text/markdown/json).
These formatters produce machine-consumable output for integrations.

Design: architecture-integration.md §3.3
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scanner import ScanResult


def format_ci_json(result: "ScanResult") -> str:
    """Structured JSON for CI pipelines (GitHub Actions, etc.).

    Includes metadata (counts, cache_hit, verification) alongside findings.
    """
    data = {
        "findings": result.shown,
        "summary": {
            "shown": len(result.shown),
            "filtered": len(result.filtered),
            "suppressed": len(result.suppressed),
            "expired": len(result.expired),
            "raw_count": result.raw_count,
            "cache_hit": result.cache_hit,
        },
    }
    if result.verification_meta:
        data["summary"]["verification"] = result.verification_meta
    return json.dumps(data, indent=2, ensure_ascii=False)


def format_pr_markdown(result: "ScanResult", repo_name: str = "") -> str:
    """Markdown for GitHub PR comments.

    Compact format optimised for PR review context:
    - Summary table at top
    - Collapsible details per finding
    - Severity badges
    """
    shown = result.shown
    if not shown:
        parts = []
        if result.filtered:
            parts.append(f"{len(result.filtered)} lower-severity filtered")
        if result.suppressed:
            parts.append(f"{len(result.suppressed)} suppressed")
        suffix = f" ({', '.join(parts)})" if parts else ""
        return f"**delta-lint**: No structural contradictions detected.{suffix}\n"

    severity_icon = {"high": ":red_circle:", "medium": ":orange_circle:", "low": ":white_circle:"}
    high = sum(1 for f in shown if f.get("severity", "").lower() == "high")
    medium = sum(1 for f in shown if f.get("severity", "").lower() == "medium")
    low = len(shown) - high - medium

    lines = [
        f"## delta-lint: {len(shown)} finding(s)",
        "",
        "| Severity | Count |",
        "|----------|-------|",
    ]
    if high:
        lines.append(f"| :red_circle: High | {high} |")
    if medium:
        lines.append(f"| :orange_circle: Medium | {medium} |")
    if low:
        lines.append(f"| :white_circle: Low | {low} |")
    lines.append("")

    for i, f in enumerate(shown, 1):
        sev = f.get("severity", "low").lower()
        icon = severity_icon.get(sev, ":white_circle:")
        pattern = f.get("pattern", "?")
        loc = f.get("location", {})
        file_a = loc.get("file_a", "?")
        file_b = loc.get("file_b", "")
        contradiction = f.get("contradiction", "")
        impact = f.get("user_impact") or f.get("impact", "")

        title = f"{icon} **{pattern}** ({sev}) — `{file_a}`"
        if file_b:
            title += f" ↔ `{file_b}`"

        lines.append(f"<details><summary>{title}</summary>")
        lines.append("")
        if contradiction:
            lines.append(f"**Contradiction**: {contradiction}")
            lines.append("")
        if impact:
            lines.append(f"**Impact**: {impact}")
            lines.append("")
        if loc.get("detail_a"):
            lines.append(f"- `{file_a}`: {loc['detail_a']}")
        if loc.get("detail_b") and file_b:
            lines.append(f"- `{file_b}`: {loc['detail_b']}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Footer
    footer = []
    if result.filtered:
        footer.append(f"{len(result.filtered)} lower-severity filtered")
    if result.suppressed:
        footer.append(f"{len(result.suppressed)} suppressed")
    if result.cache_hit:
        footer.append("cached result")
    if footer:
        lines.append(f"---\n*{', '.join(footer)}*")

    return "\n".join(lines) + "\n"


def format_annotations(result: "ScanResult") -> list[dict]:
    """GitHub Check Run annotations format.

    Returns a list of annotation dicts compatible with the GitHub Checks API:
    https://docs.github.com/en/rest/checks/runs#create-a-check-run

    Each annotation has: path, start_line, end_line, annotation_level, message, title
    """
    annotations = []
    level_map = {"high": "failure", "medium": "warning", "low": "notice"}

    for f in result.shown:
        loc = f.get("location", {})
        file_a = loc.get("file_a", "")
        if not file_a:
            continue

        sev = f.get("severity", "low").lower()
        # Try to extract line number from detail
        line = 1
        detail_a = loc.get("detail_a", "")
        if detail_a:
            import re
            m = re.search(r"line\s*~?(\d+)", detail_a, re.IGNORECASE)
            if m:
                line = int(m.group(1))

        pattern = f.get("pattern", "?")
        contradiction = f.get("contradiction", "")
        impact = f.get("user_impact") or f.get("impact", "")
        message = contradiction
        if impact:
            message += f"\n\nImpact: {impact}"

        annotations.append({
            "path": file_a,
            "start_line": line,
            "end_line": line,
            "annotation_level": level_map.get(sev, "notice"),
            "message": message,
            "title": f"delta-lint: {pattern} ({sev})",
        })

        # Add annotation for file_b if present
        file_b = loc.get("file_b", "")
        if file_b:
            line_b = 1
            detail_b = loc.get("detail_b", "")
            if detail_b:
                import re
                m = re.search(r"line\s*~?(\d+)", detail_b, re.IGNORECASE)
                if m:
                    line_b = int(m.group(1))
            annotations.append({
                "path": file_b,
                "start_line": line_b,
                "end_line": line_b,
                "annotation_level": level_map.get(sev, "notice"),
                "message": f"Related: {contradiction}",
                "title": f"delta-lint: {pattern} ({sev}) — see {file_a}",
            })

    return annotations


# ---------------------------------------------------------------------------
# SARIF 2.1.0 (GitHub Code Scanning)
# ---------------------------------------------------------------------------

# Static rule definitions for the 6 contradiction patterns.
_SARIF_RULES: list[dict] = [
    {
        "id": "\u2460",
        "name": "AsymmetricDefaults",
        "shortDescription": {"text": "Asymmetric Defaults"},
        "helpUri": "https://github.com/karesansui-u/DeltaRegret",
    },
    {
        "id": "\u2461",
        "name": "SemanticMismatch",
        "shortDescription": {"text": "Semantic Mismatch"},
        "helpUri": "https://github.com/karesansui-u/DeltaRegret",
    },
    {
        "id": "\u2462",
        "name": "ExternalSpecDivergence",
        "shortDescription": {"text": "External Spec Divergence"},
        "helpUri": "https://github.com/karesansui-u/DeltaRegret",
    },
    {
        "id": "\u2463",
        "name": "GuardNonPropagation",
        "shortDescription": {"text": "Guard Non-Propagation"},
        "helpUri": "https://github.com/karesansui-u/DeltaRegret",
    },
    {
        "id": "\u2464",
        "name": "PairedSettingOverride",
        "shortDescription": {"text": "Paired-Setting Override"},
        "helpUri": "https://github.com/karesansui-u/DeltaRegret",
    },
    {
        "id": "\u2465",
        "name": "LifecycleOrdering",
        "shortDescription": {"text": "Lifecycle Ordering"},
        "helpUri": "https://github.com/karesansui-u/DeltaRegret",
    },
]

def format_sarif(result: "ScanResult", repo_name: str = "") -> str:
    """SARIF 2.1.0 JSON for GitHub Code Scanning integration.

    Produces a valid SARIF log that can be uploaded via
    github/codeql-action/upload-sarif@v3.
    """
    level_map = {"high": "error", "medium": "warning", "low": "note"}

    # Build rules list — start with the 6 known patterns, add unknowns
    rules = list(_SARIF_RULES)
    rule_index: dict[str, int] = {r["id"]: i for i, r in enumerate(rules)}

    sarif_results: list[dict] = []

    for f in result.shown:
        pattern = f.get("pattern", "?")
        sev = f.get("severity", "low").lower()
        loc = f.get("location", {})
        file_a = loc.get("file_a", "")
        file_b = loc.get("file_b", "")
        contradiction = f.get("contradiction", "")
        impact = f.get("user_impact") or f.get("impact", "")

        # Ensure rule exists
        if pattern not in rule_index:
            rule_index[pattern] = len(rules)
            rules.append({
                "id": pattern,
                "name": pattern,
                "shortDescription": {"text": pattern},
                "helpUri": "https://github.com/karesansui-u/DeltaRegret",
            })

        # Extract line number from detail
        line_a = 1
        detail_a = loc.get("detail_a", "")
        if detail_a:
            import re
            m = re.search(r"line\s*~?(\d+)", detail_a, re.IGNORECASE)
            if m:
                line_a = int(m.group(1))

        message_text = contradiction
        if impact:
            message_text += f"\n\nImpact: {impact}"

        sarif_result: dict = {
            "ruleId": pattern,
            "ruleIndex": rule_index[pattern],
            "level": level_map.get(sev, "note"),
            "message": {"text": message_text or "Structural contradiction detected"},
            "locations": [],
        }

        if file_a:
            sarif_result["locations"].append({
                "physicalLocation": {
                    "artifactLocation": {"uri": file_a, "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": line_a},
                },
            })

        # Related location (file_b)
        if file_b:
            line_b = 1
            detail_b = loc.get("detail_b", "")
            if detail_b:
                import re
                m = re.search(r"line\s*~?(\d+)", detail_b, re.IGNORECASE)
                if m:
                    line_b = int(m.group(1))
            sarif_result["relatedLocations"] = [{
                "id": 0,
                "physicalLocation": {
                    "artifactLocation": {"uri": file_b, "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": line_b},
                },
                "message": {"text": f"Related file: {file_b}"},
            }]

        sarif_results.append(sarif_result)

    sarif_log = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "delta-lint",
                    "informationUri": "https://github.com/karesansui-u/DeltaRegret",
                    "version": "0.1.0",
                    "rules": rules,
                },
            },
            "results": sarif_results,
        }],
    }

    return json.dumps(sarif_log, indent=2, ensure_ascii=False)
