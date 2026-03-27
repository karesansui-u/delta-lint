"""② Semantic Mismatch — 3重大度シナリオ.

high:   "status" が order=配送状態 vs payment=決済状態 で異なる意味
medium: "active" が user=アカウント有効 vs subscription=課金中 で異なる意味
low:    "count" が API=アイテム数 vs service=DB行数(deleted含む) で異なる意味
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ② × high — "status" の意味が order vs payment で異なる
# =====================================================================

_H_ORDER_API_A = """\
# api/orders.py
from services.order_service import OrderService
from services.payment_service import PaymentService

order_service = OrderService()
payment_service = PaymentService()

def get_order_status(request, order_id: str):
    \"\"\"GET /orders/:id/status — Get combined order and payment status.\"\"\"
    order = order_service.get_by_id(order_id)
    if not order:
        return {"error": "Order not found"}, 404

    payment = payment_service.get_for_order(order_id)
    return {
        "order_id": order_id,
        "status": order.status,
        "payment_status": payment.status if payment else None,
    }, 200

def fulfill_order(request, order_id: str):
    \"\"\"POST /orders/:id/fulfill — Mark order as fulfilled.

    Checks that payment is complete before allowing fulfillment.
    Requirement: payment.status must be "completed" to proceed.
    \"\"\"
    order = order_service.get_by_id(order_id)
    if not order:
        return {"error": "Order not found"}, 404

    payment = payment_service.get_for_order(order_id)
    if not payment or payment.status != "completed":
        return {"error": "Payment not completed"}, 400

    order_service.mark_fulfilled(order_id)
    return {"status": "fulfilled"}, 200
"""

_H_ORDER_API_B = """\
# api/orders.py
from services.order_service import OrderService
from services.payment_service import PaymentService

order_service = OrderService()
payment_service = PaymentService()

# ⚠ SEMANTIC MISMATCH: "status" means different things in different contexts.
# - order.status values: "pending", "confirmed", "shipped", "delivered", "cancelled"
# - payment.status values: "pending", "authorized", "captured", "completed", "refunded"
# The fulfill_order() check `payment.status != "completed"` is WRONG because
# payment_service uses "captured" (not "completed") for successful payments.
# "completed" in payment context means "captured + settled" which happens 2-3 days later.
# Orders can never be fulfilled until settlement, causing a 2-3 day delay.

def get_order_status(request, order_id: str):
    \"\"\"GET /orders/:id/status — Get combined order and payment status.\"\"\"
    order = order_service.get_by_id(order_id)
    if not order:
        return {"error": "Order not found"}, 404

    payment = payment_service.get_for_order(order_id)
    return {
        "order_id": order_id,
        "status": order.status,
        "payment_status": payment.status if payment else None,
    }, 200

def fulfill_order(request, order_id: str):
    \"\"\"POST /orders/:id/fulfill — Mark order as fulfilled.

    Checks that payment is complete before allowing fulfillment.
    Requirement: payment.status must be "completed" to proceed.
    \"\"\"
    order = order_service.get_by_id(order_id)
    if not order:
        return {"error": "Order not found"}, 404

    payment = payment_service.get_for_order(order_id)
    if not payment or payment.status != "completed":
        return {"error": "Payment not completed"}, 400

    order_service.mark_fulfilled(order_id)
    return {"status": "fulfilled"}, 200
"""

_H_ORDER_MODEL = """\
# models/order.py
from dataclasses import dataclass, field
from datetime import datetime
import uuid

@dataclass
class Order:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    status: str = "pending"  # pending, confirmed, shipped, delivered, cancelled
    total: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)
"""

_H_PAYMENT_MODEL = """\
# models/payment.py
from dataclasses import dataclass, field
from datetime import datetime
import uuid

@dataclass
class Payment:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str = ""
    amount: float = 0.0
    status: str = "pending"
    provider: str = "stripe"
    created_at: datetime = field(default_factory=datetime.utcnow)
"""

_H_WEBHOOK = """\
# integrations/stripe_webhook.py
from services.payment_service import PaymentService

payment_service = PaymentService()

def handle_webhook(event: dict):
    event_type = event["type"]
    payment_id = event["data"]["payment_id"]

    if event_type == "payment_intent.authorized":
        payment_service.update_status(payment_id, "authorized")
    elif event_type == "payment_intent.captured":
        payment_service.update_status(payment_id, "captured")
    elif event_type == "charge.settled":
        payment_service.update_status(payment_id, "completed")
    elif event_type == "charge.refunded":
        payment_service.update_status(payment_id, "refunded")
"""

_H_FULFILLMENT = """\
# jobs/auto_fulfill.py
\"\"\"Nightly job: auto-fulfill orders with completed payments.\"\"\"
from services.order_service import OrderService
from services.payment_service import PaymentService

def run():
    order_service = OrderService()
    payment_service = PaymentService()

    pending_orders = order_service.find_by_status("confirmed")
    for order in pending_orders:
        payment = payment_service.get_for_order(order.id)
        if payment and payment.status == "completed":
            order_service.mark_fulfilled(order.id)
"""

P02_HIGH = Scenario(
    pattern="②",
    pattern_name="Semantic Mismatch",
    severity="high",
    description="'status' means delivery state for orders but transaction state for payments; 'completed' check is wrong",
    visible_files={
        "api/orders.py": _H_ORDER_API_A,
        "models/order.py": _H_ORDER_MODEL,
        "models/payment.py": _H_PAYMENT_MODEL,
        "integrations/stripe_webhook.py": _H_WEBHOOK,
        "jobs/auto_fulfill.py": _H_FULFILLMENT,
    },
    annotated_files={
        "api/orders.py": _H_ORDER_API_B,
    },
    hidden_file_name="services/payment_service.py",
    hidden_file_description="Payment status flow: pending→authorized→captured→completed; 'captured' means payment succeeded, 'completed' means settled (2-3 days later)",
    questions=[
        Question(
            text=(
                "A customer pays for an order via Stripe. The payment is successfully captured "
                "(money is taken). A warehouse worker tries to fulfill the order immediately. "
                "What happens when they call POST /orders/:id/fulfill?"
            ),
            choices={
                "A": "Success — the payment status is 'captured' which satisfies the 'completed' check",
                "B": "Failure — the check requires status='completed' but the payment is only 'captured'; "
                     "'completed' happens 2-3 days later after settlement",
                "C": "Success — the fulfill endpoint only checks that a payment exists, not its status",
                "D": "Failure — the order status must be 'confirmed' before it can be fulfilled",
            },
            correct="B",
        ),
        Question(
            text=(
                "The auto_fulfill job runs nightly and checks payment.status == 'completed'. "
                "On Monday, 100 orders are paid (status='captured'). "
                "When will these orders be auto-fulfilled?"
            ),
            choices={
                "A": "Monday night — the captured status satisfies the completed check",
                "B": "Wednesday or Thursday night — after Stripe settlement changes status to 'completed' "
                     "(2-3 day delay)",
                "C": "Never — auto_fulfill only processes 'confirmed' orders, not 'captured' payments",
                "D": "Tuesday night — Stripe settles next business day",
            },
            correct="B",
        ),
        Question(
            text=(
                "The team adds a new 'express fulfillment' feature that should ship orders "
                "within 1 hour of payment. They reuse the same check: "
                "payment.status == 'completed'. Will express fulfillment work?"
            ),
            choices={
                "A": "Yes — 'completed' status is set immediately after successful payment",
                "B": "No — 'completed' only occurs after settlement (2-3 days); they need to check "
                     "for 'captured' status instead",
                "C": "Yes — but only for credit card payments, not bank transfers",
                "D": "Depends on the payment provider's webhook delivery time",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ② × medium — "active" user vs subscription
# =====================================================================

_M_DASH_A = """\
# api/dashboard.py
from services.user_service import UserService
from services.subscription_service import SubscriptionService

user_service = UserService()
sub_service = SubscriptionService()

def get_active_users_report(request):
    \"\"\"GET /admin/reports/active-users

    Returns count of active users with active subscriptions.
    Used for billing and capacity planning.
    \"\"\"
    active_users = user_service.count_active()
    active_subs = sub_service.count_active()

    return {
        "active_users": active_users,
        "active_subscriptions": active_subs,
        "coverage_rate": active_subs / active_users if active_users > 0 else 0,
    }, 200

def deactivate_expired(request):
    \"\"\"POST /admin/deactivate-expired

    Deactivate users whose subscriptions have expired.
    \"\"\"
    expired_users = sub_service.find_expired()
    for user_id in expired_users:
        user_service.deactivate(user_id)
    return {"deactivated": len(expired_users)}, 200
"""

_M_DASH_B = """\
# api/dashboard.py
from services.user_service import UserService
from services.subscription_service import SubscriptionService

user_service = UserService()
sub_service = SubscriptionService()

# ⚠ SEMANTIC MISMATCH: "active" means different things:
# - user_service.count_active(): users with is_active=True (account not disabled)
# - sub_service.count_active(): subscriptions with status="active" (currently paying)
# A user can be "active" (account enabled) but have no subscription (free tier).
# A user can be "inactive" (account disabled) but still have an "active" subscription
# (e.g., disabled for TOS violation, subscription not yet cancelled).
# The deactivate_expired() function is WRONG: it disables user ACCOUNTS when
# SUBSCRIPTIONS expire, conflating billing status with account access.

def get_active_users_report(request):
    \"\"\"GET /admin/reports/active-users

    Returns count of active users with active subscriptions.
    Used for billing and capacity planning.
    \"\"\"
    active_users = user_service.count_active()
    active_subs = sub_service.count_active()

    return {
        "active_users": active_users,
        "active_subscriptions": active_subs,
        "coverage_rate": active_subs / active_users if active_users > 0 else 0,
    }, 200

def deactivate_expired(request):
    \"\"\"POST /admin/deactivate-expired

    Deactivate users whose subscriptions have expired.
    \"\"\"
    expired_users = sub_service.find_expired()
    for user_id in expired_users:
        user_service.deactivate(user_id)
    return {"deactivated": len(expired_users)}, 200
"""

_M_USER_MODEL = """\
# models/user.py
from dataclasses import dataclass, field
from datetime import datetime
import uuid

@dataclass
class User:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    email: str = ""
    is_active: bool = True  # account enabled/disabled
    created_at: datetime = field(default_factory=datetime.utcnow)
"""

_M_SUB_MODEL = """\
# models/subscription.py
from dataclasses import dataclass, field
from datetime import datetime
import uuid

@dataclass
class Subscription:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    plan: str = "free"  # free, basic, pro, enterprise
    status: str = "active"  # active, expired, cancelled
    expires_at: datetime = field(default_factory=datetime.utcnow)
"""

_M_BILLING = """\
# jobs/billing_report.py
from services.subscription_service import SubscriptionService

def generate_monthly_report():
    sub_service = SubscriptionService()
    active = sub_service.count_active()
    return {
        "total_paying_users": active,
        "monthly_revenue": active * 29.99,  # simplified
    }
"""

P02_MEDIUM = Scenario(
    pattern="②",
    pattern_name="Semantic Mismatch",
    severity="medium",
    description="'active' means account-enabled for users but currently-paying for subscriptions; deactivation conflates them",
    visible_files={
        "api/dashboard.py": _M_DASH_A,
        "models/user.py": _M_USER_MODEL,
        "models/subscription.py": _M_SUB_MODEL,
        "jobs/billing_report.py": _M_BILLING,
    },
    annotated_files={
        "api/dashboard.py": _M_DASH_B,
    },
    hidden_file_name="services/subscription_service.py",
    hidden_file_description="count_active() counts status='active' subscriptions; find_expired() returns user_ids with expired subs",
    questions=[
        Question(
            text=(
                "There are 1000 users with is_active=True. Of those, 600 have active subscriptions "
                "and 400 are on free tier (no subscription). "
                "What does the coverage_rate report show?"
            ),
            choices={
                "A": "60% — 600 active subscriptions / 1000 active users",
                "B": "100% — all active users have active subscriptions",
                "C": "60% — but the metric is misleading because 'active' means "
                     "different things for users vs subscriptions",
                "D": "40% — it counts users WITHOUT subscriptions",
            },
            correct="C",
        ),
        Question(
            text=(
                "A user's subscription expires but they still use free-tier features. "
                "The admin runs POST /admin/deactivate-expired. "
                "What happens to this user?"
            ),
            choices={
                "A": "Nothing — only their subscription status changes to 'expired'",
                "B": "Their user account is disabled (is_active=False) — they can no longer log in, "
                     "even though deactivation was meant for billing purposes only",
                "C": "Their subscription is cancelled but their account remains active",
                "D": "They are downgraded to free tier automatically",
            },
            correct="B",
        ),
        Question(
            text=(
                "A user is disabled by admin for TOS violation (is_active=False) "
                "but their annual subscription hasn't expired yet. "
                "Does the billing report count them as a paying user?"
            ),
            choices={
                "A": "No — billing only counts users where is_active=True AND subscription is active",
                "B": "Yes — sub_service.count_active() counts subscriptions with status='active' "
                     "regardless of whether the user account is disabled",
                "C": "No — disabling the account automatically cancels the subscription",
                "D": "Yes — but a separate reconciliation job will catch the discrepancy",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ② × low — "count" の意味が API vs service で異なる
# =====================================================================

_L_API_A = """\
# api/inventory.py
from services.inventory_service import InventoryService

inventory_service = InventoryService()

def get_product_count(request, category: str):
    \"\"\"GET /inventory/:category/count

    Returns the number of products available in a category.
    Used by the storefront to show "X products available".
    \"\"\"
    count = inventory_service.count_by_category(category)
    return {"category": category, "available": count}, 200

def get_low_stock_alerts(request):
    \"\"\"GET /inventory/alerts/low-stock

    Returns categories where available count < threshold.
    \"\"\"
    alerts = []
    for cat in inventory_service.get_categories():
        count = inventory_service.count_by_category(cat)
        if count < 10:
            alerts.append({"category": cat, "count": count})
    return {"alerts": alerts}, 200
"""

_L_API_B = """\
# api/inventory.py
from services.inventory_service import InventoryService

inventory_service = InventoryService()

# ⚠ COUNT MISMATCH: The API labels this as "available" products, but
# inventory_service.count_by_category() counts ALL rows in the products table
# for that category, including discontinued (is_available=False) products.
# A category with 100 products where 60 are discontinued will show
# "available: 100" in the storefront, but only 40 can actually be ordered.
# The low_stock alert also triggers based on total count, not available count.

def get_product_count(request, category: str):
    \"\"\"GET /inventory/:category/count

    Returns the number of products available in a category.
    Used by the storefront to show "X products available".
    \"\"\"
    count = inventory_service.count_by_category(category)
    return {"category": category, "available": count}, 200

def get_low_stock_alerts(request):
    \"\"\"GET /inventory/alerts/low-stock

    Returns categories where available count < threshold.
    \"\"\"
    alerts = []
    for cat in inventory_service.get_categories():
        count = inventory_service.count_by_category(cat)
        if count < 10:
            alerts.append({"category": cat, "count": count})
    return {"alerts": alerts}, 200
"""

_L_PRODUCT = """\
# models/product.py
from dataclasses import dataclass, field
import uuid

@dataclass
class Product:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    category: str = ""
    price: float = 0.0
    is_available: bool = True
    stock_quantity: int = 0
"""

_L_SCHEMA = """\
# db/schema.sql
CREATE TABLE products (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(100) NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    stock_quantity INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_available ON products(is_available);
"""

_L_STOREFRONT = """\
# frontend/storefront.py
\"\"\"Storefront display logic.\"\"\"

def render_category_page(category: str, api_data: dict) -> str:
    count = api_data["available"]
    return f"<h1>{category}</h1><p>{count} products available</p>"
"""

P02_LOW = Scenario(
    pattern="②",
    pattern_name="Semantic Mismatch",
    severity="low",
    description="API labels count as 'available' but service counts all products including discontinued",
    visible_files={
        "api/inventory.py": _L_API_A,
        "models/product.py": _L_PRODUCT,
        "db/schema.sql": _L_SCHEMA,
        "frontend/storefront.py": _L_STOREFRONT,
    },
    annotated_files={
        "api/inventory.py": _L_API_B,
    },
    hidden_file_name="services/inventory_service.py",
    hidden_file_description="count_by_category() counts ALL rows regardless of is_available flag",
    questions=[
        Question(
            text=(
                "A category 'Electronics' has 100 products total: 40 available and 60 discontinued. "
                "A customer visits the storefront. What does the category page show?"
            ),
            choices={
                "A": "'40 products available' — the service correctly filters by is_available=True",
                "B": "'100 products available' — the service counts all products including "
                     "discontinued ones, and the API labels this as 'available'",
                "C": "'40 products available, 60 discontinued' — both counts are shown",
                "D": "'100 products' — without the 'available' label",
            },
            correct="B",
        ),
        Question(
            text=(
                "A category has 8 total products (5 available, 3 discontinued). "
                "Should the low-stock alert fire for this category?"
            ),
            choices={
                "A": "Yes — only 5 products are available, which is below the threshold of 10",
                "B": "No — the count returns 8 (total products including discontinued), "
                     "which is below 10, so it DOES fire — but for the wrong reason",
                "C": "No — 8 is close to 10 but not below it; the alert won't fire",
                "D": "Yes — the alert correctly triggers based on available inventory",
            },
            correct="B",
        ),
        Question(
            text=(
                "The team discontinues 50 products in 'Books' (moving from 120 total to 70 available, "
                "120 total). Does the reported 'available' count on the storefront change?"
            ),
            choices={
                "A": "Yes — it drops from 120 to 70, reflecting the actual available products",
                "B": "No — it stays at 120 because the service counts total rows and "
                     "discontinued products are not deleted, just flagged",
                "C": "Yes — it drops from 120 to 70 because discontinued products are moved to an archive table",
                "D": "No — it stays at 120, but the products won't appear in search results",
            },
            correct="B",
        ),
    ],
)
