"""⑨ Interface Mismatch — 3重大度シナリオ.

high:   呼び出し側が amount を cents で渡すが、受け側は dollars を期待 → 100倍課金
medium: 日時を UTC で渡すが受け側がローカルタイムとして解釈 → 9時間ずれ
low:    配列を渡すが受け側が CSV 文字列を期待 → toString で "[object Object]"
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ⑨ × high — Cents vs dollars
# =====================================================================

_H_CHECKOUT_A = """\
# api/checkout.py
from services.payment_gateway import PaymentGateway
from services.order_service import OrderService

payment = PaymentGateway()
order_service = OrderService()

def process_payment(request):
    \"\"\"POST /checkout/pay\"\"\"
    order = order_service.get_by_id(request.json["order_id"])
    if not order:
        return {"error": "Order not found"}, 404

    # Convert dollars to cents for payment processing
    amount_cents = int(order.total * 100)

    result = payment.charge(
        amount=amount_cents,
        currency="usd",
        token=request.json["payment_token"],
        description=f"Order #{order.id}",
    )

    if result.success:
        order_service.mark_paid(order.id, result.charge_id)
        return {"charge_id": result.charge_id, "amount": order.total}, 200
    return {"error": result.error_message}, 402
"""

_H_CHECKOUT_B = """\
# api/checkout.py
from services.payment_gateway import PaymentGateway
from services.order_service import OrderService

payment = PaymentGateway()
order_service = OrderService()

# ⚠ INTERFACE MISMATCH: This code converts to cents (amount * 100) before
# calling payment.charge(). But PaymentGateway.charge() ALSO converts to
# cents internally (it expects dollars and multiplies by 100 itself).
# Result: A $50.00 order is charged as $5000.00 (50 * 100 * 100 = 500000 cents).
# The double conversion means customers are charged 100x the correct amount.

def process_payment(request):
    \"\"\"POST /checkout/pay\"\"\"
    order = order_service.get_by_id(request.json["order_id"])
    if not order:
        return {"error": "Order not found"}, 404

    # Convert dollars to cents for payment processing
    amount_cents = int(order.total * 100)

    result = payment.charge(
        amount=amount_cents,
        currency="usd",
        token=request.json["payment_token"],
        description=f"Order #{order.id}",
    )

    if result.success:
        order_service.mark_paid(order.id, result.charge_id)
        return {"charge_id": result.charge_id, "amount": order.total}, 200
    return {"error": result.error_message}, 402
"""

_H_ORDER = """\
# models/order.py
from dataclasses import dataclass

@dataclass
class Order:
    id: str = ""
    user_id: str = ""
    total: float = 0.0  # in dollars
    status: str = "pending"
"""

_H_REFUND = """\
# api/refund.py
from services.payment_gateway import PaymentGateway

payment = PaymentGateway()

def process_refund(request):
    \"\"\"POST /checkout/refund\"\"\"
    charge_id = request.json["charge_id"]
    amount_dollars = request.json["amount"]  # Frontend sends dollars

    result = payment.refund(
        charge_id=charge_id,
        amount=amount_dollars,  # Passes dollars directly
    )
    return {"status": "refunded" if result.success else "failed"}, 200
"""

_H_RECEIPT = """\
# services/receipt_service.py
def generate_receipt(order, charge_id: str) -> dict:
    return {
        "order_id": order.id,
        "amount_charged": f"${order.total:.2f}",
        "charge_id": charge_id,
    }
"""

P09_HIGH = Scenario(
    pattern="⑨",
    pattern_name="Interface Mismatch",
    severity="high",
    description="Checkout converts to cents then passes to gateway which also converts; 100x overcharge",
    visible_files={
        "api/checkout.py": _H_CHECKOUT_A,
        "models/order.py": _H_ORDER,
        "api/refund.py": _H_REFUND,
        "services/receipt_service.py": _H_RECEIPT,
    },
    annotated_files={
        "api/checkout.py": _H_CHECKOUT_B,
    },
    hidden_file_name="services/payment_gateway.py",
    hidden_file_description="charge() expects amount in dollars and internally multiplies by 100 for the Stripe API",
    questions=[
        Question(
            text=(
                "A customer places a $29.99 order and submits payment. "
                "How much is actually charged to their credit card?"
            ),
            choices={
                "A": "$29.99 — the correct amount",
                "B": "$2,999.00 — the checkout converts $29.99 to 2999 cents, then the gateway "
                     "treats 2999 as dollars and converts again to 299900 cents",
                "C": "$0.30 — rounding error in the cents conversion",
                "D": "$29.99 — the gateway detects the double conversion and corrects it",
            },
            correct="B",
        ),
        Question(
            text=(
                "The refund endpoint passes amount_dollars directly to payment.refund() "
                "without converting to cents. The checkout passes cents to payment.charge(). "
                "If a customer requests a full refund of their $29.99 order, what happens?"
            ),
            choices={
                "A": "Full refund — the gateway handles both dollars and cents",
                "B": "The refund processes $29.99 correctly (gateway expects dollars), but "
                     "the original charge was $2,999.00 — so the customer is refunded $29.99 "
                     "of a $2,999.00 charge, losing $2,969.01",
                "C": "The refund fails — amount mismatch between charge and refund",
                "D": "The refund is $0.30 — same conversion error as the charge",
            },
            correct="B",
        ),
        Question(
            text=(
                "The receipt shows 'Amount charged: $29.99' (from order.total). "
                "The customer's credit card statement shows $2,999.00. "
                "They contact support with the receipt. Can support identify the bug?"
            ),
            choices={
                "A": "Yes — the 100x discrepancy clearly points to a double conversion",
                "B": "Unlikely — the receipt uses order.total (correct), not the actual charge amount; "
                     "support sees $29.99 in the system and may assume the credit card statement is wrong",
                "C": "Yes — the payment gateway logs the actual amount charged",
                "D": "No — the receipt amount and charge amount are both stored as $29.99",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑨ × medium — UTC vs local time
# =====================================================================

_M_API_A = """\
# api/scheduling.py
from datetime import datetime, timezone
from services.scheduler_service import SchedulerService

scheduler = SchedulerService()

def create_meeting(request):
    \"\"\"POST /meetings — Schedule a meeting.

    Body: {"title": "...", "start_time": "2024-01-15T14:00:00Z", ...}
    All times should be in UTC (ISO 8601 with Z suffix).
    \"\"\"
    data = request.json
    start_time = datetime.fromisoformat(data["start_time"].replace("Z", "+00:00"))

    meeting = scheduler.schedule(
        title=data["title"],
        start_time=start_time,
        duration_minutes=data.get("duration", 60),
        attendees=data.get("attendees", []),
    )
    return meeting.to_dict(), 201

def list_today_meetings(request):
    \"\"\"GET /meetings/today — List meetings for today (UTC).\"\"\"
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59)
    meetings = scheduler.get_range(start, end)
    return {"meetings": [m.to_dict() for m in meetings]}, 200
"""

_M_API_B = """\
# api/scheduling.py
from datetime import datetime, timezone
from services.scheduler_service import SchedulerService

scheduler = SchedulerService()

# ⚠ INTERFACE MISMATCH: This API parses times as UTC (correct per docs).
# But scheduler_service.schedule() interprets the datetime as LOCAL TIME
# (Asia/Tokyo, UTC+9) because it uses datetime.replace(tzinfo=None) internally
# and the server's default timezone is JST.
# A meeting scheduled for "14:00 UTC" (2PM London) becomes "14:00 JST" (2PM Tokyo),
# which is actually 05:00 UTC. Attendees in London join 9 hours late.

def create_meeting(request):
    \"\"\"POST /meetings — Schedule a meeting.

    Body: {"title": "...", "start_time": "2024-01-15T14:00:00Z", ...}
    All times should be in UTC (ISO 8601 with Z suffix).
    \"\"\"
    data = request.json
    start_time = datetime.fromisoformat(data["start_time"].replace("Z", "+00:00"))

    meeting = scheduler.schedule(
        title=data["title"],
        start_time=start_time,
        duration_minutes=data.get("duration", 60),
        attendees=data.get("attendees", []),
    )
    return meeting.to_dict(), 201

def list_today_meetings(request):
    \"\"\"GET /meetings/today — List meetings for today (UTC).\"\"\"
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59)
    meetings = scheduler.get_range(start, end)
    return {"meetings": [m.to_dict() for m in meetings]}, 200
"""

_M_MODEL = """\
# models/meeting.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class Meeting:
    id: str = ""
    title: str = ""
    start_time: Optional[datetime] = None
    duration_minutes: int = 60
    attendees: list = field(default_factory=list)
"""

_M_NOTIF = """\
# services/notification_service.py
def send_meeting_reminder(meeting, minutes_before: int = 15):
    \"\"\"Send reminder to attendees before meeting start.\"\"\"
    for attendee in meeting.attendees:
        # Uses meeting.start_time to calculate when to send
        send_email(
            to=attendee,
            subject=f"Reminder: {meeting.title} in {minutes_before} minutes",
            body=f"Your meeting starts at {meeting.start_time.isoformat()}",
        )
"""

_M_CAL = """\
# integrations/calendar_sync.py
\"\"\"Sync meetings to Google Calendar.\"\"\"

def sync_to_gcal(meeting):
    # Google Calendar API expects UTC
    return {
        "summary": meeting.title,
        "start": {"dateTime": meeting.start_time.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": meeting.end_time.isoformat(), "timeZone": "UTC"},
    }
"""

P09_MEDIUM = Scenario(
    pattern="⑨",
    pattern_name="Interface Mismatch",
    severity="medium",
    description="API passes UTC datetime but scheduler strips timezone and interprets as local (JST) → 9-hour offset",
    visible_files={
        "api/scheduling.py": _M_API_A,
        "models/meeting.py": _M_MODEL,
        "services/notification_service.py": _M_NOTIF,
        "integrations/calendar_sync.py": _M_CAL,
    },
    annotated_files={
        "api/scheduling.py": _M_API_B,
    },
    hidden_file_name="services/scheduler_service.py",
    hidden_file_description="schedule() strips timezone info and treats datetime as server-local (JST/UTC+9)",
    questions=[
        Question(
            text=(
                "A user in London schedules a meeting for '2024-01-15T14:00:00Z' (2 PM UTC). "
                "A colleague in Tokyo checks the meeting time. What time does it show?"
            ),
            choices={
                "A": "23:00 JST (14:00 UTC + 9 hours) — correct conversion",
                "B": "14:00 JST — the scheduler stripped the UTC timezone and stored 14:00 as "
                     "local time (JST), which is actually 05:00 UTC, not 14:00 UTC",
                "C": "14:00 UTC — times are always displayed in UTC",
                "D": "05:00 JST — the offset is applied in the wrong direction",
            },
            correct="B",
        ),
        Question(
            text=(
                "The meeting reminder email says 'starts at 14:00' but the London user "
                "expected 14:00 UTC. They receive the reminder 9 hours before the meeting. "
                "Why?"
            ),
            choices={
                "A": "Email delivery delay",
                "B": "The stored time is 14:00 JST (= 05:00 UTC). The reminder fires 15 min "
                     "before 05:00 UTC, which is 9 hours before the intended 14:00 UTC",
                "C": "The reminder is set for the wrong timezone",
                "D": "The notification service has a separate timezone bug",
            },
            correct="B",
        ),
        Question(
            text=(
                "The calendar sync sends meeting.start_time to Google Calendar as 'UTC'. "
                "If a meeting was created for 14:00 UTC, what time appears in Google Calendar?"
            ),
            choices={
                "A": "14:00 UTC — correct",
                "B": "05:00 UTC — the stored time is 14:00 JST (which is 05:00 UTC), "
                     "and the sync sends this as UTC, so Google displays 05:00 UTC",
                "C": "23:00 UTC — double timezone offset",
                "D": "14:00 JST — Google Calendar ignores the timezone parameter",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑨ × low — Array vs CSV string
# =====================================================================

_L_API_A = """\
# api/bulk_operations.py
from services.tag_service import TagService

tag_service = TagService()

def bulk_tag(request):
    \"\"\"POST /items/bulk-tag — Add tags to multiple items.

    Body: {"item_ids": ["id1", "id2"], "tags": ["urgent", "review"]}
    \"\"\"
    item_ids = request.json.get("item_ids", [])
    tags = request.json.get("tags", [])

    if not item_ids or not tags:
        return {"error": "item_ids and tags are required"}, 400

    result = tag_service.bulk_add(item_ids=item_ids, tags=tags)
    return {"tagged": result.count}, 200
"""

_L_API_B = """\
# api/bulk_operations.py
from services.tag_service import TagService

tag_service = TagService()

# ⚠ INTERFACE MISMATCH: This API passes `tags` as a Python list (e.g.,
# ["urgent", "review"]). But tag_service.bulk_add() expects `tags` as a
# comma-separated string (e.g., "urgent,review"). When it receives a list,
# it calls str(tags) → "['urgent', 'review']" and stores this as a SINGLE tag
# with value "['urgent', 'review']" instead of two separate tags.
# Items end up with one malformed tag instead of the intended individual tags.

def bulk_tag(request):
    \"\"\"POST /items/bulk-tag — Add tags to multiple items.

    Body: {"item_ids": ["id1", "id2"], "tags": ["urgent", "review"]}
    \"\"\"
    item_ids = request.json.get("item_ids", [])
    tags = request.json.get("tags", [])

    if not item_ids or not tags:
        return {"error": "item_ids and tags are required"}, 400

    result = tag_service.bulk_add(item_ids=item_ids, tags=tags)
    return {"tagged": result.count}, 200
"""

_L_MODEL = """\
# models/item.py
from dataclasses import dataclass, field

@dataclass
class Item:
    id: str = ""
    name: str = ""
    tags: list = field(default_factory=list)
"""

_L_SEARCH = """\
# api/search.py
from services.tag_service import TagService

tag_service = TagService()

def search_by_tag(request):
    \"\"\"GET /items/search?tag=urgent\"\"\"
    tag = request.args.get("tag", "")
    items = tag_service.find_by_tag(tag)
    return {"items": [i.to_dict() for i in items]}, 200
"""

_L_EXPORT = """\
# jobs/export_tags.py
from services.tag_service import TagService

def export_tag_report():
    tag_service = TagService()
    all_tags = tag_service.get_all_tags()
    return {"total_unique_tags": len(all_tags), "tags": sorted(all_tags)}
"""

P09_LOW = Scenario(
    pattern="⑨",
    pattern_name="Interface Mismatch",
    severity="low",
    description="API passes list of tags but service expects CSV string; list becomes '[\"urgent\", \"review\"]' single tag",
    visible_files={
        "api/bulk_operations.py": _L_API_A,
        "models/item.py": _L_MODEL,
        "api/search.py": _L_SEARCH,
        "jobs/export_tags.py": _L_EXPORT,
    },
    annotated_files={
        "api/bulk_operations.py": _L_API_B,
    },
    hidden_file_name="services/tag_service.py",
    hidden_file_description="bulk_add() expects tags as comma-separated string; calls str() on list input",
    questions=[
        Question(
            text=(
                "A user calls POST /items/bulk-tag with tags=[\"urgent\", \"review\"]. "
                "They then search GET /items/search?tag=urgent. Do they find the items?"
            ),
            choices={
                "A": "Yes — both tags 'urgent' and 'review' are stored separately",
                "B": "No — the service stored one tag with value '[\"urgent\", \"review\"]' (the string "
                     "representation of the list); searching for 'urgent' won't match",
                "C": "Yes — the search service handles both list and string tag formats",
                "D": "No — the tags were rejected as invalid format",
            },
            correct="B",
        ),
        Question(
            text=(
                "The tag export report shows 500 'unique tags'. A developer notices many tags "
                "look like \"['tag1', 'tag2']\" instead of individual words. "
                "What is the actual number of intended unique tags?"
            ),
            choices={
                "A": "500 — each entry is a valid tag",
                "B": "Fewer than 500 — many 'tags' are actually string representations of lists; "
                     "the true tags are embedded inside these strings, and the real count of "
                     "intended individual tags is much lower",
                "C": "More than 500 — each list-string contains multiple intended tags",
                "D": "Exactly 500 — but some have encoding issues",
            },
            correct="B",
        ),
        Question(
            text=(
                "The API responds with {\"tagged\": 2}, indicating 2 items were tagged. "
                "The user expects each item to have 2 tags. How many tags does each item actually have?"
            ),
            choices={
                "A": "2 — 'urgent' and 'review' are stored separately",
                "B": "1 — a single tag containing the string representation of the list; "
                     "the 'tagged' count refers to items processed, not tags applied",
                "C": "0 — the tagging failed silently",
                "D": "2 — but they're concatenated as 'urgent,review' (one string with comma)",
            },
            correct="B",
        ),
    ],
)
