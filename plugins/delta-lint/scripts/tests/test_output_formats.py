"""output_formats.py ユニットテスト.

ScanResult モックデータで 4 フォーマッタの出力を検証する。
LLM 不要・$0 で実行可能。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from scanner import ScanResult
from output_formats import (
    format_ci_json,
    format_pr_markdown,
    format_annotations,
    format_sarif,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_finding(
    *,
    pattern="①",
    severity="high",
    file_a="src/server.ts",
    file_b="src/client.ts",
    detail_a="line ~42: sets timeout=30",
    detail_b="line ~10: expects timeout=60",
    contradiction="Server timeout 30s but client expects 60s",
    impact="Requests will time out prematurely",
):
    return {
        "pattern": pattern,
        "severity": severity,
        "location": {
            "file_a": file_a,
            "file_b": file_b,
            "detail_a": detail_a,
            "detail_b": detail_b,
        },
        "contradiction": contradiction,
        "user_impact": impact,
    }


@pytest.fixture
def result_with_findings():
    """3 findings: high, medium, low."""
    return ScanResult(
        shown=[
            _make_finding(pattern="①", severity="high"),
            _make_finding(
                pattern="②",
                severity="medium",
                file_a="lib/auth.py",
                file_b="lib/session.py",
                detail_a="line ~5: uses bcrypt",
                detail_b="line ~20: expects argon2",
                contradiction="Hash algorithm mismatch",
                impact="Auth will fail",
            ),
            _make_finding(
                pattern="⑥",
                severity="low",
                file_a="config/init.yaml",
                file_b="",
                detail_a="line ~1: order=before_db",
                detail_b="",
                contradiction="Init runs before DB is ready",
                impact="Startup race condition",
            ),
        ],
        filtered=[_make_finding(severity="low", pattern="③")],
        suppressed=[_make_finding(severity="medium", pattern="④")],
        expired=[],
        raw_count=5,
        verification_meta={"confirmed": 3, "rejected": 2},
        cache_hit=False,
    )


@pytest.fixture
def result_empty():
    """No findings at all."""
    return ScanResult()


@pytest.fixture
def result_clean_with_filtered():
    """No shown findings, but some filtered/suppressed."""
    return ScanResult(
        shown=[],
        filtered=[_make_finding(severity="low")],
        suppressed=[_make_finding(severity="medium")],
    )


# ---------------------------------------------------------------------------
# format_ci_json
# ---------------------------------------------------------------------------

class TestFormatCiJson:
    def test_valid_json(self, result_with_findings):
        raw = format_ci_json(result_with_findings)
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_summary_counts(self, result_with_findings):
        data = json.loads(format_ci_json(result_with_findings))
        s = data["summary"]
        assert s["shown"] == 3
        assert s["filtered"] == 1
        assert s["suppressed"] == 1
        assert s["raw_count"] == 5
        assert s["cache_hit"] is False

    def test_verification_meta_present(self, result_with_findings):
        data = json.loads(format_ci_json(result_with_findings))
        assert data["summary"]["verification"] == {"confirmed": 3, "rejected": 2}

    def test_verification_meta_absent(self, result_empty):
        data = json.loads(format_ci_json(result_empty))
        assert "verification" not in data["summary"]

    def test_findings_match_shown(self, result_with_findings):
        data = json.loads(format_ci_json(result_with_findings))
        assert len(data["findings"]) == 3

    def test_empty_result(self, result_empty):
        data = json.loads(format_ci_json(result_empty))
        assert data["findings"] == []
        assert data["summary"]["shown"] == 0


# ---------------------------------------------------------------------------
# format_pr_markdown
# ---------------------------------------------------------------------------

class TestFormatPrMarkdown:
    def test_has_heading_with_count(self, result_with_findings):
        md = format_pr_markdown(result_with_findings)
        assert "## delta-lint: 3 finding(s)" in md

    def test_severity_table(self, result_with_findings):
        md = format_pr_markdown(result_with_findings)
        assert ":red_circle:" in md
        assert ":orange_circle:" in md
        assert ":white_circle:" in md

    def test_collapsible_details(self, result_with_findings):
        md = format_pr_markdown(result_with_findings)
        assert md.count("<details>") == 3
        assert md.count("</details>") == 3

    def test_contradiction_in_body(self, result_with_findings):
        md = format_pr_markdown(result_with_findings)
        assert "Server timeout 30s" in md

    def test_footer_filtered_suppressed(self, result_with_findings):
        md = format_pr_markdown(result_with_findings)
        assert "1 lower-severity filtered" in md
        assert "1 suppressed" in md

    def test_clean_result(self, result_empty):
        md = format_pr_markdown(result_empty)
        assert "No structural contradictions detected" in md

    def test_clean_with_filtered(self, result_clean_with_filtered):
        md = format_pr_markdown(result_clean_with_filtered)
        assert "No structural contradictions detected" in md
        assert "1 lower-severity filtered" in md
        assert "1 suppressed" in md

    def test_file_b_shown_when_present(self, result_with_findings):
        md = format_pr_markdown(result_with_findings)
        assert "src/client.ts" in md

    def test_no_file_b_when_empty(self, result_with_findings):
        """Finding ⑥ has no file_b — should not show ↔."""
        md = format_pr_markdown(result_with_findings)
        lines = [l for l in md.split("\n") if "config/init.yaml" in l]
        for line in lines:
            if "<summary>" in line:
                assert "↔" not in line


# ---------------------------------------------------------------------------
# format_annotations
# ---------------------------------------------------------------------------

class TestFormatAnnotations:
    def test_returns_list(self, result_with_findings):
        anns = format_annotations(result_with_findings)
        assert isinstance(anns, list)

    def test_annotation_count(self, result_with_findings):
        """3 findings: 2 have file_b → 5 annotations total."""
        anns = format_annotations(result_with_findings)
        assert len(anns) == 5  # 3 file_a + 2 file_b

    def test_annotation_fields(self, result_with_findings):
        anns = format_annotations(result_with_findings)
        required = {"path", "start_line", "end_line", "annotation_level", "message", "title"}
        for a in anns:
            assert required.issubset(a.keys()), f"Missing keys: {required - a.keys()}"

    def test_severity_mapping(self, result_with_findings):
        anns = format_annotations(result_with_findings)
        levels = {a["annotation_level"] for a in anns}
        assert levels == {"failure", "warning", "notice"}

    def test_line_extraction(self, result_with_findings):
        anns = format_annotations(result_with_findings)
        first = anns[0]
        assert first["path"] == "src/server.ts"
        assert first["start_line"] == 42

    def test_empty_result(self, result_empty):
        assert format_annotations(result_empty) == []

    def test_no_file_a_skipped(self):
        """Finding with empty file_a should be skipped."""
        result = ScanResult(shown=[{
            "pattern": "①",
            "severity": "high",
            "location": {"file_a": "", "file_b": "b.py"},
            "contradiction": "test",
        }])
        assert format_annotations(result) == []


# ---------------------------------------------------------------------------
# format_sarif
# ---------------------------------------------------------------------------

class TestFormatSarif:
    def test_valid_json(self, result_with_findings):
        raw = format_sarif(result_with_findings)
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_sarif_version(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        assert data["version"] == "2.1.0"

    def test_schema_present(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        assert "$schema" in data

    def test_tool_driver(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        driver = data["runs"][0]["tool"]["driver"]
        assert driver["name"] == "delta-lint"
        assert "version" in driver

    def test_rules_include_known_patterns(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        rules = data["runs"][0]["tool"]["driver"]["rules"]
        rule_ids = {r["id"] for r in rules}
        assert {"①", "②", "③", "④", "⑤", "⑥"}.issubset(rule_ids)

    def test_results_count(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        results = data["runs"][0]["results"]
        assert len(results) == 3

    def test_severity_to_level_mapping(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        results = data["runs"][0]["results"]
        levels = {r["level"] for r in results}
        assert levels == {"error", "warning", "note"}

    def test_rule_id_matches_pattern(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        results = data["runs"][0]["results"]
        assert results[0]["ruleId"] == "①"
        assert results[1]["ruleId"] == "②"
        assert results[2]["ruleId"] == "⑥"

    def test_rule_index_valid(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        rules = data["runs"][0]["tool"]["driver"]["rules"]
        for r in data["runs"][0]["results"]:
            idx = r["ruleIndex"]
            assert 0 <= idx < len(rules)
            assert rules[idx]["id"] == r["ruleId"]

    def test_location_artifact_uri(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        loc = data["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "src/server.ts"
        assert loc["region"]["startLine"] == 42

    def test_related_location(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        first = data["runs"][0]["results"][0]
        assert "relatedLocations" in first
        related = first["relatedLocations"][0]
        assert related["physicalLocation"]["artifactLocation"]["uri"] == "src/client.ts"

    def test_no_related_location_when_no_file_b(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        third = data["runs"][0]["results"][2]  # ⑥ has no file_b
        assert "relatedLocations" not in third

    def test_unknown_pattern_added_as_rule(self):
        """Pattern not in ①-⑥ should be dynamically added."""
        result = ScanResult(shown=[_make_finding(pattern="⑦ NewPattern")])
        data = json.loads(format_sarif(result))
        rules = data["runs"][0]["tool"]["driver"]["rules"]
        rule_ids = {r["id"] for r in rules}
        assert "⑦ NewPattern" in rule_ids

    def test_empty_result(self, result_empty):
        data = json.loads(format_sarif(result_empty))
        assert data["runs"][0]["results"] == []
        # 6 base rules still present
        assert len(data["runs"][0]["tool"]["driver"]["rules"]) == 6

    def test_message_includes_impact(self, result_with_findings):
        data = json.loads(format_sarif(result_with_findings))
        msg = data["runs"][0]["results"][0]["message"]["text"]
        assert "Impact:" in msg
        assert "time out prematurely" in msg
