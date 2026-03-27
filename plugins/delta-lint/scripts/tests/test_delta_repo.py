"""
δ_repo / 健全性 (compute_delta_repo, health_barometer, i_base_lookup) のテスト.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from info_theory import compute_delta_repo, health_barometer, i_base_lookup


def _open_finding(pattern: str, severity: str = "medium", status: str = "found") -> dict:
    return {"pattern": pattern, "severity": severity, "status": status}


class TestIBaseLookup:
    def test_calibrated_cell(self):
        val, calibrated = i_base_lookup("②", "medium")
        assert val == pytest.approx(3.4012)
        assert calibrated is True

    def test_unknown_pattern_uses_fallback(self):
        val, calibrated = i_base_lookup("⑦", "high")
        assert val == pytest.approx(0.4055)
        assert calibrated is False


class TestComputeDeltaRepo:
    def test_empty_findings(self):
        r = compute_delta_repo([])
        assert r["delta_repo"] == 0.0
        assert r["health_factor"] == 1.0
        assert r["health_emoji"] == "🟢"
        assert r["health_label"] == "excellent"
        assert r["active_count"] == 0
        assert r["breakdown"] == {}

    def test_only_resolved_findings(self):
        findings = [
            _open_finding("①", "low", "merged"),
            _open_finding("②", "high", "false_positive"),
        ]
        r = compute_delta_repo(findings)
        assert r["delta_repo"] == 0.0
        assert r["active_count"] == 0

    def test_mock_four_findings_breakdown(self):
        """①×2 + ② medium + ③ — matches calibration doc example."""
        findings = [
            _open_finding("①", "low"),
            _open_finding("①", "low"),
            _open_finding("②", "medium"),
            _open_finding("③", "high"),
        ]
        r = compute_delta_repo(findings)
        expected_delta = 2 * 0.4055 + 3.4012 + 0.0
        assert r["delta_repo"] == pytest.approx(round(expected_delta, 4))
        assert r["delta_repo_calibrated"] == pytest.approx(round(expected_delta, 4))
        assert r["active_count"] == 4
        assert r["health_emoji"] == "🔴"
        assert r["health_label"] == "poor"
        assert r["health_factor"] == pytest.approx(round(math.exp(-expected_delta), 4))
        assert r["breakdown"]["①"]["count"] == 2
        assert r["breakdown"]["①"]["delta"] == pytest.approx(0.811, rel=1e-3)
        assert r["breakdown"]["①"]["calibrated"] is True
        assert r["breakdown"]["②"]["delta"] == pytest.approx(3.4012)
        assert r["breakdown"]["③"]["delta"] == 0.0

    def test_uncalibrated_pattern_flagged(self):
        """⑦ uses fallback — breakdown should show calibrated=False."""
        findings = [_open_finding("⑦", "medium")]
        r = compute_delta_repo(findings)
        assert r["delta_repo"] == pytest.approx(0.4055)
        assert r["delta_repo_calibrated"] == 0.0
        assert r["breakdown"]["⑦"]["calibrated"] is False

    def test_stress_test_pattern_excluded(self):
        """⑩ (stress-test origin) should not contribute to δ_repo."""
        findings = [
            _open_finding("①", "high"),
            _open_finding("⑩", "medium"),
            _open_finding("⑩", "low"),
        ]
        r = compute_delta_repo(findings)
        assert r["delta_repo"] == pytest.approx(0.4055)
        assert r["active_count"] == 1
        assert "⑩" not in r["breakdown"]

    def test_compute_matches_health_barometer(self):
        findings = [_open_finding("②", "medium")]
        r = compute_delta_repo(findings)
        emoji, label = health_barometer(r["delta_repo"])
        assert emoji == r["health_emoji"]
        assert label == r["health_label"]


@pytest.mark.parametrize(
    "delta,expected_emoji,expected_label",
    [
        (0.0, "🟢", "excellent"),
        (0.5, "🟡", "good"),
        (1.0, "🟡", "good"),
        (1.0001, "🟠", "moderate"),
        (3.0, "🟠", "moderate"),
        (3.0001, "🔴", "poor"),
        (8.0, "🔴", "poor"),
        (8.0001, "💀", "critical"),
    ],
)
def test_health_barometer_thresholds(delta, expected_emoji, expected_label):
    emoji, label = health_barometer(delta)
    assert emoji == expected_emoji
    assert label == expected_label
