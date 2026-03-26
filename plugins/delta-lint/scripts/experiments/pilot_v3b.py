"""Phase -1 Pilot V3b: 部分コンテキスト × 反直感的実装.

V3 の問題: HTTP timeout の一般知識で正解できてしまう。
V3b の改善: hidden file の具体的実装を知らないと正解できないシナリオ。

シナリオ: ユーザー管理API
  visible: DELETE /users/:id → user_service.remove_user(id) → 204 No Content
  hidden:  user_service.remove_user() = ソフトデリート (is_active=False)
           + email に "_deleted_{timestamp}" suffix を追加

  「DELETE = 物理削除」は自然な前提だが、hidden file では soft delete。
  この仕様を知らないとDB状態・unique制約に関する質問で間違える。
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
# ファイル定義
# ---------------------------------------------------------------------------

FILE_MODELS = """\
# models/user.py
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
import uuid

@dataclass
class User:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    email: str = ""
    name: str = ""
    hashed_password: str = ""
    is_active: bool = True
    role: str = "member"
    team_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
"""

FILE_SCHEMA = """\
# db/schema.sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    role VARCHAR(50) NOT NULL DEFAULT 'member',
    team_id UUID REFERENCES teams(id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login TIMESTAMP
);

CREATE UNIQUE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_team ON users(team_id);
CREATE INDEX idx_users_active ON users(is_active);
"""

FILE_API_USERS_CONDITION_A = """\
# api/users.py
import logging
from middleware.auth import require_admin
from services.user_service import UserService

logger = logging.getLogger(__name__)
user_service = UserService()

def list_users(request):
    \"\"\"GET /users — List all users (admin only).\"\"\"
    team_id = request.args.get("team_id")
    users = user_service.list_active(team_id=team_id)
    return {"users": [u.to_dict() for u in users]}, 200

def get_user(request, user_id: str):
    \"\"\"GET /users/:id\"\"\"
    user = user_service.get_by_id(user_id)
    if not user:
        return {"error": "User not found"}, 404
    return user.to_dict(), 200

def create_user(request):
    \"\"\"POST /users — Create new user.\"\"\"
    data = request.json
    email = data.get("email", "").strip().lower()
    name = data.get("name", "").strip()

    if not email or not name:
        return {"error": "email and name are required"}, 400

    existing = user_service.get_by_email(email)
    if existing:
        return {"error": "A user with this email already exists"}, 409

    user = user_service.create(email=email, name=name, team_id=data.get("team_id"))
    logger.info(f"User created: {user.id} ({email})")
    return user.to_dict(), 201

def delete_user(request, user_id: str):
    \"\"\"DELETE /users/:id — Remove a user.

    Removes the user and frees their email for re-registration.
    Returns 204 on success, 404 if not found.
    \"\"\"
    user = user_service.get_by_id(user_id)
    if not user:
        return {"error": "User not found"}, 404

    user_service.remove_user(user_id)
    logger.info(f"User deleted: {user_id} ({user.email})")
    return "", 204

def update_user(request, user_id: str):
    \"\"\"PATCH /users/:id\"\"\"
    user = user_service.get_by_id(user_id)
    if not user:
        return {"error": "User not found"}, 404
    data = request.json
    user_service.update(user_id, name=data.get("name"), role=data.get("role"))
    return user_service.get_by_id(user_id).to_dict(), 200
"""

FILE_API_USERS_CONDITION_B = """\
# api/users.py
import logging
from middleware.auth import require_admin
from services.user_service import UserService

logger = logging.getLogger(__name__)
user_service = UserService()

# ⚠ SOFT DELETE: user_service.remove_user() does NOT physically delete
# the user record. It sets is_active=False and appends "_deleted_{timestamp}"
# to the email field. The record remains in the database.
# The UNIQUE constraint on email means the original email is "freed" by
# the suffix, allowing re-registration with the same email.
# However, the old record still exists and can be found by id or raw SQL.

def list_users(request):
    \"\"\"GET /users — List all users (admin only).\"\"\"
    team_id = request.args.get("team_id")
    users = user_service.list_active(team_id=team_id)
    return {"users": [u.to_dict() for u in users]}, 200

def get_user(request, user_id: str):
    \"\"\"GET /users/:id\"\"\"
    user = user_service.get_by_id(user_id)
    if not user:
        return {"error": "User not found"}, 404
    return user.to_dict(), 200

def create_user(request):
    \"\"\"POST /users — Create new user.\"\"\"
    data = request.json
    email = data.get("email", "").strip().lower()
    name = data.get("name", "").strip()

    if not email or not name:
        return {"error": "email and name are required"}, 400

    existing = user_service.get_by_email(email)
    if existing:
        return {"error": "A user with this email already exists"}, 409

    user = user_service.create(email=email, name=name, team_id=data.get("team_id"))
    logger.info(f"User created: {user.id} ({email})")
    return user.to_dict(), 201

def delete_user(request, user_id: str):
    \"\"\"DELETE /users/:id — Remove a user.

    Removes the user and frees their email for re-registration.
    Returns 204 on success, 404 if not found.
    \"\"\"
    user = user_service.get_by_id(user_id)
    if not user:
        return {"error": "User not found"}, 404

    user_service.remove_user(user_id)
    logger.info(f"User deleted: {user_id} ({user.email})")
    return "", 204

def update_user(request, user_id: str):
    \"\"\"PATCH /users/:id\"\"\"
    user = user_service.get_by_id(user_id)
    if not user:
        return {"error": "User not found"}, 404
    data = request.json
    user_service.update(user_id, name=data.get("name"), role=data.get("role"))
    return user_service.get_by_id(user_id).to_dict(), 200
"""

FILE_REPOSITORY = """\
# repositories/user_repo.py
from typing import Optional, List
from models.user import User
from db import session
import logging

logger = logging.getLogger(__name__)

class UserRepository:
    def create(self, user: User) -> User:
        session.add(user)
        session.commit()
        return user

    def find_by_id(self, user_id: str) -> Optional[User]:
        return session.query(User).filter_by(id=user_id).first()

    def find_by_email(self, email: str) -> Optional[User]:
        return session.query(User).filter_by(email=email, is_active=True).first()

    def find_active(self, team_id: str = None) -> List[User]:
        q = session.query(User).filter_by(is_active=True)
        if team_id:
            q = q.filter_by(team_id=team_id)
        return q.order_by(User.created_at.desc()).all()

    def update(self, user: User) -> User:
        session.merge(user)
        session.commit()
        return user

    def delete(self, user_id: str) -> bool:
        user = self.find_by_id(user_id)
        if not user:
            return False
        session.delete(user)
        session.commit()
        return True

    def count_active(self, team_id: str = None) -> int:
        q = session.query(User).filter_by(is_active=True)
        if team_id:
            q = q.filter_by(team_id=team_id)
        return q.count()
"""

FILE_MIDDLEWARE = """\
# middleware/auth.py
import jwt
import logging
from functools import wraps
from config import JWT_SECRET

logger = logging.getLogger(__name__)

def require_auth(f):
    @wraps(f)
    def wrapper(request, *args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return {"error": "Authentication required"}, 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            request.user_id = payload["sub"]
            request.user_role = payload.get("role", "member")
        except jwt.InvalidTokenError:
            return {"error": "Invalid token"}, 401
        return f(request, *args, **kwargs)
    return wrapper

def require_admin(f):
    @wraps(f)
    @require_auth
    def wrapper(request, *args, **kwargs):
        if request.user_role != "admin":
            return {"error": "Admin access required"}, 403
        return f(request, *args, **kwargs)
    return wrapper
"""

FILE_API_TEAMS = """\
# api/teams.py
import logging
from services.team_service import TeamService

logger = logging.getLogger(__name__)
team_service = TeamService()

def list_teams(request):
    \"\"\"GET /teams\"\"\"
    return {"teams": [t.to_dict() for t in team_service.list_all()]}, 200

def get_team(request, team_id: str):
    \"\"\"GET /teams/:id — includes member count.\"\"\"
    team = team_service.get_by_id(team_id)
    if not team:
        return {"error": "Team not found"}, 404
    member_count = team_service.count_members(team_id)
    result = team.to_dict()
    result["member_count"] = member_count
    return result, 200

def delete_team(request, team_id: str):
    \"\"\"DELETE /teams/:id — cascades to remove all team members.\"\"\"
    team = team_service.get_by_id(team_id)
    if not team:
        return {"error": "Team not found"}, 404
    team_service.delete_team_and_members(team_id)
    logger.info(f"Team deleted: {team_id}")
    return "", 204
"""

FILE_AUDIT = """\
# services/audit_service.py
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class AuditService:
    def log_action(self, actor_id: str, action: str, target_type: str,
                   target_id: str, details: dict = None):
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "actor_id": actor_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "details": details or {},
        }
        logger.info(f"AUDIT: {action} {target_type}/{target_id} by {actor_id}")
        # In production: write to audit_log table
        return entry

    def get_user_actions(self, user_id: str, limit: int = 50) -> list:
        # Query audit_log table for actions by or on this user
        pass
"""

# ---------------------------------------------------------------------------
# 質問
# ---------------------------------------------------------------------------

@dataclass
class Question:
    text: str
    choices: dict[str, str]
    correct: str

QUESTIONS = [
    Question(
        text=(
            "An admin calls DELETE /users/abc-123 for a user with email 'john@example.com'. "
            "The API returns 204. An analyst then runs a direct database query: "
            "SELECT COUNT(*) FROM users WHERE id = 'abc-123'. "
            "What does the query return?"
        ),
        choices={
            "A": "0 — the user record was deleted from the database",
            "B": "1 — the record still exists but with is_active=FALSE",
            "C": "An error — the users table has been modified and the record is in an archive table",
            "D": "0 — but the record exists in a separate deleted_users audit table",
        },
        correct="B",
    ),
    Question(
        text=(
            "After deleting user 'john@example.com' via DELETE /users/:id, "
            "a new employee joins and needs to register with the same email "
            "'john@example.com'. They call POST /users with that email. "
            "What happens?"
        ),
        choices={
            "A": "Success (201) — the old email was freed when the user was deleted",
            "B": "Conflict (409) — 'A user with this email already exists' because "
                 "the old record's email still occupies the UNIQUE index",
            "C": "Success (201) — but it overwrites the old user record instead of creating a new one",
            "D": "Error (500) — database constraint violation on the UNIQUE email index",
        },
        correct="A",
    ),
    Question(
        text=(
            "The compliance team needs to purge all personal data for user abc-123 "
            "(GDPR 'right to erasure'). An admin calls DELETE /users/abc-123. "
            "Is the system now GDPR-compliant for this user's data?"
        ),
        choices={
            "A": "Yes — the DELETE endpoint removed the user and all their personal data from the database",
            "B": "No — the user record (including email, name) still exists in the database "
                 "with is_active=FALSE; a physical DELETE or data anonymization is needed",
            "C": "Partially — the user record is gone but audit logs still contain their user_id",
            "D": "Yes — the database UNIQUE constraint ensures no trace of the email remains",
        },
        correct="B",
    ),
]

# ---------------------------------------------------------------------------
# プロンプト
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior backend engineer reviewing a user management system.
You are shown source files but services/user_service.py is NOT included.
You must reason about system behavior based on what you can see.

Answer the multiple-choice question by selecting exactly ONE letter (A, B, C, or D).
Output ONLY the letter of your answer, nothing else."""

def build_user_prompt(condition: str, question: Question) -> str:
    api_users = FILE_API_USERS_CONDITION_A if condition == "A" else FILE_API_USERS_CONDITION_B
    choices_text = "\n".join(f"  {k}) {v}" for k, v in question.choices.items())

    return f"""\
You are reviewing a user management system. The following source files are available
(note: services/user_service.py is NOT shown):

---
{FILE_MODELS}
---
{FILE_SCHEMA}
---
{api_users}
---
{FILE_REPOSITORY}
---
{FILE_MIDDLEWARE}
---
{FILE_API_TEAMS}
---
{FILE_AUDIT}
---

Question: {question.text}

{choices_text}

Answer (single letter):"""


# ---------------------------------------------------------------------------
# 実験インフラ（V3 と同一）
# ---------------------------------------------------------------------------

def extract_answer(response: str) -> str | None:
    text = response.strip()
    if len(text) == 1 and text.upper() in "ABCD":
        return text.upper()
    m = re.search(r'\b([A-D])\b', text)
    if m:
        return m.group(1).upper()
    return None

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
        cond_label = ("δ>0 (no annotation, user_service hidden)"
                      if condition == "A"
                      else "δ≈0 (soft delete annotated)")
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


def print_results(result: ExperimentResult, n_trials: int = N_TRIALS,
                  model: str = MODEL, temperature: float = TEMPERATURE) -> None:
    print("\n" + "=" * 60)
    print("Phase -1 Pilot V3b: Soft Delete (user_service hidden)")
    print("=" * 60)

    print(f"\n設定: {n_trials} trials × {len(QUESTIONS)} questions × 2 conditions")
    print(f"モデル: {model}, temperature: {temperature}")
    print(f"方式: 部分コンテキスト（user_service.py hidden）")

    for condition in ["A", "B"]:
        label = ("δ>0 (no annotation)" if condition == "A"
                 else "δ≈0 (soft delete annotated)")
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
        print(f"  I(f) = -ln({acc_a:.3f} / {acc_b:.3f}) = {i_nats:.3f} nats")
        print(f"  e^{{-I}} = {math.exp(-i_nats):.3f}")
    else:
        print("  I(f): 計算不能")

    if i_nats is not None and i_nats > 0.05:
        print(f"\n--- 解釈 ---")
        print(f"  soft delete の暗黙性が {i_nats:.2f} nats の情報損失を生成")

    print(f"\n--- 質問別分析 ---")
    for q_idx, q in enumerate(QUESTIONS):
        acc_a_q = result.acc_by_question("A").get(q_idx, 0)
        acc_b_q = result.acc_by_question("B").get(q_idx, 0)
        diff = acc_b_q - acc_a_q
        print(f"  Q{q_idx+1}: acc_A={acc_a_q:.0%} → acc_B={acc_b_q:.0%} (Δ={diff:+.0%})")
        if abs(diff) > 0.2:
            print(f"       ^ アノテーション効果大")


def save_results(result: ExperimentResult, path: Path,
                 n_trials: int = N_TRIALS, model: str = MODEL,
                 temperature: float = TEMPERATURE) -> None:
    data = {
        "experiment": "phase_minus1_pilot_v3b",
        "pattern": "④ Guard Non-Propagation (soft delete variant)",
        "severity": "high",
        "scenario": "soft delete — visible DELETE endpoint, hidden soft-delete implementation",
        "protocol": "partial_context",
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
            str(q): result.response_distribution("A", q) for q in range(len(QUESTIONS))
        },
        "response_dist_b": {
            str(q): result.response_distribution("B", q) for q in range(len(QUESTIONS))
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
        description="Phase -1 V3b: soft delete partial context")
    parser.add_argument("--trials", type=int, default=N_TRIALS)
    parser.add_argument("--temperature", type=float, default=TEMPERATURE)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    n_trials = args.trials
    temperature = args.temperature
    model = args.model

    print(f"Phase -1 Pilot V3b: Soft Delete", file=sys.stderr)
    print(f"設計: {n_trials} trials × {len(QUESTIONS)} Q × 2 cond "
          f"= {n_trials * len(QUESTIONS) * 2} calls", file=sys.stderr)
    print(f"モデル: {model}, temp: {temperature}", file=sys.stderr)

    start = time.time()
    result = run_experiment(n_trials=n_trials, temperature=temperature,
                            model=model, verbose=not args.quiet)
    elapsed = time.time() - start

    print_results(result, n_trials=n_trials, model=model, temperature=temperature)
    print(f"\n所要時間: {elapsed:.0f}秒 ({elapsed/60:.1f}分)")

    output_path = Path(args.output) if args.output else (
        Path(__file__).parent / "results" / "pilot_v3b_result.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_results(result, output_path, n_trials=n_trials, model=model,
                 temperature=temperature)


if __name__ == "__main__":
    main()
