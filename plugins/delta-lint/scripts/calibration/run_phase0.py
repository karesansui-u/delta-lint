#!/usr/bin/env python3
"""Phase 0 calibration experiment runner.

Measures I_base for each (pattern, severity) cell using partial-context protocol.

Protocol:
  Condition A (δ>0): visible_code only → LLM answers questions → acc_A
  Condition B (δ≈0): visible_code + annotation → LLM answers → acc_B
  I = -ln(acc_A / acc_B) if acc_B > acc_A else 0  [nats]

Usage:
  cd plugins/delta-lint/scripts
  python -m calibration.run_phase0 [--model MODEL] [--trials N] [--output PATH]
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

# Add scripts/ to path for llm.py import
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm import call_llm
from calibration.scenarios import SCENARIOS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TRIALS = 10
DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # Haiku = fast + Phase -1 showed identical results
DEFAULT_TEMPERATURE = 0.7
OUTPUT_DIR = Path(__file__).parent / "results"

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are answering questions about a software codebase.
You will be shown code and asked multiple-choice questions.
Answer with ONLY the letter of the correct option (A, B, or C).
Do not explain your reasoning. Just output the single letter."""

USER_PROMPT_CONDITION_A = """\
Here is the code:

```
{visible_code}
```

Question: {question}

Options:
A) {option_a}
B) {option_b}
C) {option_c}

Answer (A, B, or C):"""

USER_PROMPT_CONDITION_B = """\
Here is the code:

```
{visible_code}
```

IMPORTANT ANNOTATION — Hidden implementation detail:
{annotation}

Question: {question}

Options:
A) {option_a}
B) {option_b}
C) {option_c}

Answer (A, B, or C):"""


def extract_answer(response: str) -> str | None:
    """Extract A/B/C from LLM response."""
    response = response.strip().upper()
    # Direct single letter
    if response in ("A", "B", "C"):
        return response
    # First character
    if response and response[0] in ("A", "B", "C"):
        return response[0]
    # Search for "A)" etc.
    for letter in ("A", "B", "C"):
        if f"{letter})" in response or f"{letter}." in response:
            return letter
    return None


def run_trial(scenario: dict, question: dict, condition: str, model: str) -> bool:
    """Run a single trial. Returns True if correct."""
    if condition == "A":
        prompt = USER_PROMPT_CONDITION_A.format(
            visible_code=scenario["visible_code"],
            question=question["q"],
            option_a=question["options"]["A"],
            option_b=question["options"]["B"],
            option_c=question["options"]["C"],
        )
    else:
        prompt = USER_PROMPT_CONDITION_B.format(
            visible_code=scenario["visible_code"],
            annotation=scenario["hidden_behavior"],
            question=question["q"],
            option_a=question["options"]["A"],
            option_b=question["options"]["B"],
            option_c=question["options"]["C"],
        )

    try:
        response = call_llm(
            system=SYSTEM_PROMPT,
            user=prompt,
            model=model,
            temperature=DEFAULT_TEMPERATURE,
            timeout=30,
            retries=1,
        )
        answer = extract_answer(response)
        return answer == question["correct"]
    except Exception as e:
        print(f"    [ERROR] LLM call failed: {e}", file=sys.stderr)
        return False


def run_cell(scenario: dict, trials: int, model: str) -> dict:
    """Run a full cell (1 scenario, all questions, both conditions)."""
    cell_id = scenario["id"]
    print(f"\n{'='*60}")
    print(f"Cell: {cell_id} ({scenario['pattern_name']}, {scenario['severity']})")
    print(f"{'='*60}")

    questions = scenario["questions"]
    n_questions = len(questions)
    total_trials = n_questions * trials

    # Condition A: no annotation (δ > 0)
    correct_a = 0
    for qi, q in enumerate(questions):
        q_correct = 0
        for t in range(trials):
            if run_trial(scenario, q, "A", model):
                q_correct += 1
            # Small delay to avoid rate limiting
            time.sleep(0.3)
        print(f"  Q{qi+1} condA: {q_correct}/{trials}")
        correct_a += q_correct

    acc_a = correct_a / total_trials

    # Condition B: with annotation (δ ≈ 0)
    correct_b = 0
    for qi, q in enumerate(questions):
        q_correct = 0
        for t in range(trials):
            if run_trial(scenario, q, "B", model):
                q_correct += 1
            time.sleep(0.3)
        print(f"  Q{qi+1} condB: {q_correct}/{trials}")
        correct_b += q_correct

    acc_b = correct_b / total_trials

    # Compute I in nats
    if acc_b > acc_a and acc_a > 0:
        i_nats = -math.log(acc_a / acc_b)
    elif acc_b > 0 and acc_a == 0:
        # acc_A = 0 → I = ∞ in theory; use cap at ln(total_trials) as practical bound
        i_nats = math.log(total_trials)
    else:
        i_nats = 0.0

    survival = math.exp(-i_nats)

    result = {
        "id": cell_id,
        "pattern": scenario["pattern"],
        "pattern_name": scenario["pattern_name"],
        "severity": scenario["severity"],
        "acc_a": round(acc_a, 4),
        "acc_b": round(acc_b, 4),
        "correct_a": correct_a,
        "correct_b": correct_b,
        "total_trials": total_trials,
        "i_nats": round(i_nats, 4),
        "survival_factor": round(survival, 4),
        "model": model,
        "trials_per_question": trials,
        "n_questions": n_questions,
    }

    print(f"\n  acc_A={acc_a:.3f}  acc_B={acc_b:.3f}  I={i_nats:.4f} nats  e^{{-I}}={survival:.4f}")
    return result


def generate_i_base_table(results: list[dict]) -> dict:
    """Generate i_base_v1.json from experiment results."""
    i_base = {}
    for r in results:
        key = f"{r['pattern']}-{r['severity']}"
        i_base[key] = {
            "pattern": r["pattern"],
            "pattern_name": r["pattern_name"],
            "severity": r["severity"],
            "i_nats": r["i_nats"],
            "survival_factor": r["survival_factor"],
            "acc_a": r["acc_a"],
            "acc_b": r["acc_b"],
            "model": r["model"],
            "trials": r["trials_per_question"],
        }

    return {
        "version": "v1",
        "protocol": "partial-context",
        "date": time.strftime("%Y-%m-%d"),
        "description": "I_base table from Phase 0 calibration experiment",
        "cells": i_base,
        "summary": {
            "total_cells": len(results),
            "mean_i_nats": round(sum(r["i_nats"] for r in results) / len(results), 4) if results else 0,
            "by_severity": _summarize_by_severity(results),
            "by_pattern": _summarize_by_pattern(results),
        },
    }


def _summarize_by_severity(results):
    by_sev = {}
    for r in results:
        sev = r["severity"]
        by_sev.setdefault(sev, []).append(r["i_nats"])
    return {sev: round(sum(vals) / len(vals), 4) for sev, vals in by_sev.items()}


def _summarize_by_pattern(results):
    by_pat = {}
    for r in results:
        pat = r["pattern"]
        by_pat.setdefault(pat, []).append(r["i_nats"])
    return {pat: round(sum(vals) / len(vals), 4) for pat, vals in by_pat.items()}


def main():
    parser = argparse.ArgumentParser(description="Phase 0 calibration experiment")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LLM model to use")
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS, help="Trials per question per condition")
    parser.add_argument("--output", default=None, help="Output directory for results")
    parser.add_argument("--cell", default=None, help="Run only a specific cell (e.g. '①-high')")
    parser.add_argument("--dry-run", action="store_true", help="Show scenarios without running")
    args = parser.parse_args()

    output_dir = Path(args.output) if args.output else OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Filter scenarios
    scenarios = SCENARIOS
    if args.cell:
        scenarios = [s for s in SCENARIOS if s["id"] == args.cell]
        if not scenarios:
            print(f"Cell '{args.cell}' not found. Available: {[s['id'] for s in SCENARIOS]}")
            sys.exit(1)

    if args.dry_run:
        print("Scenarios:")
        for s in scenarios:
            print(f"  {s['id']}: {s['pattern_name']} ({s['severity']})")
            for qi, q in enumerate(s["questions"]):
                print(f"    Q{qi+1}: {q['q'][:80]}...")
        print(f"\nTotal calls: {len(scenarios) * 3 * args.trials * 2}")
        return

    # Estimate
    total_calls = len(scenarios) * 3 * args.trials * 2
    print(f"Phase 0 Calibration Experiment")
    print(f"Model: {args.model}")
    print(f"Cells: {len(scenarios)}")
    print(f"Trials/question: {args.trials}")
    print(f"Total LLM calls: {total_calls}")
    print(f"Estimated time: ~{total_calls * 2 // 60} min")
    print()

    # Run experiment
    results = []
    start_time = time.time()

    for scenario in scenarios:
        result = run_cell(scenario, args.trials, args.model)
        results.append(result)

        # Save incremental results
        incremental_path = output_dir / "results_incremental.json"
        with open(incremental_path, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - start_time

    # Save full results
    results_path = output_dir / "results_full.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Generate i_base table
    i_base = generate_i_base_table(results)
    i_base_path = output_dir / "i_base_v1.json"
    with open(i_base_path, "w") as f:
        json.dump(i_base, f, indent=2, ensure_ascii=False)

    # Summary
    print(f"\n{'='*60}")
    print(f"EXPERIMENT COMPLETE")
    print(f"{'='*60}")
    print(f"Elapsed: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"Results: {results_path}")
    print(f"I_base:  {i_base_path}")
    print()
    print("I_base summary by severity:")
    for sev, val in i_base["summary"]["by_severity"].items():
        print(f"  {sev}: {val:.4f} nats")
    print()
    print("I_base summary by pattern:")
    for pat, val in i_base["summary"]["by_pattern"].items():
        print(f"  {pat}: {val:.4f} nats")


if __name__ == "__main__":
    main()
