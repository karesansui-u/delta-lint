"""Phase 0 実験フレームワーク — 30 セル（10パターン × 3重大度）の汎用実行基盤.

V3b パイロットの実験インフラを汎用化。各シナリオは Scenario データクラスで
宣言的に定義し、run_scenario() で自動実行する。

使い方:
    from framework import Scenario, Question, run_scenario
    scenario = Scenario(pattern="④", severity="high", ...)
    result = run_scenario(scenario)
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import call_llm

# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass
class Question:
    """多肢選択質問."""
    text: str
    choices: dict[str, str]
    correct: str  # "A", "B", "C", or "D"


@dataclass
class Scenario:
    """1 セル（パターン × 重大度）の実験定義."""
    pattern: str           # "①" 〜 "⑩"
    pattern_name: str      # "Asymmetric Defaults" 等
    severity: str          # "high", "medium", "low"
    description: str       # シナリオの説明

    # ファイル定義: {filename: content}
    visible_files: dict[str, str]

    # 条件 B で追加するアノテーション
    # key = visible_files 内のファイル名, value = そのファイルの条件B版コンテンツ
    annotated_files: dict[str, str]

    # hidden file の情報（実験自体には使わないが記録用）
    hidden_file_name: str
    hidden_file_description: str

    # 質問
    questions: list[Question]

    # システムプロンプト（カスタム可能）
    system_prompt: str = ""

    def __post_init__(self):
        if not self.system_prompt:
            self.system_prompt = (
                "You are a senior software engineer reviewing a codebase.\n"
                f"You are shown source files but {self.hidden_file_name} is NOT included.\n"
                "You must reason about system behavior based on what you can see.\n\n"
                "Answer the multiple-choice question by selecting exactly ONE letter "
                "(A, B, C, or D).\n"
                "Output ONLY the letter of your answer, nothing else."
            )


# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------

def build_prompt(scenario: Scenario, condition: str, question: Question) -> str:
    """条件 A/B に応じたユーザープロンプトを構築."""
    if condition == "A":
        files = scenario.visible_files
    else:
        # 条件 B: annotated_files のキーがあればそちらを使用
        files = {}
        for fname, content in scenario.visible_files.items():
            if fname in scenario.annotated_files:
                files[fname] = scenario.annotated_files[fname]
            else:
                files[fname] = content

    files_text = "\n---\n".join(content for content in files.values())
    choices_text = "\n".join(f"  {k}) {v}" for k, v in question.choices.items())

    return (
        f"You are reviewing a codebase. The following source files are available\n"
        f"(note: {scenario.hidden_file_name} is NOT shown):\n\n"
        f"---\n{files_text}\n---\n\n"
        f"Question: {question.text}\n\n"
        f"{choices_text}\n\n"
        f"Answer (single letter):"
    )


# ---------------------------------------------------------------------------
# 回答抽出
# ---------------------------------------------------------------------------

def extract_answer(response: str) -> str | None:
    text = response.strip()
    if len(text) == 1 and text.upper() in "ABCD":
        return text.upper()
    m = re.search(r'\b([A-D])\b', text)
    if m:
        return m.group(1).upper()
    return None


# ---------------------------------------------------------------------------
# 結果データ
# ---------------------------------------------------------------------------

@dataclass
class TrialResult:
    condition: str
    question_idx: int
    trial: int
    raw_response: str
    extracted: str | None
    correct: bool


@dataclass
class ExperimentResult:
    scenario_key: str = ""  # e.g. "④_high"
    trials: list[TrialResult] = field(default_factory=list)

    @property
    def acc_a(self) -> float:
        a = [t for t in self.trials if t.condition == "A" and t.extracted is not None]
        return sum(t.correct for t in a) / len(a) if a else 0.0

    @property
    def acc_b(self) -> float:
        b = [t for t in self.trials if t.condition == "B" and t.extracted is not None]
        return sum(t.correct for t in b) / len(b) if b else 0.0

    @property
    def i_nats(self) -> float | None:
        a, b = self.acc_a, self.acc_b
        if b == 0:
            return None
        if a == 0:
            return float("inf")
        return -math.log(a / b)

    def acc_by_question(self, condition: str) -> dict[int, float]:
        result = {}
        q_indices = set(t.question_idx for t in self.trials)
        for q_idx in q_indices:
            trials = [t for t in self.trials
                      if t.condition == condition and t.question_idx == q_idx
                      and t.extracted is not None]
            if trials:
                result[q_idx] = sum(t.correct for t in trials) / len(trials)
        return result

    def confidence_interval_95(self, condition: str) -> tuple[float, float]:
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

    def response_distribution(self, condition: str, q_idx: int) -> dict[str, int]:
        dist: dict[str, int] = {}
        for t in self.trials:
            if t.condition == condition and t.question_idx == q_idx and t.extracted:
                dist[t.extracted] = dist.get(t.extracted, 0) + 1
        return dist


# ---------------------------------------------------------------------------
# 実験実行
# ---------------------------------------------------------------------------

def run_scenario(
    scenario: Scenario,
    *,
    n_trials: int = 10,
    temperature: float = 0.7,
    model: str = "claude-haiku-4-5-20251001",
    verbose: bool = True,
) -> ExperimentResult:
    """1 シナリオ（1 セル）を実行して ExperimentResult を返す."""
    result = ExperimentResult(
        scenario_key=f"{scenario.pattern}_{scenario.severity}"
    )
    total_calls = n_trials * len(scenario.questions) * 2
    call_count = 0

    for condition in ["A", "B"]:
        cond_label = (
            f"δ>0 (no annotation, {scenario.hidden_file_name} hidden)"
            if condition == "A"
            else f"δ≈0 (annotated)"
        )
        if verbose:
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"条件 {condition}: {cond_label}", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)

        for q_idx, question in enumerate(scenario.questions):
            if verbose:
                print(f"\n  Q{q_idx+1}: {question.text[:70]}...", file=sys.stderr)
                print(f"  正解: {question.correct}", file=sys.stderr)

            for trial in range(n_trials):
                call_count += 1
                prompt = build_prompt(scenario, condition, question)

                try:
                    raw = call_llm(
                        scenario.system_prompt, prompt,
                        model=model,
                        temperature=temperature,
                        timeout=90,
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

                result.trials.append(TrialResult(
                    condition=condition, question_idx=q_idx,
                    trial=trial, raw_response=raw.strip(),
                    extracted=extracted, correct=is_correct,
                ))

                if verbose:
                    mark = "✓" if is_correct else "✗"
                    print(f"    Trial {trial+1}: {extracted or '?'} {mark}  "
                          f"[{call_count}/{total_calls}]", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# 結果表示
# ---------------------------------------------------------------------------

def print_results(
    scenario: Scenario,
    result: ExperimentResult,
    n_trials: int,
    model: str,
    temperature: float,
) -> None:
    print(f"\n{'='*60}")
    print(f"Phase 0: {scenario.pattern} {scenario.pattern_name} × {scenario.severity}")
    print(f"{'='*60}")
    print(f"\n設定: {n_trials} trials × {len(scenario.questions)} questions × 2 conditions")
    print(f"モデル: {model}, temperature: {temperature}")
    print(f"方式: 部分コンテキスト（{scenario.hidden_file_name} hidden）")
    print(f"概要: {scenario.description}")

    for condition in ["A", "B"]:
        label = "δ>0 (no annotation)" if condition == "A" else "δ≈0 (annotated)"
        ci = result.confidence_interval_95(condition)
        acc = result.acc_a if condition == "A" else result.acc_b
        by_q = result.acc_by_question(condition)

        print(f"\n--- 条件 {condition}: {label} ---")
        print(f"  全体 accuracy: {acc:.1%}  (95% CI: [{ci[0]:.1%}, {ci[1]:.1%}])")
        for q_idx in sorted(by_q):
            dist = result.response_distribution(condition, q_idx)
            dist_str = " ".join(f"{k}:{v}" for k, v in sorted(dist.items()))
            print(f"  Q{q_idx+1}: {by_q[q_idx]:.0%} ({int(by_q[q_idx]*n_trials)}/{n_trials})  [{dist_str}]")

    print(f"\n--- I(f) = -ln(acc_A / acc_B) ---")
    acc_a, acc_b = result.acc_a, result.acc_b
    print(f"  acc_A = {acc_a:.3f}")
    print(f"  acc_B = {acc_b:.3f}")

    i_nats = result.i_nats
    if i_nats is not None and i_nats != float("inf"):
        print(f"  I(f) = -ln({acc_a:.3f} / {acc_b:.3f}) = {i_nats:.3f} nats")
        print(f"  e^{{-I}} = {math.exp(-i_nats):.3f}")
    elif i_nats == float("inf"):
        print(f"  I(f) = ∞ (acc_A = 0)")
    else:
        print("  I(f): 計算不能 (acc_B = 0)")

    print(f"\n--- 質問別分析 ---")
    for q_idx in range(len(scenario.questions)):
        acc_a_q = result.acc_by_question("A").get(q_idx, 0)
        acc_b_q = result.acc_by_question("B").get(q_idx, 0)
        diff = acc_b_q - acc_a_q
        print(f"  Q{q_idx+1}: acc_A={acc_a_q:.0%} → acc_B={acc_b_q:.0%} (Δ={diff:+.0%})")
        if abs(diff) > 0.2:
            print(f"       ^ アノテーション効果大")


# ---------------------------------------------------------------------------
# 結果保存
# ---------------------------------------------------------------------------

def save_result(
    scenario: Scenario,
    result: ExperimentResult,
    path: Path,
    n_trials: int,
    model: str,
    temperature: float,
    elapsed: float = 0.0,
) -> None:
    data = {
        "experiment": "phase0_calibration",
        "pattern": scenario.pattern,
        "pattern_name": scenario.pattern_name,
        "severity": scenario.severity,
        "scenario_key": result.scenario_key,
        "description": scenario.description,
        "hidden_file": scenario.hidden_file_name,
        "protocol": "partial_context",
        "model": model,
        "temperature": temperature,
        "n_trials": n_trials,
        "n_questions": len(scenario.questions),
        "acc_a": result.acc_a,
        "acc_b": result.acc_b,
        "i_nats": result.i_nats if result.i_nats != float("inf") else "inf",
        "ci_a_95": list(result.confidence_interval_95("A")),
        "ci_b_95": list(result.confidence_interval_95("B")),
        "acc_by_question_a": {str(k): v for k, v in result.acc_by_question("A").items()},
        "acc_by_question_b": {str(k): v for k, v in result.acc_by_question("B").items()},
        "response_dist_a": {
            str(q): result.response_distribution("A", q)
            for q in range(len(scenario.questions))
        },
        "response_dist_b": {
            str(q): result.response_distribution("B", q)
            for q in range(len(scenario.questions))
        },
        "elapsed_seconds": elapsed,
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(f"\n結果を保存: {path}")


# ---------------------------------------------------------------------------
# メインランナー
# ---------------------------------------------------------------------------

def run_and_save(
    scenario: Scenario,
    *,
    n_trials: int = 10,
    temperature: float = 0.7,
    model: str = "claude-haiku-4-5-20251001",
    output_dir: Optional[Path] = None,
    verbose: bool = True,
) -> ExperimentResult:
    """シナリオ実行 → 表示 → 保存 のワンショット."""
    key = f"{scenario.pattern}_{scenario.severity}"
    print(f"\nPhase 0: {scenario.pattern} {scenario.pattern_name} × {scenario.severity}",
          file=sys.stderr)
    total = n_trials * len(scenario.questions) * 2
    print(f"設計: {n_trials} trials × {len(scenario.questions)} Q × 2 cond = {total} calls",
          file=sys.stderr)
    print(f"モデル: {model}, temp: {temperature}", file=sys.stderr)

    start = time.time()
    result = run_scenario(
        scenario,
        n_trials=n_trials,
        temperature=temperature,
        model=model,
        verbose=verbose,
    )
    elapsed = time.time() - start

    print_results(scenario, result, n_trials, model, temperature)
    print(f"\n所要時間: {elapsed:.0f}秒 ({elapsed/60:.1f}分)")

    if output_dir is None:
        output_dir = Path(__file__).parent / "results" / "phase0"

    model_short = model.split("-")[1] if "-" in model else model
    out_path = output_dir / f"{scenario.pattern}_{scenario.severity}_{model_short}.json"
    save_result(scenario, result, out_path, n_trials, model, temperature, elapsed)

    return result
