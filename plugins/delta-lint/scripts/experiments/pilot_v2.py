"""Phase -1 Pilot V2: ④ Guard Non-Propagation × high — コンテキストノイズ版.

V1 の結果: 3ファイルだと Sonnet が矛盾を100%見抜く → I(f) = 0 nats
V2 の改善:
  - 8ファイルに増加（ノイズ5ファイル + 矛盾を含む3ファイル）
  - 矛盾をより微妙に: timeout 値の非対称（30s vs 300s）
  - 質問を間接的に: 矛盾の存在を直接問わず、システム挙動の推論を要求
  - ファイルを長めに: 各100行前後（実際のコードベースに近い）

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
# 決済処理システム。API gateway → payment service → external payment provider.
# API gateway で timeout=30s を設定。payment service 内部で外部API呼び出しに
# timeout=300s を設定（矛盾: gateway が先にタイムアウトしてリトライする間、
# payment が継続して二重課金になる）。
#
# この矛盾は 8 ファイルの中に埋もれている。
# ---------------------------------------------------------------------------

# --- ノイズファイル（矛盾に関係ない） ---

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
import time
import logging
from functools import wraps

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

        signature = request.headers.get("X-Signature")
        if signature:
            if not verify_signature(request.body, signature, API_KEYS[api_key]):
                return {"error": "Invalid signature"}, 403

        request.merchant_id = API_KEYS[api_key]["merchant_id"]
        return f(request, *args, **kwargs)
    return wrapper
"""

FILE_NOTIFICATIONS = """\
# services/notification_service.py
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, webhook_url: str = None, email_service=None):
        self.webhook_url = webhook_url
        self.email_service = email_service

    def notify_payment_success(self, payment_id: str, order_id: str,
                                amount: float, currency: str):
        logger.info(f"Payment {payment_id} succeeded: {amount} {currency}")
        self._send_webhook("payment.success", {
            "payment_id": payment_id,
            "order_id": order_id,
            "amount": amount,
            "currency": currency,
        })

    def notify_payment_failure(self, payment_id: str, order_id: str,
                                error: str):
        logger.warning(f"Payment {payment_id} failed: {error}")
        self._send_webhook("payment.failed", {
            "payment_id": payment_id,
            "order_id": order_id,
            "error": error,
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

# --- 矛盾を含むファイル群 ---

FILE_GATEWAY_CONDITION_A = """\
# api/gateway.py
import logging
import requests
from middleware.auth import require_auth
from config.settings import config

logger = logging.getLogger(__name__)

PAYMENT_SERVICE_URL = "http://payment-service:8080"
GATEWAY_TIMEOUT = 30  # seconds — SLA: respond to merchant within 30s

@require_auth
def create_payment(request):
    \"\"\"Gateway endpoint for payment creation.

    Validates merchant request, forwards to payment service,
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
from config.settings import config

logger = logging.getLogger(__name__)

PAYMENT_SERVICE_URL = "http://payment-service:8080"
GATEWAY_TIMEOUT = 30  # seconds — SLA: respond to merchant within 30s

# ⚠ TIMEOUT INCONSISTENCY: This gateway times out at 30s, but the
# downstream payment_service.py calls the external provider with
# timeout=300s (see payment_service.py:process_payment).
# If the provider takes 31-300s, this gateway returns 504 while
# payment_service continues processing → merchant may retry →
# potential DOUBLE CHARGE. The idempotency_key mitigates this only
# if the merchant sends the same key on retry.

@require_auth
def create_payment(request):
    \"\"\"Gateway endpoint for payment creation.

    Validates merchant request, forwards to payment service,
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

FILE_PAYMENT_SERVICE = """\
# services/payment_service.py
import logging
import requests
from models.payment import Payment, PaymentStatus
from repositories.payment_repo import PaymentRepository
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)

PROVIDER_API_URL = "https://api.payment-provider.com/v2"
PROVIDER_API_KEY = "pk_live_xxx"  # loaded from env in production
PROVIDER_TIMEOUT = 300  # seconds — provider may take long for bank transfers

repo = PaymentRepository()
notifier = NotificationService()

def process_payment(order_id: str, amount: float, currency: str,
                    merchant_id: str, idempotency_key: str = None) -> dict:
    \"\"\"Core payment processing logic.

    Called by internal API (from gateway) and async job processor.
    Handles idempotency, provider communication, and status updates.
    \"\"\"
    # Idempotency check
    if idempotency_key:
        existing = repo.find_by_idempotency_key(idempotency_key)
        if existing:
            logger.info(f"Idempotent hit for key {idempotency_key}")
            return _payment_to_response(existing)

    # Create payment record
    payment = Payment(
        order_id=order_id,
        amount=amount,
        currency=currency,
        idempotency_key=idempotency_key,
    )
    repo.create(payment)

    # Call external provider
    try:
        provider_resp = requests.post(
            f"{PROVIDER_API_URL}/charges",
            json={
                "amount": int(amount),
                "currency": currency.lower(),
                "metadata": {"order_id": order_id, "merchant_id": merchant_id},
            },
            headers={"Authorization": f"Bearer {PROVIDER_API_KEY}"},
            timeout=PROVIDER_TIMEOUT,
        )

        if provider_resp.status_code == 200:
            tx_data = provider_resp.json()
            repo.update_status(
                payment.id, PaymentStatus.COMPLETED,
                provider_tx_id=tx_data.get("transaction_id"),
            )
            notifier.notify_payment_success(
                payment.id, order_id, amount, currency)
            payment.status = PaymentStatus.COMPLETED
        else:
            error_msg = provider_resp.json().get("error", "Unknown provider error")
            repo.update_status(
                payment.id, PaymentStatus.FAILED, error=error_msg)
            notifier.notify_payment_failure(payment.id, order_id, error_msg)
            payment.status = PaymentStatus.FAILED

    except requests.Timeout:
        logger.error(f"Provider timeout for payment {payment.id} "
                     f"(timeout={PROVIDER_TIMEOUT}s)")
        repo.update_status(
            payment.id, PaymentStatus.TIMED_OUT,
            error=f"Provider timeout after {PROVIDER_TIMEOUT}s")
        payment.status = PaymentStatus.TIMED_OUT

    except requests.RequestException as e:
        logger.error(f"Provider error for payment {payment.id}: {e}")
        repo.update_status(
            payment.id, PaymentStatus.FAILED, error=str(e))
        payment.status = PaymentStatus.FAILED

    return _payment_to_response(payment)


def _payment_to_response(payment: Payment) -> dict:
    return {
        "payment_id": payment.id,
        "order_id": payment.order_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status.value,
        "provider_tx_id": payment.provider_tx_id,
    }
"""

FILE_INTERNAL_API = """\
# api/internal.py
import logging
from services.payment_service import process_payment

logger = logging.getLogger(__name__)

def handle_internal_payment(request):
    \"\"\"Internal API endpoint called by the gateway.

    No auth check — only reachable within the service mesh.
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
# 推論タスク（多肢選択式）
# ---------------------------------------------------------------------------

@dataclass
class Question:
    text: str
    choices: dict[str, str]
    correct: str

QUESTIONS = [
    Question(
        text=(
            "A merchant calls create_payment via the gateway. The external payment "
            "provider takes 45 seconds to process a bank transfer. "
            "What is the outcome for this transaction?"
        ),
        choices={
            "A": "The gateway waits 45s and returns the successful payment response",
            "B": "The gateway returns 504 after 30s, but the payment completes "
                 "successfully in payment_service (provider finishes at 45s)",
            "C": "Both the gateway and payment_service time out, and the payment fails",
            "D": "The payment_service detects the gateway disconnection and cancels "
                 "the provider request",
        },
        correct="B",
    ),
    Question(
        text=(
            "After the scenario above (provider took 45s), the merchant receives "
            "a 504 timeout from the gateway and retries the same payment WITHOUT "
            "an Idempotency-Key. What happens?"
        ),
        choices={
            "A": "The retry is deduplicated by order_id — only one charge occurs",
            "B": "A second payment record is created and a second charge is sent "
                 "to the provider — the customer is charged twice",
            "C": "The retry fails because the first payment is still PROCESSING",
            "D": "payment_service detects the duplicate via the provider's "
                 "fraud detection and rejects the second charge",
        },
        correct="B",
    ),
    Question(
        text=(
            "The SRE team wants to prevent double charges caused by gateway "
            "timeouts. Which single change would be MOST effective?"
        ),
        choices={
            "A": "Increase PROVIDER_TIMEOUT in payment_service from 300s to 600s",
            "B": "Decrease PROVIDER_TIMEOUT in payment_service to match "
                 "GATEWAY_TIMEOUT (30s), or increase GATEWAY_TIMEOUT to match "
                 "PROVIDER_TIMEOUT",
            "C": "Add retry logic with exponential backoff in the gateway",
            "D": "Add a database unique constraint on order_id in the payments table",
        },
        correct="B",
    ),
]

# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior software engineer analyzing a payment processing system.
You are reviewing 8 source files to answer questions about system behavior.
Answer the multiple-choice question by selecting exactly ONE letter (A, B, C, or D).
Output ONLY the letter of your answer, nothing else. No explanation."""

def build_user_prompt(condition: str, question: Question) -> str:
    gateway = FILE_GATEWAY_CONDITION_A if condition == "A" else FILE_GATEWAY_CONDITION_B

    choices_text = "\n".join(f"  {k}) {v}" for k, v in question.choices.items())

    return f"""\
You are reviewing a payment processing system. Here are the source files:

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
{FILE_PAYMENT_SERVICE}
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
# 実験実行
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


def run_experiment(n_trials: int = N_TRIALS, temperature: float = TEMPERATURE,
                   model: str = MODEL, verbose: bool = True) -> ExperimentResult:
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


# ---------------------------------------------------------------------------
# 結果表示
# ---------------------------------------------------------------------------

def print_results(result: ExperimentResult, n_trials: int = N_TRIALS,
                  model: str = MODEL, temperature: float = TEMPERATURE) -> None:
    print("\n" + "=" * 60)
    print("Phase -1 Pilot V2 Results: ④ Guard Non-Propagation × high")
    print("  (timeout asymmetry: 30s gateway vs 300s provider)")
    print("=" * 60)

    print(f"\n設定: {n_trials} trials × {len(QUESTIONS)} questions × 2 conditions")
    print(f"モデル: {model}, temperature: {temperature}")
    print(f"コンテキスト: 8 files (5 noise + 3 relevant)")

    for condition in ["A", "B"]:
        label = "δ>0 (implicit)" if condition == "A" else "δ≈0 (annotated)"
        ci = result.confidence_interval_95(condition)
        acc = result.acc_a if condition == "A" else result.acc_b
        by_q = result.acc_by_question(condition)

        print(f"\n--- 条件 {condition}: {label} ---")
        print(f"  全体 accuracy: {acc:.1%}  (95% CI: [{ci[0]:.1%}, {ci[1]:.1%}])")
        for q_idx in sorted(by_q):
            print(f"  Q{q_idx+1}: {by_q[q_idx]:.0%} ({int(by_q[q_idx]*n_trials)}/{n_trials})")

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

    if i_nats is not None:
        print(f"\n--- 解釈 ---")
        if i_nats > 0.05:
            print(f"  アノテーションにより accuracy が {acc_a:.0%} → {acc_b:.0%} に向上")
            print(f"  矛盾の暗黙存在が {i_nats:.2f} nats の情報損失を生んでいる")
            if i_nats > 0.5:
                print(f"  → XOR制約 (ln2=0.69) に匹敵する重大な情報損失")
        elif abs(i_nats) <= 0.05:
            print(f"  差が小さい (|I| < 0.05 nats) → 実質 δ ≈ 0")
        else:
            print(f"  acc_A > acc_B (逆転) → 実験設計の見直しが必要")

    # 質問ごとの分析
    print(f"\n--- 質問別分析 ---")
    for q_idx, q in enumerate(QUESTIONS):
        acc_a_q = result.acc_by_question("A").get(q_idx, 0)
        acc_b_q = result.acc_by_question("B").get(q_idx, 0)
        diff = acc_b_q - acc_a_q
        print(f"  Q{q_idx+1}: acc_A={acc_a_q:.0%} → acc_B={acc_b_q:.0%} (Δ={diff:+.0%})")
        if diff > 0.2:
            print(f"       ^ この質問でアノテーション効果が大きい")


def save_results(result: ExperimentResult, path: Path,
                 n_trials: int = N_TRIALS, model: str = MODEL,
                 temperature: float = TEMPERATURE) -> None:
    data = {
        "experiment": "phase_minus1_pilot_v2",
        "pattern": "④ Guard Non-Propagation",
        "severity": "high",
        "scenario": "timeout asymmetry (30s gateway vs 300s provider)",
        "context_files": 8,
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
        description="Phase -1 Pilot V2: timeout asymmetry scenario")
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

    print(f"Phase -1 Pilot V2 Experiment", file=sys.stderr)
    print(f"パターン: ④ Guard Non-Propagation × high", file=sys.stderr)
    print(f"シナリオ: timeout 非対称 (gateway=30s vs provider=300s)", file=sys.stderr)
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
        Path(__file__).parent / "results" / "pilot_v2_result.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_results(result, output_path, n_trials=n_trials, model=model,
                 temperature=temperature)


if __name__ == "__main__":
    main()
