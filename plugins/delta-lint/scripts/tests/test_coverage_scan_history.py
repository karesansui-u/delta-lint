"""
scan_history のレガシー行（scope 欠落）とカバレッジマトリクスの整合。

dl-b7291310: scan_type=deep かつ scope なしの行は、append_scan_history の推論と同じく
(smart, deep, default) に集計されること（wide セルに誤って入らないこと）。
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from findings import compute_coverage_matrix


def test_legacy_scan_type_deep_without_scope_maps_to_smart_deep_cell():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        hist = base / ".delta-lint" / "scan_history.jsonl"
        hist.parent.mkdir(parents=True)
        rec = {
            "scan_type": "deep",
            "timestamp": "2026-03-21T12:00:00",
            "clusters": 1,
            "findings_count": 0,
        }
        hist.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")

        matrix = compute_coverage_matrix(base)
        cells = {(c["scope"], c["depth"], c["lens"]): c["count"] for c in matrix["cells"]}

        assert cells.get(("smart", "deep", "default"), 0) == 1
        assert cells.get(("wide", "deep", "default"), 0) == 0
