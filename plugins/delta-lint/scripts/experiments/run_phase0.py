"""Phase 0 全セル実行ランナー.

使い方:
    cd plugins/delta-lint/scripts
    python experiments/run_phase0.py                    # 全30セル Haiku
    python experiments/run_phase0.py --pattern ④        # 特定パターンのみ
    python experiments/run_phase0.py --severity high    # 特定重大度のみ
    python experiments/run_phase0.py --cell ④_high      # 特定セルのみ
    python experiments/run_phase0.py --dry-run           # 1 trial で確認
    python experiments/run_phase0.py --model claude-sonnet-4-20250514  # Sonnet で実行
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Ensure import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from framework import run_and_save, Scenario

# Import all scenarios
from scenarios.p01_asymmetric import P01_HIGH, P01_MEDIUM, P01_LOW
from scenarios.p02_semantic import P02_HIGH, P02_MEDIUM, P02_LOW
from scenarios.p03_spec import P03_HIGH, P03_MEDIUM, P03_LOW
from scenarios.p04_guard import P04_HIGH, P04_MEDIUM, P04_LOW
from scenarios.p05_paired import P05_HIGH, P05_MEDIUM, P05_LOW
from scenarios.p06_lifecycle import P06_HIGH, P06_MEDIUM, P06_LOW
from scenarios.p07_dead import P07_HIGH, P07_MEDIUM, P07_LOW
from scenarios.p08_duplication import P08_HIGH, P08_MEDIUM, P08_LOW
from scenarios.p09_interface import P09_HIGH, P09_MEDIUM, P09_LOW
from scenarios.p10_abstraction import P10_HIGH, P10_MEDIUM, P10_LOW

ALL_SCENARIOS: list[Scenario] = [
    P01_HIGH, P01_MEDIUM, P01_LOW,
    P02_HIGH, P02_MEDIUM, P02_LOW,
    P03_HIGH, P03_MEDIUM, P03_LOW,
    P04_HIGH, P04_MEDIUM, P04_LOW,
    P05_HIGH, P05_MEDIUM, P05_LOW,
    P06_HIGH, P06_MEDIUM, P06_LOW,
    P07_HIGH, P07_MEDIUM, P07_LOW,
    P08_HIGH, P08_MEDIUM, P08_LOW,
    P09_HIGH, P09_MEDIUM, P09_LOW,
    P10_HIGH, P10_MEDIUM, P10_LOW,
]


def main():
    parser = argparse.ArgumentParser(description="Phase 0 全セル実行ランナー")
    parser.add_argument("--pattern", type=str, help="特定パターン(①〜⑩)のみ実行")
    parser.add_argument("--severity", type=str, choices=["high", "medium", "low"],
                        help="特定重大度のみ実行")
    parser.add_argument("--cell", type=str, help="特定セル(例: ④_high)のみ実行")
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--model", type=str, default="claude-haiku-4-5-20251001")
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true", help="1 trial で確認")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    # Filter scenarios
    scenarios = ALL_SCENARIOS
    if args.pattern:
        scenarios = [s for s in scenarios if s.pattern == args.pattern]
    if args.severity:
        scenarios = [s for s in scenarios if s.severity == args.severity]
    if args.cell:
        p, s = args.cell.rsplit("_", 1)
        scenarios = [sc for sc in scenarios if sc.pattern == p and sc.severity == s]

    if not scenarios:
        print("対象シナリオが見つかりません。", file=sys.stderr)
        sys.exit(1)

    n_trials = 1 if args.dry_run else args.trials
    output_dir = Path(args.output_dir) if args.output_dir else None

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Phase 0 キャリブレーション実験", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"セル数: {len(scenarios)}", file=sys.stderr)
    print(f"各セル: {n_trials} trials × 3 Q × 2 cond = {n_trials * 6} calls", file=sys.stderr)
    total_calls = len(scenarios) * n_trials * 6
    print(f"総コール数: {total_calls}", file=sys.stderr)
    print(f"モデル: {args.model}", file=sys.stderr)
    print(f"温度: {args.temperature}", file=sys.stderr)
    if args.dry_run:
        print(f"⚡ DRY RUN (1 trial)", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    results = {}
    start_all = time.time()

    for i, scenario in enumerate(scenarios, 1):
        key = f"{scenario.pattern}_{scenario.severity}"
        print(f"\n[{i}/{len(scenarios)}] {key}: {scenario.pattern_name} × {scenario.severity}",
              file=sys.stderr)
        print(f"  {scenario.description}", file=sys.stderr)

        result = run_and_save(
            scenario,
            n_trials=n_trials,
            temperature=args.temperature,
            model=args.model,
            output_dir=output_dir,
            verbose=not args.quiet,
        )
        results[key] = {
            "acc_a": result.acc_a,
            "acc_b": result.acc_b,
            "i_nats": result.i_nats,
        }

    elapsed = time.time() - start_all

    # Summary
    print(f"\n\n{'='*60}")
    print(f"Phase 0 実験完了サマリ")
    print(f"{'='*60}")
    print(f"セル数: {len(scenarios)}")
    print(f"総コール数: {total_calls}")
    print(f"所要時間: {elapsed:.0f}秒 ({elapsed/60:.1f}分)")
    print(f"\n{'セル':<12} {'acc_A':>8} {'acc_B':>8} {'I(f) nats':>10}")
    print("-" * 42)

    for key in sorted(results.keys()):
        r = results[key]
        i_str = f"{r['i_nats']:.3f}" if r["i_nats"] is not None and r["i_nats"] != float("inf") else "∞"
        print(f"{key:<12} {r['acc_a']:>8.3f} {r['acc_b']:>8.3f} {i_str:>10}")

    # Save summary
    summary_path = (Path(args.output_dir) if args.output_dir
                     else Path(__file__).parent / "results" / "phase0") / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_data = {
        "experiment": "phase0_calibration",
        "model": args.model,
        "n_trials": n_trials,
        "temperature": args.temperature,
        "n_scenarios": len(scenarios),
        "total_calls": total_calls,
        "elapsed_seconds": elapsed,
        "results": results,
    }
    summary_path.write_text(json.dumps(summary_data, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    print(f"\nサマリを保存: {summary_path}")


if __name__ == "__main__":
    main()
