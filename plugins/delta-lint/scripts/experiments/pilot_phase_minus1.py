"""Phase -1 Pilot: ④ Guard Non-Propagation × high の I_base 実測.

プロトコル（docs/nats-delta.md §3.2 / §5.2）:
  条件A（δ>0）: 矛盾を含むコードをそのまま見せて推論
  条件B（δ≈0）: 矛盾箇所にアノテーションを付与して推論
  I(f) = -ln(acc_A / acc_B) [nats]

設計: 3質問 × 10試行 × 2条件 = 60 calls（claude -p で $0）
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# llm.py を import できるよう scripts/ を path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import call_llm

# ---------------------------------------------------------------------------
# 実験設定
# ---------------------------------------------------------------------------

N_TRIALS = 10
TEMPERATURE = 0.7
MODEL = "claude-sonnet-4-20250514"

# ---------------------------------------------------------------------------
# テストシナリオ: ④ Guard Non-Propagation × high
#
# file_a (api/orders.py): 入力バリデーションあり
# file_b (workers/order_processor.py): バリデーションなし ← 矛盾
# file_c (services/order_service.py): バリデーションなし（前提: 呼び出し元が検証済み）
# ---------------------------------------------------------------------------

CODE_FILE_A = """\
# api/orders.py
from services.order_service import create_order

def post_order(request):
    \"\"\"REST API endpoint for creating orders.\"\"\"
    data = request.json

    # Input validation
    if not data.get("product_id"):
        raise ValidationError("Product ID is required")
    if not isinstance(data.get("amount"), (int, float)) or data["amount"] <= 0:
        raise ValidationError("Amount must be a positive number")
    if data["amount"] > 999999:
        raise ValidationError("Amount exceeds maximum limit")

    return create_order(
        product_id=data["product_id"],
        amount=data["amount"],
        user_id=request.user.id,
    )
"""

CODE_FILE_B_CONDITION_A = """\
# workers/order_processor.py
from services.order_service import create_order

def process_bulk_import(csv_rows):
    \"\"\"Process bulk order import from partner CSV feed.

    Runs nightly via cron. Each row: product_id, amount, user_id.
    \"\"\"
    results = []
    for row in csv_rows:
        result = create_order(
            product_id=row["product_id"],
            amount=int(row["amount"]),
            user_id=row["user_id"],
        )
        results.append(result)
    return results
"""

CODE_FILE_B_CONDITION_B = """\
# workers/order_processor.py
from services.order_service import create_order

def process_bulk_import(csv_rows):
    \"\"\"Process bulk order import from partner CSV feed.

    Runs nightly via cron. Each row: product_id, amount, user_id.

    ⚠ INCONSISTENCY: Unlike api/orders.py which validates amount > 0,
    amount <= 999999, and product_id presence, this function performs
    NO input validation before calling create_order().
    create_order() assumes pre-validated input (see its docstring).
    Any invalid data in the CSV (negative amounts, missing product_id,
    etc.) will be written directly to the database.
    \"\"\"
    results = []
    for row in csv_rows:
        result = create_order(
            product_id=row["product_id"],
            amount=int(row["amount"]),
            user_id=row["user_id"],
        )
        results.append(result)
    return results
"""

CODE_FILE_C = """\
# services/order_service.py
from db import session, Order

def create_order(product_id: str, amount: int, user_id: str) -> dict:
    \"\"\"Create an order record.

    Assumes caller has validated input (amount > 0, product_id exists).
    This is a thin persistence layer — no business logic validation.
    \"\"\"
    order = Order(
        product_id=product_id,
        amount=amount,
        user_id=user_id,
        status="pending",
    )
    session.add(order)
    session.commit()
    return {"order_id": order.id, "status": "pending"}
"""

# ---------------------------------------------------------------------------
# 推論タスク（多肢選択式、正解は1つ）
# ---------------------------------------------------------------------------

@dataclass
class Question:
    text: str
    choices: dict[str, str]
    correct: str  # "A", "B", "C", or "D"

QUESTIONS = [
    Question(
        text=(
            "A partner sends a CSV file containing a row with amount=-100 and "
            "a valid product_id. What happens when process_bulk_import() processes "
            "this row?"
        ),
        choices={
            "A": "A ValidationError is raised and the row is skipped",
            "B": "The order is created with amount=-100 in the database",
            "C": "The amount is silently converted to its absolute value (100)",
            "D": "create_order() rejects the negative amount and raises an error",
        },
        correct="B",
    ),
    Question(
        text=(
            "A QA engineer needs to verify that negative amounts cannot enter "
            "the database through ANY code path. Which testing strategy is correct?"
        ),
        choices={
            "A": "Test only post_order() — it's the only user-facing entry point",
            "B": "Test only create_order() — all paths converge there and it validates input",
            "C": "Test both post_order() AND process_bulk_import() — they may have different validation",
            "D": "Test only process_bulk_import() — bulk imports are the highest risk",
        },
        correct="C",
    ),
    Question(
        text=(
            "The ops team discovers Order records with amount=0 in the production "
            "database. The REST API validation rejects amount <= 0. "
            "What is the most likely source of these records?"
        ),
        choices={
            "A": "A bug in the REST API validation (off-by-one in the comparison)",
            "B": "The bulk import worker, which does not validate amount before writing",
            "C": "A database migration that set default amount to 0",
            "D": "create_order() has a fallback that sets amount to 0 for missing values",
        },
        correct="B",
    ),
]

# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior software engineer analyzing Python code.
Answer the multiple-choice question by selecting exactly ONE letter (A, B, C, or D).
Output ONLY the letter of your answer, nothing else. No explanation."""

def build_user_prompt(condition: str, question: Question) -> str:
    """条件A or B のコードと質問からプロンプトを構築."""
    file_b = CODE_FILE_B_CONDITION_A if condition == "A" else CODE_FILE_B_CONDITION_B

    choices_text = "\n".join(f"  {k}) {v}" for k, v in question.choices.items())

    return f"""\
Here are three files from a Python web application:

---
{CODE_FILE_A}
---
{file_b}
---
{CODE_FILE_C}
---

Question: {question.text}

{choices_text}

Answer (single letter):"""


# ---------------------------------------------------------------------------
# 回答抽出・採点
# ---------------------------------------------------------------------------

def extract_answer(response: str) -> str | None:
    """LLM 応答から選択肢の文字を抽出."""
    text = response.strip()
    # 単一文字
    if len(text) == 1 and text.upper() in "ABCD":
        return text.upper()
    # "B" や "B)" や "Answer: B" 等
    m = re.search(r'\b([A-D])\b', text)
    if m:
        return m.group(1).upper()
    return None


# ---------------------------------------------------------------------------
# 実験実行
# ---------------------------------------------------------------------------

@dataclass
class TrialResult:
    condition: str  # "A" or "B"
    question_idx: int
    trial: int
    raw_response: str
    extracted: str | None
    correct: bool

@dataclass
class ExperimentResult:
    trials: list[TrialResult] = field(default_factory=list)

    @property
    def acc_a(self) -> float:
        a_trials = [t for t in self.trials if t.condition == "A" and t.extracted is not None]
        return sum(t.correct for t in a_trials) / len(a_trials) if a_trials else 0.0

    @property
    def acc_b(self) -> float:
        b_trials = [t for t in self.trials if t.condition == "B" and t.extracted is not None]
        return sum(t.correct for t in b_trials) / len(b_trials) if b_trials else 0.0

    @property
    def i_nats(self) -> float | None:
        """I(f) = -ln(acc_A / acc_B) [nats]."""
        a, b = self.acc_a, self.acc_b
        if b == 0 or a == 0:
            return None  # 計算不能
        return -math.log(a / b)

    def acc_by_question(self, condition: str) -> dict[int, float]:
        result = {}
        for q_idx in range(len(QUESTIONS)):
            trials = [t for t in self.trials
                      if t.condition == condition and t.question_idx == q_idx
                      and t.extracted is not None]
            if trials:
                result[q_idx] = sum(t.correct for t in trials) / len(trials)
        return result

    def confidence_interval_95(self, condition: str) -> tuple[float, float]:
        """Wilson score interval for binomial proportion."""
        trials = [t for t in self.trials if t.condition == condition and t.extracted is not None]
        n = len(trials)
        if n == 0:
            return (0.0, 0.0)
        p = sum(t.correct for t in trials) / n
        z = 1.96
        denom = 1 + z**2 / n
        center = (p + z**2 / (2 * n)) / denom
        spread = z * math.sqrt((p * (1 - p) + z**2 / (4 * n)) / n) / denom
        return (max(0.0, center - spread), min(1.0, center + spread))


def run_experiment(n_trials: int = N_TRIALS, temperature: float = TEMPERATURE,
                   model: str = MODEL, verbose: bool = True) -> ExperimentResult:
    """パイロット実験を実行."""
    result = ExperimentResult()
    total_calls = n_trials * len(QUESTIONS) * 2
    call_count = 0

    for condition in ["A", "B"]:
        cond_label = "δ>0 (implicit)" if condition == "A" else "δ≈0 (annotated)"
        if verbose:
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"条件 {condition}: {cond_label}", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)

        for q_idx, question in enumerate(QUESTIONS):
            if verbose:
                print(f"\n  Q{q_idx+1}: {question.text[:70]}...", file=sys.stderr)
                print(f"  正解: {question.correct}", file=sys.stderr)

            for trial in range(n_trials):
                call_count += 1
                prompt = build_user_prompt(condition, question)

                try:
                    raw = call_llm(
                        SYSTEM_PROMPT, prompt,
                        model=model,
                        temperature=temperature,
                        timeout=60,
                    )
                except RuntimeError as e:
                    if verbose:
                        print(f"    Trial {trial+1}: ERROR - {e}", file=sys.stderr)
                    result.trials.append(TrialResult(
                        condition=condition, question_idx=q_idx,
                        trial=trial, raw_response=str(e),
                        extracted=None, correct=False,
                    ))
                    continue

                extracted = extract_answer(raw)
                is_correct = extracted == question.correct

                tr = TrialResult(
                    condition=condition, question_idx=q_idx,
                    trial=trial, raw_response=raw.strip(),
                    extracted=extracted, correct=is_correct,
                )
                result.trials.append(tr)

                if verbose:
                    mark = "✓" if is_correct else "✗"
                    print(f"    Trial {trial+1}: {extracted or '?'} {mark}  "
                          f"[{call_count}/{total_calls}]", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# 結果表示
# ---------------------------------------------------------------------------

def print_results(result: ExperimentResult, n_trials: int = N_TRIALS,
                  model: str = MODEL, temperature: float = TEMPERATURE) -> None:
    """実験結果を表示."""
    print("\n" + "=" * 60)
    print("Phase -1 Pilot Results: ④ Guard Non-Propagation × high")
    print("=" * 60)

    print(f"\n設定: {n_trials} trials × {len(QUESTIONS)} questions × 2 conditions")
    print(f"モデル: {model}, temperature: {temperature}")

    # 条件別・質問別 accuracy
    for condition in ["A", "B"]:
        label = "δ>0 (implicit)" if condition == "A" else "δ≈0 (annotated)"
        ci = result.confidence_interval_95(condition)
        acc = result.acc_a if condition == "A" else result.acc_b
        by_q = result.acc_by_question(condition)

        print(f"\n--- 条件 {condition}: {label} ---")
        print(f"  全体 accuracy: {acc:.1%}  (95% CI: [{ci[0]:.1%}, {ci[1]:.1%}])")
        for q_idx in sorted(by_q):
            print(f"  Q{q_idx+1}: {by_q[q_idx]:.0%} ({int(by_q[q_idx]*n_trials)}/{n_trials})")

    # I(f) 計算
    print(f"\n--- I(f) = -ln(acc_A / acc_B) ---")
    acc_a, acc_b = result.acc_a, result.acc_b
    print(f"  acc_A = {acc_a:.3f}")
    print(f"  acc_B = {acc_b:.3f}")

    i_nats = result.i_nats
    if i_nats is not None:
        print(f"  I(④, high) = -ln({acc_a:.3f} / {acc_b:.3f}) = {i_nats:.3f} nats")
        print(f"  e^{{-I}} = {math.exp(-i_nats):.3f}")
    else:
        print("  I(f): 計算不能 (acc_A=0 or acc_B=0)")

    # 解釈
    if i_nats is not None:
        print(f"\n--- 解釈 ---")
        if i_nats > 0:
            print(f"  アノテーションにより accuracy が {acc_b:.0%} → {acc_a:.0%} に低下")
            print(f"  矛盾の暗黙存在が {i_nats:.2f} nats の情報損失を生んでいる")
        elif i_nats == 0:
            print(f"  アノテーションの有無で accuracy に差なし → δ ≈ 0")
            print(f"  この矛盾パターンは LLM にとって「見抜ける」矛盾")
        else:
            print(f"  acc_A > acc_B (逆転) → 実験設計の見直しが必要")

    # acc_A ≈ acc_B 問題のチェック
    if acc_a > 0 and acc_b > 0:
        ratio = acc_a / acc_b
        if ratio > 0.9:
            print(f"\n⚠ acc_A / acc_B = {ratio:.2f} (差が小さい)")
            print(f"  考えられる原因:")
            print(f"  - LLM がコードを注意深く読んで矛盾に気づいている")
            print(f"  - 質問が矛盾の有無に依存しにくい設計になっている")
            print(f"  - N={N_TRIALS} が少なすぎて差が出ていない")


def save_results(result: ExperimentResult, path: Path,
                 n_trials: int = N_TRIALS, model: str = MODEL,
                 temperature: float = TEMPERATURE) -> None:
    """結果を JSON で保存."""
    data = {
        "experiment": "phase_minus1_pilot",
        "pattern": "④ Guard Non-Propagation",
        "severity": "high",
        "model": model,
        "temperature": temperature,
        "n_trials": n_trials,
        "n_questions": len(QUESTIONS),
        "acc_a": result.acc_a,
        "acc_b": result.acc_b,
        "i_nats": result.i_nats,
        "ci_a_95": list(result.confidence_interval_95("A")),
        "ci_b_95": list(result.confidence_interval_95("B")),
        "acc_by_question_a": result.acc_by_question("A"),
        "acc_by_question_b": result.acc_by_question("B"),
        "trials": [
            {
                "condition": t.condition,
                "question_idx": t.question_idx,
                "trial": t.trial,
                "raw_response": t.raw_response,
                "extracted": t.extracted,
                "correct": t.correct,
            }
            for t in result.trials
        ],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(f"\n結果を保存: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Phase -1 Pilot: ④ Guard Non-Propagation × high の I_base 実測")
    parser.add_argument("--trials", type=int, default=N_TRIALS,
                        help=f"試行回数 (default: {N_TRIALS})")
    parser.add_argument("--temperature", type=float, default=TEMPERATURE,
                        help=f"サンプリング温度 (default: {TEMPERATURE})")
    parser.add_argument("--model", default=MODEL,
                        help=f"モデル (default: {MODEL})")
    parser.add_argument("--output", type=str, default=None,
                        help="結果 JSON の出力先 (default: experiments/results/pilot_result.json)")
    parser.add_argument("--quiet", action="store_true",
                        help="進捗出力を抑制")
    args = parser.parse_args()

    n_trials = args.trials
    temperature = args.temperature
    model = args.model
    total_calls = n_trials * len(QUESTIONS) * 2

    print(f"Phase -1 Pilot Experiment", file=sys.stderr)
    print(f"パターン: ④ Guard Non-Propagation × high", file=sys.stderr)
    print(f"設計: {n_trials} trials × {len(QUESTIONS)} questions × 2 conditions "
          f"= {total_calls} calls", file=sys.stderr)
    print(f"モデル: {model}, temperature: {temperature}", file=sys.stderr)
    print(f"コスト: $0 (claude -p)", file=sys.stderr)

    start = time.time()
    result = run_experiment(n_trials=n_trials, temperature=temperature,
                            model=model, verbose=not args.quiet)
    elapsed = time.time() - start

    print_results(result, n_trials=n_trials, model=model, temperature=temperature)
    print(f"\n所要時間: {elapsed:.0f}秒 ({elapsed/60:.1f}分)")

    # 結果保存
    output_path = Path(args.output) if args.output else (
        Path(__file__).parent / "results" / "pilot_result.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_results(result, output_path, n_trials=n_trials, model=model,
                 temperature=temperature)


if __name__ == "__main__":
    main()
