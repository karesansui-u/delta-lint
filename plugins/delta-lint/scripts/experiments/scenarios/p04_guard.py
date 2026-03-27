"""④ Guard Non-Propagation — 3重大度シナリオ.

high:   soft delete（V3b 移植）— DELETE が物理削除でなく論理削除
medium: 権限チェックが API 層のみで service 層にない → 内部呼び出しでバイパス
low:    入力トリムが片方のエンドポイントにしかない
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ④ × high — Soft Delete (V3b 移植)
# =====================================================================

_FILE_MODELS = """\
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

_FILE_SCHEMA = """\
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

_FILE_API_A = """\
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

_FILE_API_B = """\
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

_FILE_REPO = """\
# repositories/user_repo.py
from typing import Optional, List
from models.user import User
from db import session

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
"""

_FILE_MIDDLEWARE = """\
# middleware/auth.py
import jwt
from functools import wraps
from config import JWT_SECRET

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

_FILE_TEAMS = """\
# api/teams.py
from services.team_service import TeamService

team_service = TeamService()

def list_teams(request):
    return {"teams": [t.to_dict() for t in team_service.list_all()]}, 200

def delete_team(request, team_id: str):
    \"\"\"DELETE /teams/:id — cascades to remove all team members.\"\"\"
    team = team_service.get_by_id(team_id)
    if not team:
        return {"error": "Team not found"}, 404
    team_service.delete_team_and_members(team_id)
    return "", 204
"""

_FILE_AUDIT = """\
# services/audit_service.py
from datetime import datetime

class AuditService:
    def log_action(self, actor_id, action, target_type, target_id, details=None):
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "actor_id": actor_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "details": details or {},
        }
        return entry
"""

P04_HIGH = Scenario(
    pattern="④",
    pattern_name="Guard Non-Propagation",
    severity="high",
    description="DELETE endpoint performs soft delete (is_active=False + email suffix) but API docs say 'remove'",
    visible_files={
        "models/user.py": _FILE_MODELS,
        "db/schema.sql": _FILE_SCHEMA,
        "api/users.py": _FILE_API_A,
        "repositories/user_repo.py": _FILE_REPO,
        "middleware/auth.py": _FILE_MIDDLEWARE,
        "api/teams.py": _FILE_TEAMS,
        "services/audit_service.py": _FILE_AUDIT,
    },
    annotated_files={
        "api/users.py": _FILE_API_B,
    },
    hidden_file_name="services/user_service.py",
    hidden_file_description="Implements soft delete: sets is_active=False, appends _deleted_{ts} to email",
    questions=[
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
    ],
)


# =====================================================================
# ④ × medium — 権限チェックが API 層のみ
# =====================================================================

_MED_API_A = """\
# api/orders.py
import logging
from middleware.auth import require_auth
from services.order_service import OrderService

logger = logging.getLogger(__name__)
order_service = OrderService()

@require_auth
def cancel_order(request, order_id: str):
    \"\"\"POST /orders/:id/cancel — Cancel an order.

    Only the order owner or an admin can cancel.
    Returns the updated order with status='cancelled'.
    \"\"\"
    order = order_service.get_by_id(order_id)
    if not order:
        return {"error": "Order not found"}, 404

    # Permission check: owner or admin only
    if order.user_id != request.user_id and request.user_role != "admin":
        return {"error": "You can only cancel your own orders"}, 403

    result = order_service.cancel(order_id)
    logger.info(f"Order {order_id} cancelled by {request.user_id}")
    return result.to_dict(), 200

@require_auth
def refund_order(request, order_id: str):
    \"\"\"POST /orders/:id/refund — Refund a cancelled order (admin only).\"\"\"
    if request.user_role != "admin":
        return {"error": "Admin access required"}, 403

    order = order_service.get_by_id(order_id)
    if not order:
        return {"error": "Order not found"}, 404

    result = order_service.refund(order_id)
    return result.to_dict(), 200
"""

_MED_API_B = """\
# api/orders.py
import logging
from middleware.auth import require_auth
from services.order_service import OrderService

logger = logging.getLogger(__name__)
order_service = OrderService()

# ⚠ GUARD GAP: order_service.cancel() does NOT check permissions internally.
# It trusts the caller to verify ownership. If cancel() is called from
# background jobs (e.g., cron_expire_orders.py) or internal services,
# it will cancel ANY order regardless of who owns it.
# The permission check exists ONLY in this API layer.

@require_auth
def cancel_order(request, order_id: str):
    \"\"\"POST /orders/:id/cancel — Cancel an order.

    Only the order owner or an admin can cancel.
    Returns the updated order with status='cancelled'.
    \"\"\"
    order = order_service.get_by_id(order_id)
    if not order:
        return {"error": "Order not found"}, 404

    # Permission check: owner or admin only
    if order.user_id != request.user_id and request.user_role != "admin":
        return {"error": "You can only cancel your own orders"}, 403

    result = order_service.cancel(order_id)
    logger.info(f"Order {order_id} cancelled by {request.user_id}")
    return result.to_dict(), 200

@require_auth
def refund_order(request, order_id: str):
    \"\"\"POST /orders/:id/refund — Refund a cancelled order (admin only).\"\"\"
    if request.user_role != "admin":
        return {"error": "Admin access required"}, 403

    order = order_service.get_by_id(order_id)
    if not order:
        return {"error": "Order not found"}, 404

    result = order_service.refund(order_id)
    return result.to_dict(), 200
"""

_MED_MODEL = """\
# models/order.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

@dataclass
class Order:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    status: str = "pending"  # pending, confirmed, shipped, cancelled, refunded
    total_amount: float = 0.0
    items: list = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[str] = None
"""

_MED_CRON = """\
# jobs/cron_expire_orders.py
\"\"\"Nightly job: cancel orders that have been pending for > 7 days.\"\"\"
import logging
from datetime import datetime, timedelta
from services.order_service import OrderService

logger = logging.getLogger(__name__)

def run():
    order_service = OrderService()
    cutoff = datetime.utcnow() - timedelta(days=7)
    stale = order_service.find_pending_before(cutoff)
    for order in stale:
        order_service.cancel(order.id)
        logger.info(f"Auto-expired order {order.id} (created {order.created_at})")
    logger.info(f"Expired {len(stale)} stale orders")
"""

_MED_WEBHOOK = """\
# integrations/payment_webhook.py
\"\"\"Handle payment provider webhooks.\"\"\"
import logging
from services.order_service import OrderService

logger = logging.getLogger(__name__)

def handle_payment_failed(payload: dict):
    order_service = OrderService()
    order_id = payload.get("order_id")
    if not order_id:
        logger.warning("Payment failed webhook missing order_id")
        return
    order_service.cancel(order_id)
    logger.info(f"Order {order_id} cancelled due to payment failure")
"""

_MED_SCHEMA = """\
# db/schema.sql
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    total_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    cancelled_at TIMESTAMP,
    cancelled_by UUID
);

CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);
"""

P04_MEDIUM = Scenario(
    pattern="④",
    pattern_name="Guard Non-Propagation",
    severity="medium",
    description="Permission check exists only in API layer; service layer trusts caller blindly",
    visible_files={
        "api/orders.py": _MED_API_A,
        "models/order.py": _MED_MODEL,
        "jobs/cron_expire_orders.py": _MED_CRON,
        "integrations/payment_webhook.py": _MED_WEBHOOK,
        "db/schema.sql": _MED_SCHEMA,
    },
    annotated_files={
        "api/orders.py": _MED_API_B,
    },
    hidden_file_name="services/order_service.py",
    hidden_file_description="cancel() sets status='cancelled' without any ownership check",
    questions=[
        Question(
            text=(
                "The cron job cron_expire_orders.py runs nightly and calls "
                "order_service.cancel() for stale orders. A confirmed order belonging "
                "to user X has been pending for 8 days. Can the cron job cancel it "
                "even though user X didn't request cancellation?"
            ),
            choices={
                "A": "No — order_service.cancel() checks that the caller has permission, "
                     "and the cron job doesn't provide user credentials",
                "B": "Yes — order_service.cancel() only changes the status field without "
                     "checking who is requesting the cancellation",
                "C": "No — the cron job only processes orders with status='pending', "
                     "and this order is 'confirmed'",
                "D": "Yes — but it will be logged as cancelled_by=None, triggering an alert",
            },
            correct="B",
        ),
        Question(
            text=(
                "A payment webhook calls order_service.cancel() when a payment fails. "
                "An attacker crafts a fake webhook payload with another user's order_id. "
                "Assuming the webhook endpoint lacks signature verification, "
                "what happens to the victim's order?"
            ),
            choices={
                "A": "Nothing — order_service.cancel() verifies that the cancellation "
                     "request comes from the order owner",
                "B": "The order is cancelled — order_service.cancel() does not verify "
                     "ownership and will cancel any order given a valid ID",
                "C": "An error is raised — the order status doesn't allow cancellation "
                     "from a webhook context",
                "D": "The order is flagged for review but not cancelled",
            },
            correct="B",
        ),
        Question(
            text=(
                "A developer adds a new internal endpoint POST /admin/bulk-cancel that "
                "calls order_service.cancel() in a loop for a list of order IDs. "
                "The endpoint requires admin auth. Is this implementation secure?"
            ),
            choices={
                "A": "Yes — admin auth on the endpoint plus the service layer's own "
                     "permission check provides defense in depth",
                "B": "Mostly secure — admin auth is sufficient since order_service.cancel() "
                     "doesn't add its own permission check, but admin should be trusted",
                "C": "No — if the admin endpoint is later exposed to non-admin users "
                     "(e.g., via a bug), there is no safety net in the service layer",
                "D": "Yes — the database foreign key constraints prevent unauthorized cancellation",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ④ × low — 入力トリムが片方だけ
# =====================================================================

_LOW_API_A = """\
# api/products.py
import logging
from services.product_service import ProductService

logger = logging.getLogger(__name__)
product_service = ProductService()

def create_product(request):
    \"\"\"POST /products — Create a new product listing.\"\"\"
    data = request.json
    name = data.get("name", "").strip()
    sku = data.get("sku", "").strip().upper()

    if not name or not sku:
        return {"error": "name and sku are required"}, 400

    existing = product_service.get_by_sku(sku)
    if existing:
        return {"error": f"SKU {sku} already exists"}, 409

    product = product_service.create(name=name, sku=sku, price=data.get("price", 0))
    return product.to_dict(), 201

def update_product(request, product_id: str):
    \"\"\"PATCH /products/:id — Update product details.\"\"\"
    data = request.json
    product = product_service.get_by_id(product_id)
    if not product:
        return {"error": "Product not found"}, 404

    new_sku = data.get("sku")
    if new_sku:
        new_sku = new_sku.strip().upper()
        conflict = product_service.get_by_sku(new_sku)
        if conflict and conflict.id != product_id:
            return {"error": f"SKU {new_sku} already exists"}, 409

    product_service.update(product_id, name=data.get("name"), sku=new_sku, price=data.get("price"))
    return product_service.get_by_id(product_id).to_dict(), 200

def search_products(request):
    \"\"\"GET /products/search?q=...\"\"\"
    query = request.args.get("q", "")
    results = product_service.search(query)
    return {"products": [p.to_dict() for p in results]}, 200
"""

_LOW_API_B = """\
# api/products.py
import logging
from services.product_service import ProductService

logger = logging.getLogger(__name__)
product_service = ProductService()

# ⚠ TRIM INCONSISTENCY: create_product() strips and uppercases the SKU,
# but product_service.get_by_sku() does NOT normalize its input.
# This means searching for "abc-001" won't find a product created with
# SKU " abc-001 " (which was stored as "ABC-001").
# Similarly, search_products() passes the raw query to product_service.search()
# without trimming or case normalization.

def create_product(request):
    \"\"\"POST /products — Create a new product listing.\"\"\"
    data = request.json
    name = data.get("name", "").strip()
    sku = data.get("sku", "").strip().upper()

    if not name or not sku:
        return {"error": "name and sku are required"}, 400

    existing = product_service.get_by_sku(sku)
    if existing:
        return {"error": f"SKU {sku} already exists"}, 409

    product = product_service.create(name=name, sku=sku, price=data.get("price", 0))
    return product.to_dict(), 201

def update_product(request, product_id: str):
    \"\"\"PATCH /products/:id — Update product details.\"\"\"
    data = request.json
    product = product_service.get_by_id(product_id)
    if not product:
        return {"error": "Product not found"}, 404

    new_sku = data.get("sku")
    if new_sku:
        new_sku = new_sku.strip().upper()
        conflict = product_service.get_by_sku(new_sku)
        if conflict and conflict.id != product_id:
            return {"error": f"SKU {new_sku} already exists"}, 409

    product_service.update(product_id, name=data.get("name"), sku=new_sku, price=data.get("price"))
    return product_service.get_by_id(product_id).to_dict(), 200

def search_products(request):
    \"\"\"GET /products/search?q=...\"\"\"
    query = request.args.get("q", "")
    results = product_service.search(query)
    return {"products": [p.to_dict() for p in results]}, 200
"""

_LOW_MODEL = """\
# models/product.py
from dataclasses import dataclass, field
import uuid

@dataclass
class Product:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    sku: str = ""
    price: float = 0.0
    is_active: bool = True
"""

_LOW_SCHEMA = """\
# db/schema.sql
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    sku VARCHAR(100) NOT NULL UNIQUE,
    price DECIMAL(10,2) NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE UNIQUE INDEX idx_products_sku ON products(sku);
"""

_LOW_IMPORT = """\
# jobs/import_products.py
\"\"\"Bulk import products from CSV.\"\"\"
import csv
from services.product_service import ProductService

def import_from_csv(filepath: str):
    product_service = ProductService()
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row["sku"]  # raw value from CSV, no trimming
            existing = product_service.get_by_sku(sku)
            if not existing:
                product_service.create(name=row["name"], sku=sku, price=float(row["price"]))
"""

P04_LOW = Scenario(
    pattern="④",
    pattern_name="Guard Non-Propagation",
    severity="low",
    description="API layer trims/uppercases SKU on create, but service layer does no normalization",
    visible_files={
        "api/products.py": _LOW_API_A,
        "models/product.py": _LOW_MODEL,
        "db/schema.sql": _LOW_SCHEMA,
        "jobs/import_products.py": _LOW_IMPORT,
    },
    annotated_files={
        "api/products.py": _LOW_API_B,
    },
    hidden_file_name="services/product_service.py",
    hidden_file_description="get_by_sku() and search() do exact match without normalization",
    questions=[
        Question(
            text=(
                "A product is created via POST /products with SKU ' abc-001 ' (with spaces). "
                "It's stored as 'ABC-001'. Later, the CSV import job tries to import "
                "a product with SKU 'abc-001' (lowercase, no spaces). "
                "What happens?"
            ),
            choices={
                "A": "The import is skipped — get_by_sku('abc-001') finds the existing 'ABC-001' "
                     "because the service normalizes input",
                "B": "A duplicate is created — get_by_sku('abc-001') returns None because "
                     "the service does exact match and 'abc-001' ≠ 'ABC-001'",
                "C": "A database error — the UNIQUE constraint on sku catches the duplicate",
                "D": "The import updates the existing product's name and price",
            },
            correct="B",
        ),
        Question(
            text=(
                "A user creates a product with SKU 'WIDGET-100' via the API. "
                "Another user searches GET /products/search?q=widget-100 (lowercase). "
                "Will they find the product?"
            ),
            choices={
                "A": "Yes — the search service normalizes the query to match stored SKUs",
                "B": "No — the search service does an exact/case-sensitive match and "
                     "'widget-100' won't match 'WIDGET-100'",
                "C": "Yes — PostgreSQL's LIKE operator is case-insensitive by default",
                "D": "Depends on whether the database collation is case-sensitive",
            },
            correct="B",
        ),
        Question(
            text=(
                "An admin tries to update a product's SKU from 'OLD-SKU' to 'new-sku' "
                "via PATCH /products/:id. The API normalizes it to 'NEW-SKU'. "
                "Later, the duplicate check in create_product for SKU 'new-sku' "
                "calls product_service.get_by_sku('NEW-SKU'). Does it correctly "
                "detect the existing product?"
            ),
            choices={
                "A": "Yes — the API always normalizes before calling the service, "
                     "so the stored value and lookup value both use uppercase",
                "B": "No — the service's get_by_sku might use a different normalization "
                     "than what was stored",
                "C": "Yes — but only because update_product also normalizes the SKU before saving",
                "D": "No — the UNIQUE index prevents the duplicate regardless of the service check",
            },
            correct="A",
        ),
    ],
)
