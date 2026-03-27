#!/usr/bin/env python3
"""Export δ_repo + Chao1-style coverage metrics for Phase 1 empirical validation.

Reads existing .delta-lint/findings/*.jsonl and scan_history.jsonl under each repo root.
Does not run scans or call LLMs.

CSV columns include placeholder fields ext_proxy_* for manual join with GitHub / internal data.

Usage:
  cd plugins/delta-lint/scripts
  python -m calibration.export_phase1_metrics --repo /path/to/repo
  python -m calibration.export_phase1_metrics --repos-file repos.txt -o phase1.csv
  python -m calibration.export_phase1_metrics --repo /path/to/repo --jsonl phase1.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# scripts/ on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from findings import list_findings, load_scan_history
from info_theory import compute_coverage_from_history, compute_delta_repo

CSV_FIELDNAMES = [
    "repo_label",
    "repo_path",
    "findings_total",
    "findings_active_for_delta",
    "delta_repo",
    "health_factor",
    "health_emoji",
    "health_label",
    "coverage_pct",
    "chao_estimated_total",
    "chao_unseen_estimate",
    "discovery_trend",
    "scan_history_count",
    "breakdown_json",
    "ext_proxy_name",
    "ext_proxy_value",
    "ext_proxy_period",
    "notes",
]


def collect_phase1_row(repo_path: Path, repo_label: str | None = None) -> dict[str, str | int | float]:
    """Aggregate metrics for one repo root (contains .delta-lint/)."""
    repo_path = repo_path.resolve()
    label = repo_label or repo_path.name

    findings = list_findings(repo_path)
    delta = compute_delta_repo(findings)
    scan_history = load_scan_history(repo_path)
    coverage = compute_coverage_from_history(scan_history, findings)

    return {
        "repo_label": label,
        "repo_path": str(repo_path),
        "findings_total": len(findings),
        "findings_active_for_delta": delta["active_count"],
        "delta_repo": delta["delta_repo"],
        "health_factor": delta["health_factor"],
        "health_emoji": delta["health_emoji"],
        "health_label": delta["health_label"],
        "coverage_pct": coverage.get("coverage_pct", 0),
        "chao_estimated_total": coverage.get("estimated_total", 0),
        "chao_unseen_estimate": coverage.get("unseen_estimate", 0),
        "discovery_trend": coverage.get("discovery_trend", ""),
        "scan_history_count": len(scan_history),
        "breakdown_json": json.dumps(delta.get("breakdown", {}), ensure_ascii=False, separators=(",", ":")),
        "ext_proxy_name": "",
        "ext_proxy_value": "",
        "ext_proxy_period": "",
        "notes": "",
    }


def _read_repos_file(path: Path) -> list[Path]:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[Path] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(Path(s).expanduser())
    return out


def gather_phase1_rows(paths: list[Path]) -> list[dict[str, str | int | float]]:
    """Collect one row per repo root; skip missing dirs, non-dirs, and collect failures."""
    rows: list[dict[str, str | int | float]] = []
    for p in paths:
        p = p.expanduser()
        if not p.is_dir():
            print(f"Warning: skip (not a directory): {p}", file=sys.stderr)
            continue
        try:
            rows.append(collect_phase1_row(p))
        except (FileNotFoundError, KeyError) as e:
            print(f"Warning: skip {p}: {e}", file=sys.stderr)
    return rows


def emit_phase1_output(
    rows: list[dict[str, str | int | float]],
    output: Path | None,
    jsonl: Path | None,
) -> None:
    """Write CSV to stdout or ``output``; optionally append JSON lines to ``jsonl``."""
    if jsonl:
        jsonl.parent.mkdir(parents=True, exist_ok=True)
        with jsonl.open("a", encoding="utf-8") as jf:
            for row in rows:
                jf.write(json.dumps(row, ensure_ascii=False) + "\n")

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export Phase 1 δ_repo / coverage metrics per repo.")
    ap.add_argument("--repo", action="append", default=[], help="Repo root (repeatable)")
    ap.add_argument("--repos-file", type=Path, help="Text file: one absolute repo path per line")
    ap.add_argument("-o", "--output", type=Path, default=None, help="CSV path (default: stdout)")
    ap.add_argument("--jsonl", type=Path, default=None, help="Also append JSON lines to this file")
    args = ap.parse_args()

    paths: list[Path] = [Path(p).expanduser() for p in args.repo]
    if args.repos_file:
        paths.extend(_read_repos_file(args.repos_file))

    if not paths:
        ap.print_help()
        print("\nError: pass --repo PATH or --repos-file PATH", file=sys.stderr)
        return 2

    rows = gather_phase1_rows(paths)
    emit_phase1_output(rows, args.output, args.jsonl)

    if args.output:
        print(f"Wrote {len(rows)} row(s) to {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
