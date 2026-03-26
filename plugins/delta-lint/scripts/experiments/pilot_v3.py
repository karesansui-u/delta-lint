"""Phase -1 Pilot V3: 部分コンテキスト方式 — ④ Guard Non-Propagation × high.

V1/V2 の結果:
  全ファイルを見せると Sonnet/Haiku ともに 100% 正解 → I = 0 nats.
  LLM は提供されたコード全体を等しく注意深く読むため、
  矛盾が「そこに書いてある」限りアノテーションは不要。

V3 の改善 — 部分コンテキスト方式:
  矛盾のある file_b を**見せない**。
  人間の開発体験をシミュレート（「file_a は読んだが file_b は知らない」）。

  条件A（δ>0）: file_a + file_c + noise を見せる。file_b は見せない。
    → LLM は file_a のパターンが全体に適用されると仮定 → 誤推論
  条件B（δ≈0）: 同上だが、file_a に「file_b はバリデーションなし」とアノテーション。
    → LLM は矛盾の存在を知った上で推論 → 正解

  I(f) = -ln(acc_A / acc_B) [nats]
  = 「file_b の情報がない状態での推論劣化」を計測

理論的根拠:
  δ = D_KL(P_actual || P_expected)
  P_actual = file_b を知らない状態での推論分布
  P_expected = 矛盾が明示された状態での推論分布
  実際のコードベースで開発者が全ファイルを読まないのと同じ状況。
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import call_llm

# ---------------------------------------------------------------------------
# 実験設定
# ---------------------------------------------------------------------------

N_TRIALS = 10
TEMPERATURE = 0.7
MODEL = "claude-sonnet-4-20250514"

# ---------------------------------------------------------------------------
# シナリオ: 決済システム — timeout 非対称
#
# 見せるファイル:
#   - config/settings.py (ノイズ)
#   - models/payment.py (ノイズ)
#   - middleware/auth.py (ノイズ)
#   - api/gateway.py (GATEWAY_TIMEOUT=30s) ← メイン
#   - api/internal.py (gateway → payment_service の中継)
#   - services/notification_service.py (ノイズ)
#   - repositories/payment_repo.py (ノイズ)
#
# 見せないファイル:
#   - services/payment_service.py (PROVIDER_TIMEOUT=300s) ← 矛盾の片方
#
# 条件A: gateway.py にアノテーションなし → LLM は payment_service が
#         gateway と同程度の timeout だと仮定しがち
# 条件B: gateway.py に「payment_service は 300s timeout」と明記
# ---------------------------------------------------------------------------

FILE_CONFIG = """\
# config/settings.py
import os
from dataclasses import dataclass

@dataclass
class DatabaseConfig:
    host: str = os.getenv("DB_HOST", "localhost")
    port: int = int(os.getenv("DB_PORT", "5432"))
    name: str = os.getenv("DB_NAME", "payments_db")
    pool_size: int = int(os.getenv("DB_POOL_SIZE", "10"))
    pool_timeout: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))

@dataclass
class RedisConfig:
    host: str = os.getenv("REDIS_HOST", "localhost")
    port: int = int(os.getenv("REDIS_PORT", "6379"))
    ttl: int = int(os.getenv("REDIS_TTL", "3600"))

@dataclass
class AppConfig:
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    db: DatabaseConfig = None
    redis: RedisConfig = None

    def __post_init__(self):
        self.db = self.db or DatabaseConfig()
        self.redis = self.redis or RedisConfig()

config = AppConfig()
"""

FILE_MODELS = """\
# models/payment.py
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
import uuid

class PaymentStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    TIMED_OUT = "timed_out"

@dataclass
class Payment:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str = ""
    amount: float = 0.0
    currency: str = "JPY"
    status: PaymentStatus = PaymentStatus.PENDING
    provider_tx_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    retry_count: int = 0
    error_message: Optional[str] = None
    idempotency_key: Optional[str] = None

    def is_terminal(self) -> bool:
        return self.status in (
            PaymentStatus.COMPLETED,
            PaymentStatus.FAILED,
            PaymentStatus.REFUNDED,
        )
"""

FILE_REPOSITORY = """\
# repositories/payment_repo.py
from typing import Optional, List
from models.payment import Payment, PaymentStatus
from db import session
import logging

logger = logging.getLogger(__name__)

class PaymentRepository:
    def create(self, payment: Payment) -> Payment:
        session.add(payment)
        session.commit()
        logger.info(f"Payment {payment.id} created for order {payment.order_id}")
        return payment

    def find_by_id(self, payment_id: str) -> Optional[Payment]:
        return session.query(Payment).filter_by(id=payment_id).first()

    def find_by_order(self, order_id: str) -> List[Payment]:
        return (session.query(Payment)
                .filter_by(order_id=order_id)
                .order_by(Payment.created_at.desc())
                .all())

    def find_by_idempotency_key(self, key: str) -> Optional[Payment]:
        return session.query(Payment).filter_by(idempotency_key=key).first()

    def update_status(self, payment_id: str, status: PaymentStatus,
                      provider_tx_id: str = None, error: str = None) -> Payment:
        payment = self.find_by_id(payment_id)
        if not payment:
            raise ValueError(f"Payment {payment_id} not found")
        payment.status = status
        if provider_tx_id:
            payment.provider_tx_id = provider_tx_id
        if error:
            payment.error_message = error
        session.commit()
        logger.info(f"Payment {payment_id} status updated to {status.value}")
        return payment
"""

FILE_MIDDLEWARE = """\
# middleware/auth.py
import hmac
import hashlib
from functools import wraps
import logging

logger = logging.getLogger(__name__)

API_KEYS = {}  # loaded from secure store at startup

def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

def require_auth(f):
    @wraps(f)
    def wrapper(request, *args, **kwargs):
        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key not in API_KEYS:
            return {"error": "Unauthorized"}, 401
        request.merchant_id = API_KEYS[api_key]["merchant_id"]
        return f(request, *args, **kwargs)
    return wrapper
"""

FILE_NOTIFICATIONS = """\
# services/notification_service.py
import logging

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url

    def notify_payment_success(self, payment_id: str, order_id: str,
                                amount: float, currency: str):
        logger.info(f"Payment {payment_id} succeeded: {amount} {currency}")
        self._send_webhook("payment.success", {
            "payment_id": payment_id, "order_id": order_id,
            "amount": amount, "currency": currency,
        })

    def notify_payment_failure(self, payment_id: str, order_id: str, error: str):
        logger.warning(f"Payment {payment_id} failed: {error}")
        self._send_webhook("payment.failed", {
            "payment_id": payment_id, "order_id": order_id, "error": error,
        })

    def _send_webhook(self, event: str, data: dict):
        if not self.webhook_url:
            return
        try:
            import requests
            requests.post(self.webhook_url, json={"event": event, "data": data},
                         timeout=5)
        except Exception as e:
            logger.error(f"Webhook failed: {e}")
"""

# gateway — 条件Aと条件Bで差分
FILE_GATEWAY_CONDITION_A = """\
# api/gateway.py
import logging
import requests
from middleware.auth import require_auth

logger = logging.getLogger(__name__)

PAYMENT_SERVICE_URL = "http://payment-service:8080"
GATEWAY_TIMEOUT = 30  # seconds — SLA: respond to merchant within 30s

@require_auth
def create_payment(request):
    \"\"\"Gateway endpoint for payment creation.

    Validates merchant request, forwards to internal payment service,
    and returns result within SLA timeout.
    \"\"\"
    data = request.json
    order_id = data.get("order_id")
    amount = data.get("amount")
    currency = data.get("currency", "JPY")
    idempotency_key = request.headers.get("Idempotency-Key")

    if not order_id or not amount:
        return {"error": "order_id and amount required"}, 400
    if amount <= 0:
        return {"error": "amount must be positive"}, 400

    try:
        resp = requests.post(
            f"{PAYMENT_SERVICE_URL}/internal/payments",
            json={
                "order_id": order_id,
                "amount": amount,
                "currency": currency,
                "merchant_id": request.merchant_id,
                "idempotency_key": idempotency_key,
            },
            timeout=GATEWAY_TIMEOUT,
            headers={"X-Request-ID": request.headers.get("X-Request-ID", "")},
        )
        return resp.json(), resp.status_code

    except requests.Timeout:
        logger.error(f"Payment service timeout for order {order_id} "
                     f"(timeout={GATEWAY_TIMEOUT}s)")
        return {
            "error": "Payment processing timeout. Check status before retrying.",
            "order_id": order_id,
            "status": "unknown",
        }, 504
"""

FILE_GATEWAY_CONDITION_B = """\
# api/gateway.py
import logging
import requests
from middleware.auth import require_auth

logger = logging.getLogger(__name__)

PAYMENT_SERVICE_URL = "http://payment-service:8080"
GATEWAY_TIMEOUT = 30  # seconds — SLA: respond to merchant within 30s

# ⚠ TIMEOUT MISMATCH: The downstream payment_service.py calls the
# external payment provider with timeout=300s. If the provider takes
# 31-300s (common for bank transfers), this gateway returns 504 while
# payment_service continues processing the charge successfully.
# Merchants receiving 504 may retry without idempotency key → DOUBLE CHARGE.

@require_auth
def create_payment(request):
    \"\"\"Gateway endpoint for payment creation.

    Validates merchant request, forwards to internal payment service,
    and returns result within SLA timeout.
    \"\"\"
    data = request.json
    order_id = data.get("order_id")
    amount = data.get("amount")
    currency = data.get("currency", "JPY")
    idempotency_key = request.headers.get("Idempotency-Key")

    if not order_id or not amount:
        return {"error": "order_id and amount required"}, 400
    if amount <= 0:
        return {"error": "amount must be positive"}, 400

    try:
        resp = requests.post(
            f"{PAYMENT_SERVICE_URL}/internal/payments",
            json={
                "order_id": order_id,
                "amount": amount,
                "currency": currency,
                "merchant_id": request.merchant_id,
                "idempotency_key": idempotency_key,
            },
            timeout=GATEWAY_TIMEOUT,
            headers={"X-Request-ID": request.headers.get("X-Request-ID", "")},
        )
        return resp.json(), resp.status_code

    except requests.Timeout:
        logger.error(f"Payment service timeout for order {order_id} "
                     f"(timeout={GATEWAY_TIMEOUT}s)")
        return {
            "error": "Payment processing timeout. Check status before retrying.",
            "order_id": order_id,
            "status": "unknown",
        }, 504
"""

FILE_INTERNAL_API = """\
# api/internal.py
import logging
from services.payment_service import process_payment

logger = logging.getLogger(__name__)

def handle_internal_payment(request):
    \"\"\"Internal API endpoint called by the gateway.

    No auth — only reachable within the service mesh.
    Delegates to payment_service.process_payment().
    \"\"\"
    data = request.json
    result = process_payment(
        order_id=data["order_id"],
        amount=data["amount"],
        currency=data.get("currency", "JPY"),
        merchant_id=data.get("merchant_id", ""),
        idempotency_key=data.get("idempotency_key"),
    )
    status_code = 200 if result["status"] != "failed" else 400
    return result, status_code
"""

# ---------------------------------------------------------------------------
# 推論タスク
#
# 質問は payment_service.py の挙動に依存するが、そのファイルは見せない。
# 条件A では LLM は gateway の timeout=30s が全体に適用されると仮定しがち。
# 条件B ではアノテーションで 300s timeout を知っている。
# ---------------------------------------------------------------------------

@dataclass
class Question:
    text: str
    choices: dict[str, str]
    correct: str

QUESTIONS = [
    Question(
        text=(
            "A merchant sends a payment request for a bank transfer. Bank transfers "
            "with the external provider typically take 45-120 seconds to process. "
            "The gateway has GATEWAY_TIMEOUT=30s. "
            "What is the MOST LIKELY outcome when the provider takes 60 seconds?"
        ),
        choices={
            "A": "The gateway returns the successful payment after 60 seconds",
            "B": "The gateway returns 504 after 30s, and the payment is cancelled "
                 "because the downstream service also times out around the same time",
            "C": "The gateway returns 504 after 30s, but the downstream payment service "
                 "continues processing and the payment eventually succeeds in the background",
            "D": "The gateway retries the request automatically after the first timeout",
        },
        correct="C",
    ),
    Question(
        text=(
            "After the gateway returns 504 (timeout) to the merchant, the merchant "
            "retries the same payment request WITHOUT an Idempotency-Key. "
            "What happens to the customer's account?"
        ),
        choices={
            "A": "Nothing — the first payment was cancelled when the gateway timed out, "
                 "so only the retry is processed",
            "B": "The customer is charged twice — once from the still-processing first "
                 "request and once from the retry",
            "C": "The payment service deduplicates by order_id and only charges once",
            "D": "The retry also times out at the gateway, so no charge occurs at all",
        },
        correct="B",
    ),
    Question(
        text=(
            "An SRE is investigating why some merchants report successful charges "
            "but received 504 errors from the gateway. What is the root cause?"
        ),
        choices={
            "A": "The gateway's error handling has a bug — it returns 504 even when "
                 "the payment service responded successfully",
            "B": "The payment service processes requests faster than the gateway timeout "
                 "but network latency causes the response to arrive late",
            "C": "The downstream payment service has a longer timeout than the gateway, "
                 "so it continues processing after the gateway gives up",
            "D": "The notification webhook is sending success notifications before "
                 "the payment is actually confirmed",
        },
        correct="C",
    ),
]

# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior backend engineer reviewing a payment processing system.
You are shown 7 of the system's source files. One file (services/payment_service.py)
is NOT included — you must reason about the system's behavior based on what you can see.

Answer the multiple-choice question by selecting exactly ONE letter (A, B, C, or D).
Output ONLY the letter of your answer, nothing else. No explanation."""

def build_user_prompt(condition: str, question: Question) -> str:
    gateway = FILE_GATEWAY_CONDITION_A if condition == "A" else FILE_GATEWAY_CONDITION_B
    choices_text = "\n".join(f"  {k}) {v}" for k, v in question.choices.items())

    return f"""\
You are reviewing a payment processing system. Here are the available source files
(note: services/payment_service.py is not shown):

---
{FILE_CONFIG}
---
{FILE_MODELS}
---
{FILE_MIDDLEWARE}
---
{gateway}
---
{FILE_INTERNAL_API}
---
{FILE_REPOSITORY}
---
{FILE_NOTIFICATIONS}
---

Question: {question.text}

{choices_text}

Answer (single letter):"""


# ---------------------------------------------------------------------------
# 回答抽出・採点
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
# 実験実行・結果（V2 と同一構造）
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
        if b == 0 or a == 0:
            return None
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
        """各選択肢の回答数."""
        dist: dict[str, int] = {}
        for t in self.trials:
            if t.condition == condition and t.question_idx == q_idx and t.extracted:
                dist[t.extracted] = dist.get(t.extracted, 0) + 1
        return dist


def run_experiment(n_trials: int = N_TRIALS, temperature: float = TEMPERATURE,
                   model: str = MODEL, verbose: bool = True) -> ExperimentResult:
    result = ExperimentResult()
    total_calls = n_trials * len(QUESTIONS) * 2
    call_count = 0

    for condition in ["A", "B"]:
        cond_label = ("δ>0 (no annotation, payment_service hidden)"
                      if condition == "A"
                      else "δ≈0 (annotated: timeout=300s)")
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


def print_results(result: ExperimentResult, n_trials: int = N_TRIALS,
                  model: str = MODEL, temperature: float = TEMPERATURE) -> None:
    print("\n" + "=" * 60)
    print("Phase -1 Pilot V3 Results: ④ Guard Non-Propagation × high")
    print("  (partial context: payment_service.py hidden)")
    print("=" * 60)

    print(f"\n設定: {n_trials} trials × {len(QUESTIONS)} questions × 2 conditions")
    print(f"モデル: {model}, temperature: {temperature}")
    print(f"方式: 部分コンテキスト（7/8 files shown, payment_service hidden）")

    for condition in ["A", "B"]:
        label = ("δ>0 (no annotation)" if condition == "A"
                 else "δ≈0 (timeout=300s annotated)")
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
    if i_nats is not None:
        print(f"  I(④, high) = -ln({acc_a:.3f} / {acc_b:.3f}) = {i_nats:.3f} nats")
        print(f"  e^{{-I}} = {math.exp(-i_nats):.3f}")
    else:
        print("  I(f): 計算不能 (acc=0)")

    if i_nats is not None:
        print(f"\n--- 解釈 ---")
        if i_nats > 0.05:
            print(f"  矛盾が不可視の状態で accuracy が {acc_b:.0%} → {acc_a:.0%} に低下")
            print(f"  情報損失 = {i_nats:.2f} nats")
            if i_nats > 0.5:
                print(f"  → XOR制約 (ln2=0.69) に匹敵する情報損失")
        elif abs(i_nats) <= 0.05:
            print(f"  差が小さい → この矛盾は部分コンテキストでも推論に影響しない")
        else:
            print(f"  逆転 → 実験設計の見直しが必要")

    print(f"\n--- 質問別分析 ---")
    for q_idx, q in enumerate(QUESTIONS):
        acc_a_q = result.acc_by_question("A").get(q_idx, 0)
        acc_b_q = result.acc_by_question("B").get(q_idx, 0)
        diff = acc_b_q - acc_a_q
        print(f"  Q{q_idx+1}: acc_A={acc_a_q:.0%} → acc_B={acc_b_q:.0%} (Δ={diff:+.0%})")


def save_results(result: ExperimentResult, path: Path,
                 n_trials: int = N_TRIALS, model: str = MODEL,
                 temperature: float = TEMPERATURE) -> None:
    data = {
        "experiment": "phase_minus1_pilot_v3",
        "pattern": "④ Guard Non-Propagation",
        "severity": "high",
        "scenario": "partial context — payment_service.py hidden, timeout asymmetry",
        "protocol": "partial_context",
        "context_files_shown": 7,
        "context_files_hidden": 1,
        "model": model,
        "temperature": temperature,
        "n_trials": n_trials,
        "n_questions": len(QUESTIONS),
        "acc_a": result.acc_a,
        "acc_b": result.acc_b,
        "i_nats": result.i_nats,
        "ci_a_95": list(result.confidence_interval_95("A")),
        "ci_b_95": list(result.confidence_interval_95("B")),
        "acc_by_question_a": {str(k): v for k, v in result.acc_by_question("A").items()},
        "acc_by_question_b": {str(k): v for k, v in result.acc_by_question("B").items()},
        "response_dist_a": {
            str(q): result.response_distribution("A", q)
            for q in range(len(QUESTIONS))
        },
        "response_dist_b": {
            str(q): result.response_distribution("B", q)
            for q in range(len(QUESTIONS))
        },
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


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Phase -1 Pilot V3: partial context protocol")
    parser.add_argument("--trials", type=int, default=N_TRIALS)
    parser.add_argument("--temperature", type=float, default=TEMPERATURE)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    n_trials = args.trials
    temperature = args.temperature
    model = args.model
    total_calls = n_trials * len(QUESTIONS) * 2

    print(f"Phase -1 Pilot V3 Experiment", file=sys.stderr)
    print(f"パターン: ④ Guard Non-Propagation × high", file=sys.stderr)
    print(f"方式: 部分コンテキスト（payment_service.py hidden）", file=sys.stderr)
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

    output_path = Path(args.output) if args.output else (
        Path(__file__).parent / "results" / "pilot_v3_result.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_results(result, output_path, n_trials=n_trials, model=model,
                 temperature=temperature)


if __name__ == "__main__":
    main()
