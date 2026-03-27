"""⑩ Missing Abstraction — 3重大度シナリオ.

high:   金額フォーマットが3箇所でバラバラ（小数点、通貨記号、丸め）→ 合計不一致
medium: 日時パースが複数箇所で個別実装 → 一部が夏時間を無視
low:    ファイルパス結合が os.path.join と文字列連結で混在 → Windows でパス区切り問題
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ⑩ × high — 金額フォーマットが3箇所でバラバラ
# =====================================================================

_H_INVOICE_A = """\
# api/invoices.py
from services.invoice_service import InvoiceService

invoice_service = InvoiceService()

def get_invoice(request, invoice_id: str):
    \"\"\"GET /invoices/:id\"\"\"
    invoice = invoice_service.get_by_id(invoice_id)
    if not invoice:
        return {"error": "Not found"}, 404

    # Format line items
    items = []
    for item in invoice.items:
        items.append({
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": f"${item.unit_price:.2f}",
            "total": f"${item.quantity * item.unit_price:.2f}",
        })

    subtotal = sum(i.quantity * i.unit_price for i in invoice.items)
    tax = subtotal * invoice.tax_rate
    total = subtotal + tax

    return {
        "invoice_id": invoice.id,
        "items": items,
        "subtotal": f"${subtotal:.2f}",
        "tax": f"${tax:.2f}",
        "total": f"${total:.2f}",
    }, 200
"""

_H_INVOICE_B = """\
# api/invoices.py
from services.invoice_service import InvoiceService

invoice_service = InvoiceService()

# ⚠ MISSING ABSTRACTION: Money formatting is done inline in 3 different places:
# 1. Here: f"${amount:.2f}" — rounds to 2 decimal places using Python's round-half-even
# 2. services/report_service.py: f"${round(amount, 2):.2f}" — rounds using round() first
# 3. services/export_service.py: f"${int(amount * 100) / 100:.2f}" — truncates (floor)
# For amount = $10.125:
#   - This file: $10.12 (banker's rounding: .5 rounds to even)
#   - report: $10.13 (round() = round-half-up for .125)
#   - export: $10.12 (truncation)
# Over many line items, these differences compound. A report total may differ
# from the invoice total by several dollars for large invoices.

def get_invoice(request, invoice_id: str):
    \"\"\"GET /invoices/:id\"\"\"
    invoice = invoice_service.get_by_id(invoice_id)
    if not invoice:
        return {"error": "Not found"}, 404

    # Format line items
    items = []
    for item in invoice.items:
        items.append({
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": f"${item.unit_price:.2f}",
            "total": f"${item.quantity * item.unit_price:.2f}",
        })

    subtotal = sum(i.quantity * i.unit_price for i in invoice.items)
    tax = subtotal * invoice.tax_rate
    total = subtotal + tax

    return {
        "invoice_id": invoice.id,
        "items": items,
        "subtotal": f"${subtotal:.2f}",
        "tax": f"${tax:.2f}",
        "total": f"${total:.2f}",
    }, 200
"""

_H_REPORT = """\
# api/reports.py
from services.report_service import ReportService

report_service = ReportService()

def monthly_revenue(request):
    \"\"\"GET /reports/monthly-revenue\"\"\"
    month = request.args.get("month", "2024-01")
    report = report_service.generate_monthly(month)
    return report, 200
"""

_H_EXPORT = """\
# api/export.py
from services.export_service import ExportService

export_service = ExportService()

def export_invoices_csv(request):
    \"\"\"GET /export/invoices.csv\"\"\"
    month = request.args.get("month", "2024-01")
    csv_content = export_service.invoices_to_csv(month)
    return csv_content, 200, {"Content-Type": "text/csv"}
"""

_H_MODEL = """\
# models/invoice.py
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class InvoiceItem:
    description: str = ""
    quantity: int = 1
    unit_price: float = 0.0

@dataclass
class Invoice:
    id: str = ""
    customer_id: str = ""
    items: list = field(default_factory=list)
    tax_rate: float = 0.08
    created_at: datetime = None
"""

_H_RECONCILE = """\
# jobs/reconciliation.py
\"\"\"Monthly reconciliation: compare invoice totals across systems.\"\"\"

def run_reconciliation(month: str):
    # Compare totals from:
    # 1. Invoice API totals
    # 2. Monthly revenue report
    # 3. CSV export totals
    # All three should match.
    # TODO: Consistently off by $2-5 for months with many small transactions.
    pass
"""

P10_HIGH = Scenario(
    pattern="⑩",
    pattern_name="Missing Abstraction",
    severity="high",
    description="Money formatting done 3 ways (banker's round, round-half-up, truncate); totals diverge across systems",
    visible_files={
        "api/invoices.py": _H_INVOICE_A,
        "api/reports.py": _H_REPORT,
        "api/export.py": _H_EXPORT,
        "models/invoice.py": _H_MODEL,
        "jobs/reconciliation.py": _H_RECONCILE,
    },
    annotated_files={
        "api/invoices.py": _H_INVOICE_B,
    },
    hidden_file_name="services/report_service.py",
    hidden_file_description="Uses round(amount, 2) (round-half-up) for formatting; export uses int(amount*100)/100 (truncation)",
    questions=[
        Question(
            text=(
                "An invoice has 100 line items each priced at $10.125. "
                "The invoice API shows each as $10.12 (banker's rounding). "
                "The monthly report shows the same items. Will the totals match?"
            ),
            choices={
                "A": "Yes — both use the same rounding method",
                "B": "No — the report uses round() which gives $10.13 per item; over 100 items "
                     "the report total is $1013.00 vs the invoice's $1012.00, a $1.00 discrepancy",
                "C": "Yes — $10.125 rounds to $10.13 in both cases",
                "D": "No — but the difference is less than $0.01 (rounding error within precision)",
            },
            correct="B",
        ),
        Question(
            text=(
                "The reconciliation job reports a consistent $2-5 discrepancy between the three "
                "systems each month. An auditor asks why. What's the root cause?"
            ),
            choices={
                "A": "Floating point precision errors in Python",
                "B": "Each system uses a different rounding method for money formatting "
                     "(banker's rounding, round-half-up, truncation) because there's no shared "
                     "money formatting function; the differences compound over many transactions",
                "C": "Some invoices are modified after the report is generated",
                "D": "The CSV export truncates cents, losing precision",
            },
            correct="B",
        ),
        Question(
            text=(
                "A developer fixes the report service to use f'${amount:.2f}' (matching the invoice). "
                "Does this fix the reconciliation discrepancy completely?"
            ),
            choices={
                "A": "Yes — all three systems now use the same formatting",
                "B": "Partially — the report now matches the invoice, but the CSV export still "
                     "uses truncation (int(amount*100)/100), so the export will still differ",
                "C": "Yes — the export inherits its formatting from the report",
                "D": "No — the underlying calculation still uses different precision levels",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑩ × medium — 日時パースが個別実装で夏時間を無視
# =====================================================================

_M_EVENTS_A = """\
# api/events.py
from datetime import datetime

def parse_event_time(request):
    \"\"\"POST /events — Create event with timezone-aware start time.

    Body: {"title": "...", "start_time": "2024-03-10 02:30:00", "timezone": "US/Eastern"}
    \"\"\"
    data = request.json
    time_str = data["start_time"]
    tz_name = data.get("timezone", "UTC")

    # Parse time string
    dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")

    from services.event_service import EventService
    event_service = EventService()
    event = event_service.create(
        title=data["title"],
        start_time=dt,
        timezone=tz_name,
    )
    return event.to_dict(), 201
"""

_M_EVENTS_B = """\
# api/events.py
from datetime import datetime

# ⚠ MISSING ABSTRACTION: Timezone-aware datetime parsing is done in 3 places:
# 1. Here: strptime + passes raw tz_name to service
# 2. services/event_service.py: uses pytz.timezone(tz_name).localize(dt) — this
#    correctly handles DST transitions
# 3. services/reminder_service.py: uses dt.replace(tzinfo=tz) — this does NOT
#    handle DST and uses the STANDARD offset even during daylight saving time
# For "2024-03-10 02:30:00" in US/Eastern (DST transition day):
#   - event_service: raises NonExistentTimeError (2:30 AM doesn't exist during spring-forward)
#   - reminder_service: silently uses EST (-05:00) instead of EDT (-04:00), creating a
#     1-hour offset that persists for all events during DST

def parse_event_time(request):
    \"\"\"POST /events — Create event with timezone-aware start time.

    Body: {"title": "...", "start_time": "2024-03-10 02:30:00", "timezone": "US/Eastern"}
    \"\"\"
    data = request.json
    time_str = data["start_time"]
    tz_name = data.get("timezone", "UTC")

    # Parse time string
    dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")

    from services.event_service import EventService
    event_service = EventService()
    event = event_service.create(
        title=data["title"],
        start_time=dt,
        timezone=tz_name,
    )
    return event.to_dict(), 201
"""

_M_REMINDERS = """\
# api/reminders.py
from services.reminder_service import ReminderService

reminder_service = ReminderService()

def set_reminder(request):
    \"\"\"POST /reminders — Set a reminder at a specific time.\"\"\"
    data = request.json
    reminder = reminder_service.create(
        message=data["message"],
        remind_at=data["time"],
        timezone=data.get("timezone", "UTC"),
    )
    return reminder.to_dict(), 201
"""

_M_CAL = """\
# integrations/calendar_export.py
from datetime import datetime

def format_ical_event(event) -> str:
    \"\"\"Export event as iCal format.\"\"\"
    start = event.start_time.strftime("%Y%m%dT%H%M%S")
    return f\"\"\"BEGIN:VEVENT
DTSTART;TZID={event.timezone}:{start}
SUMMARY:{event.title}
END:VEVENT\"\"\"
"""

_M_MODEL = """\
# models/event.py
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Event:
    id: str = ""
    title: str = ""
    start_time: Optional[datetime] = None
    timezone: str = "UTC"
"""

P10_MEDIUM = Scenario(
    pattern="⑩",
    pattern_name="Missing Abstraction",
    severity="medium",
    description="Timezone handling done 3 ways: pytz.localize (correct), replace(tzinfo=) (wrong DST), raw strptime",
    visible_files={
        "api/events.py": _M_EVENTS_A,
        "api/reminders.py": _M_REMINDERS,
        "integrations/calendar_export.py": _M_CAL,
        "models/event.py": _M_MODEL,
    },
    annotated_files={
        "api/events.py": _M_EVENTS_B,
    },
    hidden_file_name="services/reminder_service.py",
    hidden_file_description="Uses dt.replace(tzinfo=tz) which ignores DST; always uses standard offset",
    questions=[
        Question(
            text=(
                "A user in New York sets a reminder for '2024-07-15 14:00:00' timezone=US/Eastern. "
                "July is during EDT (UTC-4). What UTC time does the reminder fire?"
            ),
            choices={
                "A": "18:00 UTC (14:00 + 4 hours EDT offset) — correct",
                "B": "19:00 UTC — the reminder service uses EST offset (-5) even during summer, "
                     "so it calculates 14:00 + 5 = 19:00 UTC, firing 1 hour late",
                "C": "14:00 UTC — timezone is ignored",
                "D": "18:00 UTC — both services handle DST correctly",
            },
            correct="B",
        ),
        Question(
            text=(
                "An event is created for '2024-03-10 02:30:00' in US/Eastern. "
                "This is the spring-forward DST transition — 2:30 AM doesn't exist. "
                "What happens in the event service vs the reminder service?"
            ),
            choices={
                "A": "Both raise an error about the non-existent time",
                "B": "The event service raises NonExistentTimeError (correct DST handling); "
                     "the reminder service silently uses 2:30 AM EST (-05:00) which corresponds "
                     "to a different actual time than intended",
                "C": "Both skip to 3:30 AM EDT (the next valid time)",
                "D": "Both interpret it as 2:30 AM EST (before the transition)",
            },
            correct="B",
        ),
        Question(
            text=(
                "Events created through the event API match calendar exports exactly. "
                "But reminders are consistently 1 hour off during summer months. "
                "A developer checks the reminder API code and sees 'timezone=US/Eastern' "
                "is being passed correctly. Where is the bug?"
            ),
            choices={
                "A": "In the API layer — the timezone parameter isn't being forwarded",
                "B": "In the reminder service — it uses replace(tzinfo=) which always applies "
                     "the standard (winter) offset even during daylight saving time; this is "
                     "a known Python pitfall when there's no shared timezone utility",
                "C": "In the calendar export — it doesn't account for DST",
                "D": "In the database — timestamps are stored without timezone info",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑩ × low — パス結合方法の混在
# =====================================================================

_L_STORAGE_A = """\
# services/storage.py
import os

BASE_DIR = "/data/uploads"

def get_user_path(user_id: str, filename: str) -> str:
    \"\"\"Get full path for a user's uploaded file.\"\"\"
    return os.path.join(BASE_DIR, "users", user_id, filename)

def get_temp_path(filename: str) -> str:
    \"\"\"Get path in temp directory.\"\"\"
    return BASE_DIR + "/tmp/" + filename

def get_archive_path(year: str, month: str, filename: str) -> str:
    \"\"\"Get path for archived files.\"\"\"
    return f"{BASE_DIR}/archive/{year}/{month}/{filename}"

def list_user_files(user_id: str) -> list:
    user_dir = os.path.join(BASE_DIR, "users", user_id)
    if os.path.exists(user_dir):
        return os.listdir(user_dir)
    return []
"""

_L_STORAGE_B = """\
# services/storage.py
import os

BASE_DIR = "/data/uploads"

# ⚠ MISSING ABSTRACTION: Path construction uses 3 different methods:
# 1. os.path.join() — correct, handles OS path separators
# 2. String concatenation (+ "/tmp/" +) — hardcodes Unix separator
# 3. f-string interpolation — hardcodes Unix separator
# On Linux, all three produce the same result. On Windows (e.g., when running
# tests locally or migrating to Azure), methods 2 and 3 produce paths with
# mixed separators like "C:\\data\\uploads/tmp/file.txt" which some Windows
# APIs reject. Also, string concat doesn't normalize double slashes.

def get_user_path(user_id: str, filename: str) -> str:
    \"\"\"Get full path for a user's uploaded file.\"\"\"
    return os.path.join(BASE_DIR, "users", user_id, filename)

def get_temp_path(filename: str) -> str:
    \"\"\"Get path in temp directory.\"\"\"
    return BASE_DIR + "/tmp/" + filename

def get_archive_path(year: str, month: str, filename: str) -> str:
    \"\"\"Get path for archived files.\"\"\"
    return f"{BASE_DIR}/archive/{year}/{month}/{filename}"

def list_user_files(user_id: str) -> list:
    user_dir = os.path.join(BASE_DIR, "users", user_id)
    if os.path.exists(user_dir):
        return os.listdir(user_dir)
    return []
"""

_L_CLEANUP = """\
# jobs/cleanup.py
import os

def cleanup_temp_files(max_age_hours: int = 24):
    \"\"\"Remove temp files older than max_age_hours.\"\"\"
    from services.storage import get_temp_path
    temp_dir = get_temp_path("")
    # Note: get_temp_path("") returns "/data/uploads/tmp/"
    if os.path.isdir(temp_dir):
        for f in os.listdir(temp_dir):
            filepath = os.path.join(temp_dir, f)
            # ... check age and remove
"""

_L_BACKUP = """\
# jobs/backup.py
import os
import shutil

def backup_archives(dest_base: str = "/backups"):
    from services.storage import get_archive_path
    # Reconstruct archive root
    archive_root = get_archive_path("", "", "").rstrip("/")
    # This gives: "/data/uploads/archive//"  → rstrip → "/data/uploads/archive"
    if os.path.isdir(archive_root):
        shutil.copytree(archive_root, os.path.join(dest_base, "archive"))
"""

_L_TEST = """\
# tests/test_storage.py
import os

def test_get_user_path():
    from services.storage import get_user_path
    path = get_user_path("user-1", "photo.jpg")
    # On Linux: /data/uploads/users/user-1/photo.jpg ✓
    # On Windows: \\data\\uploads\\users\\user-1\\photo.jpg ✓
    assert "user-1" in path and "photo.jpg" in path

def test_get_temp_path():
    from services.storage import get_temp_path
    path = get_temp_path("temp.dat")
    # On Linux: /data/uploads/tmp/temp.dat ✓
    # On Windows: /data/uploads/tmp/temp.dat ← still uses / (string concat)
    assert "temp.dat" in path
"""

P10_LOW = Scenario(
    pattern="⑩",
    pattern_name="Missing Abstraction",
    severity="low",
    description="Path construction uses os.path.join, string concat, and f-string; breaks on Windows",
    visible_files={
        "services/storage.py": _L_STORAGE_A,
        "jobs/cleanup.py": _L_CLEANUP,
        "jobs/backup.py": _L_BACKUP,
        "tests/test_storage.py": _L_TEST,
    },
    annotated_files={
        "services/storage.py": _L_STORAGE_B,
    },
    hidden_file_name="services/path_utils.py",
    hidden_file_description="No shared path utility exists; each function builds paths differently",
    questions=[
        Question(
            text=(
                "The team runs tests on Windows for the first time. "
                "get_user_path() uses os.path.join, get_temp_path() uses string concatenation. "
                "Which one breaks on Windows?"
            ),
            choices={
                "A": "get_user_path — os.path.join doesn't work on Windows",
                "B": "get_temp_path — string concatenation hardcodes '/' which doesn't match "
                     "Windows path convention; some Windows APIs reject mixed separators",
                "C": "Neither — Python handles path separators automatically",
                "D": "Both — the BASE_DIR uses '/' which is invalid on Windows",
            },
            correct="B",
        ),
        Question(
            text=(
                "The backup job calls get_archive_path('', '', '').rstrip('/') to find the "
                "archive root directory. What path does this produce?"
            ),
            choices={
                "A": "/data/uploads/archive — clean path",
                "B": "/data/uploads/archive/ — the f-string creates '///' which rstrip removes "
                     "but the path may still have issues with empty path segments",
                "C": "/data/uploads/archive — correct, but the double slashes in the middle "
                     "('/archive///') are only partially cleaned by rstrip",
                "D": "An error — empty strings cause path resolution to fail",
            },
            correct="C",
        ),
        Question(
            text=(
                "A developer adds a new function that needs a path to '/data/uploads/reports/'. "
                "There's no shared path builder, so they copy the f-string pattern from "
                "get_archive_path(). The path works on Linux but later breaks on Windows. "
                "What would have prevented this?"
            ),
            choices={
                "A": "Using raw strings (r-prefix) for all paths",
                "B": "Having a shared path utility function that always uses os.path.join() "
                     "or pathlib.Path — a missing abstraction that would prevent each new "
                     "function from reinventing path construction differently",
                "C": "Setting the PATH_SEPARATOR environment variable",
                "D": "Using absolute paths instead of relative paths",
            },
            correct="B",
        ),
    ],
)
