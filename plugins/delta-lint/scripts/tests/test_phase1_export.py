"""Phase 1 metrics export — no I/O beyond temp dirs."""

import csv
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from calibration.export_phase1_metrics import (
    CSV_FIELDNAMES,
    collect_phase1_row,
    gather_phase1_rows,
)


def test_collect_empty_repo(tmp_path):
    (tmp_path / ".delta-lint" / "findings").mkdir(parents=True)
    row = collect_phase1_row(tmp_path, repo_label="empty_test")
    assert row["repo_label"] == "empty_test"
    assert row["findings_total"] == 0
    assert row["delta_repo"] == 0.0
    assert row["health_emoji"] == "🟢"
    assert row["scan_history_count"] == 0


def test_csv_fieldnames_cover_row_keys():
    sample = collect_phase1_row(Path(".").resolve(), repo_label="x")
    missing = set(sample.keys()) - set(CSV_FIELDNAMES)
    assert not missing, f"CSV_FIELDNAMES missing: {missing}"


def test_gather_skips_nonexistent_path(tmp_path):
    missing = tmp_path / "does_not_exist"
    rows = gather_phase1_rows([missing])
    assert rows == []


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError("simulated missing .delta-lint/findings"),
        KeyError("bad"),
    ],
)
def test_gather_skips_when_collect_raises_file_not_found_or_key_error(tmp_path, monkeypatch, exc):
    """FileNotFoundError / KeyError from collect → skip row (matches gather_phase1_rows except)."""
    root = tmp_path / "root"
    root.mkdir()

    def boom(_path):
        raise exc

    monkeypatch.setattr(
        "calibration.export_phase1_metrics.collect_phase1_row",
        boom,
    )
    rows = gather_phase1_rows([root])
    assert rows == []


def test_gather_one_row_when_delta_lint_findings_exists(tmp_path):
    """Valid layout under a repo root → exactly one CSV row with that folder name as label."""
    root = tmp_path / "myrepo"
    (root / ".delta-lint" / "findings").mkdir(parents=True)
    missing_sibling = tmp_path / "nope"
    rows = gather_phase1_rows([root, missing_sibling])
    assert len(rows) == 1
    assert rows[0]["repo_label"] == "myrepo"
    assert rows[0]["findings_total"] == 0


def test_csv_roundtrip_dict_keys():
    row = collect_phase1_row(Path(".").resolve(), repo_label="roundtrip")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
    w.writeheader()
    w.writerow(row)
    buf.seek(0)
    r = csv.DictReader(buf)
    rows = list(r)
    assert len(rows) == 1
    assert rows[0]["repo_label"] == "roundtrip"
