"""① Asymmetric Defaults — 3重大度シナリオ.

high:   タイムアウト値が API gateway=5s vs backend service=60s (片方が hidden)
medium: ページサイズが API=20 vs DB query=100 (hidden)
low:    ログレベルが app config=WARNING vs hidden module=DEBUG
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ① × high — タイムアウト非対称 (gateway 5s vs service 60s)
# =====================================================================

_H_GATEWAY_A = """\
# gateway/proxy.py
import logging
import httpx

logger = logging.getLogger(__name__)

BACKEND_TIMEOUT = 5  # seconds
BACKEND_URL = "http://report-service:8080"

async def proxy_report(request):
    \"\"\"POST /api/reports/generate — Proxy to report generation service.

    Generates a financial report. Large datasets may take time to process.
    Returns the generated report as JSON.
    \"\"\"
    try:
        async with httpx.AsyncClient(timeout=BACKEND_TIMEOUT) as client:
            resp = await client.post(
                f"{BACKEND_URL}/generate",
                json=request.json,
                headers={"X-Request-Id": request.headers.get("X-Request-Id", "")},
            )
            return resp.json(), resp.status_code
    except httpx.TimeoutException:
        logger.warning(f"Report generation timed out after {BACKEND_TIMEOUT}s")
        return {"error": "Report generation timed out. Please try again."}, 504
"""

_H_GATEWAY_B = """\
# gateway/proxy.py
import logging
import httpx

logger = logging.getLogger(__name__)

BACKEND_TIMEOUT = 5  # seconds
BACKEND_URL = "http://report-service:8080"

# ⚠ TIMEOUT ASYMMETRY: This gateway times out after 5 seconds, but the
# report-service internally allows up to 60 seconds for report generation.
# When a report takes 6-60 seconds, the gateway returns 504 to the client
# while the backend continues processing and completes successfully.
# The completed report is orphaned — never delivered to the client.
# The client retries, causing DUPLICATE report generation.

async def proxy_report(request):
    \"\"\"POST /api/reports/generate — Proxy to report generation service.

    Generates a financial report. Large datasets may take time to process.
    Returns the generated report as JSON.
    \"\"\"
    try:
        async with httpx.AsyncClient(timeout=BACKEND_TIMEOUT) as client:
            resp = await client.post(
                f"{BACKEND_URL}/generate",
                json=request.json,
                headers={"X-Request-Id": request.headers.get("X-Request-Id", "")},
            )
            return resp.json(), resp.status_code
    except httpx.TimeoutException:
        logger.warning(f"Report generation timed out after {BACKEND_TIMEOUT}s")
        return {"error": "Report generation timed out. Please try again."}, 504
"""

_H_QUEUE = """\
# gateway/queue_config.py
\"\"\"Message queue settings for async processing.\"\"\"

REPORT_QUEUE = "reports.generate"
REPORT_RESULT_TTL = 3600  # keep results for 1 hour
MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds between retries
"""

_H_CLIENT = """\
# frontend/api_client.py
\"\"\"Frontend API client with retry logic.\"\"\"
import time

class ReportClient:
    def __init__(self, base_url: str, max_retries: int = 3):
        self.base_url = base_url
        self.max_retries = max_retries

    def generate_report(self, params: dict) -> dict:
        \"\"\"Request report generation with retry on timeout.\"\"\"
        for attempt in range(self.max_retries):
            resp = self._post("/api/reports/generate", json=params)
            if resp.status_code == 504:
                time.sleep(2 ** attempt)
                continue
            return resp.json()
        raise TimeoutError(f"Report generation failed after {self.max_retries} retries")

    def _post(self, path, **kwargs):
        import requests
        return requests.post(f"{self.base_url}{path}", **kwargs)
"""

_H_METRICS = """\
# monitoring/metrics.py
\"\"\"Prometheus metrics for gateway.\"\"\"
from prometheus_client import Counter, Histogram

request_duration = Histogram(
    "gateway_request_duration_seconds",
    "Request duration in seconds",
    ["method", "endpoint", "status"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

timeout_total = Counter(
    "gateway_timeout_total",
    "Total timeout errors",
    ["endpoint"],
)
"""

P01_HIGH = Scenario(
    pattern="①",
    pattern_name="Asymmetric Defaults",
    severity="high",
    description="Gateway timeout=5s vs report service internal timeout=60s causes orphaned reports",
    visible_files={
        "gateway/proxy.py": _H_GATEWAY_A,
        "gateway/queue_config.py": _H_QUEUE,
        "frontend/api_client.py": _H_CLIENT,
        "monitoring/metrics.py": _H_METRICS,
    },
    annotated_files={
        "gateway/proxy.py": _H_GATEWAY_B,
    },
    hidden_file_name="services/report_service.py",
    hidden_file_description="Report generation has 60s internal timeout; completes after gateway gives up",
    questions=[
        Question(
            text=(
                "A user requests a financial report that takes 15 seconds to generate. "
                "The gateway proxies the request to report-service. "
                "What does the user see?"
            ),
            choices={
                "A": "The report is returned successfully after 15 seconds",
                "B": "A 504 timeout error after 5 seconds, even though the report "
                     "is being generated successfully on the backend",
                "C": "A 202 Accepted response with a polling URL to check status",
                "D": "A 500 Internal Server Error because the backend rejects long-running requests",
            },
            correct="B",
        ),
        Question(
            text=(
                "The frontend client retries 3 times after getting 504 errors. "
                "The report takes 8 seconds each time. "
                "How many report generation jobs actually complete on the backend?"
            ),
            choices={
                "A": "0 — the backend cancels processing when the gateway disconnects",
                "B": "1 — the backend deduplicates requests using X-Request-Id",
                "C": "3 — each retry triggers a new generation that completes independently "
                     "after the gateway has already returned 504",
                "D": "3 — but only the last one is stored; earlier ones are overwritten",
            },
            correct="C",
        ),
        Question(
            text=(
                "The ops team notices the gateway_timeout_total metric is high for /api/reports/generate. "
                "They increase BACKEND_TIMEOUT from 5 to 10 seconds. "
                "Reports that take 12 seconds will now:"
            ),
            choices={
                "A": "Succeed — 10 seconds is enough for most reports",
                "B": "Still fail with 504 — the backend service's internal 60s timeout means "
                     "reports taking 12s complete fine, but the gateway still times out at 10s",
                "C": "Succeed — the report service adjusts its processing to match the gateway timeout",
                "D": "Fail with a different error — the backend returns 408 Request Timeout",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ① × medium — ページサイズ非対称 (API=20 vs DB=100)
# =====================================================================

_M_API_A = """\
# api/notifications.py
from services.notification_service import NotificationService

notification_service = NotificationService()

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

def list_notifications(request):
    \"\"\"GET /notifications?page=1&per_page=20

    Returns paginated notifications for the authenticated user.
    Response includes pagination metadata.
    \"\"\"
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)

    result = notification_service.get_for_user(
        user_id=request.user_id,
        page=page,
        per_page=per_page,
    )
    return {
        "notifications": [n.to_dict() for n in result.items],
        "total": result.total,
        "page": page,
        "per_page": per_page,
        "total_pages": (result.total + per_page - 1) // per_page,
    }, 200
"""

_M_API_B = """\
# api/notifications.py
from services.notification_service import NotificationService

notification_service = NotificationService()

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

# ⚠ PAGE SIZE MISMATCH: This API accepts per_page up to 50, but
# notification_service.get_for_user() internally caps the query at 100 rows
# regardless of per_page — AND it ignores the per_page parameter entirely,
# always fetching 100 rows and slicing in Python. This means:
# 1. per_page=20 still loads 100 rows from DB (wasted I/O)
# 2. total_pages calculation here uses per_page=20, but the service
#    calculates total differently (using its own batch size of 100)

def list_notifications(request):
    \"\"\"GET /notifications?page=1&per_page=20

    Returns paginated notifications for the authenticated user.
    Response includes pagination metadata.
    \"\"\"
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)

    result = notification_service.get_for_user(
        user_id=request.user_id,
        page=page,
        per_page=per_page,
    )
    return {
        "notifications": [n.to_dict() for n in result.items],
        "total": result.total,
        "page": page,
        "per_page": per_page,
        "total_pages": (result.total + per_page - 1) // per_page,
    }, 200
"""

_M_MODEL = """\
# models/notification.py
from dataclasses import dataclass, field
from datetime import datetime
import uuid

@dataclass
class Notification:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    message: str = ""
    is_read: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
"""

_M_SCHEMA = """\
# db/schema.sql
CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_notifications_user ON notifications(user_id, created_at DESC);
"""

_M_BADGE = """\
# api/badge.py
from services.notification_service import NotificationService

notification_service = NotificationService()

def get_unread_count(request):
    \"\"\"GET /notifications/unread-count\"\"\"
    count = notification_service.count_unread(request.user_id)
    return {"unread": count}, 200
"""

P01_MEDIUM = Scenario(
    pattern="①",
    pattern_name="Asymmetric Defaults",
    severity="medium",
    description="API paginates at 20 per_page, but service always fetches 100 rows and slices in Python",
    visible_files={
        "api/notifications.py": _M_API_A,
        "models/notification.py": _M_MODEL,
        "db/schema.sql": _M_SCHEMA,
        "api/badge.py": _M_BADGE,
    },
    annotated_files={
        "api/notifications.py": _M_API_B,
    },
    hidden_file_name="services/notification_service.py",
    hidden_file_description="get_for_user() always loads 100 rows from DB, ignores per_page, slices in Python",
    questions=[
        Question(
            text=(
                "A user with 500 notifications calls GET /notifications?page=1&per_page=20. "
                "How many rows does the database query actually fetch?"
            ),
            choices={
                "A": "20 — the service passes per_page=20 to the SQL LIMIT clause",
                "B": "100 — the service always fetches its internal batch size regardless of per_page",
                "C": "50 — the API caps per_page at MAX_PAGE_SIZE=50",
                "D": "500 — the service fetches all notifications and paginates in memory",
            },
            correct="B",
        ),
        Question(
            text=(
                "The API returns total_pages = ceil(total / per_page) = ceil(500/20) = 25. "
                "A client iterates through all 25 pages. "
                "Does the client see all 500 notifications?"
            ),
            choices={
                "A": "Yes — the pagination is consistent between API and service layer",
                "B": "No — the service paginates by batches of 100, so pages beyond "
                     "the service's internal pagination boundary may return unexpected results",
                "C": "Yes — but with significant duplicate entries across pages",
                "D": "No — only the first 100 notifications are accessible",
            },
            correct="B",
        ),
        Question(
            text=(
                "The team wants to add a per_page=5 option for mobile clients to reduce "
                "payload size. They update MAX_PAGE_SIZE validation in the API. "
                "Will this actually reduce the database load?"
            ),
            choices={
                "A": "Yes — smaller per_page means fewer rows fetched from the database",
                "B": "No — the service layer ignores per_page and always fetches 100 rows; "
                     "only the Python slice changes",
                "C": "Partially — the ORM optimizes the query based on the slice size",
                "D": "Yes — but only if the database has proper LIMIT pushdown optimization",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ① × low — ログレベル非対称
# =====================================================================

_L_CONFIG_A = """\
# config/logging.py
\"\"\"Application-wide logging configuration.\"\"\"
import logging

LOG_LEVEL = "WARNING"
LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"

def setup_logging():
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    # Suppress noisy libraries
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
"""

_L_CONFIG_B = """\
# config/logging.py
\"\"\"Application-wide logging configuration.\"\"\"
import logging

LOG_LEVEL = "WARNING"
LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"

# ⚠ LOG LEVEL MISMATCH: This config sets WARNING as the global level,
# but services/cache_service.py hardcodes logging.DEBUG and logs every
# cache hit/miss with full key details (which may include user IDs and
# session tokens). In production, this generates ~50GB/day of debug logs
# that bypass the WARNING filter because the module sets its own level.

def setup_logging():
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    # Suppress noisy libraries
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
"""

_L_APP = """\
# app.py
from config.logging import setup_logging
from api import users, products

def create_app():
    setup_logging()
    app = App()
    app.register(users)
    app.register(products)
    return app
"""

_L_CACHE_USAGE = """\
# api/users.py
from services.cache_service import CacheService

cache = CacheService(prefix="users")

def get_user(request, user_id: str):
    cached = cache.get(f"user:{user_id}")
    if cached:
        return cached, 200
    user = user_service.get_by_id(user_id)
    if user:
        cache.set(f"user:{user_id}", user.to_dict(), ttl=300)
    return user.to_dict(), 200
"""

_L_DEPLOY = """\
# deploy/production.env
APP_ENV=production
LOG_LEVEL=WARNING
CACHE_TTL=300
DATABASE_POOL_SIZE=20
"""

P01_LOW = Scenario(
    pattern="①",
    pattern_name="Asymmetric Defaults",
    severity="low",
    description="Global log level is WARNING but cache service hardcodes DEBUG, leaking sensitive data",
    visible_files={
        "config/logging.py": _L_CONFIG_A,
        "app.py": _L_APP,
        "api/users.py": _L_CACHE_USAGE,
        "deploy/production.env": _L_DEPLOY,
    },
    annotated_files={
        "config/logging.py": _L_CONFIG_B,
    },
    hidden_file_name="services/cache_service.py",
    hidden_file_description="Hardcodes logger.setLevel(DEBUG) and logs all keys including user IDs",
    questions=[
        Question(
            text=(
                "In production with LOG_LEVEL=WARNING, a developer checks the application logs "
                "expecting to see only warnings and errors. "
                "Will they see debug-level cache hit/miss messages?"
            ),
            choices={
                "A": "No — the global WARNING level suppresses all DEBUG messages",
                "B": "Yes — the cache service overrides the global level by setting its own "
                     "logger to DEBUG, so cache messages bypass the global filter",
                "C": "No — basicConfig only applies the level once, all modules inherit it",
                "D": "Depends on whether the cache service logger is a child of the root logger",
            },
            correct="B",
        ),
        Question(
            text=(
                "The cache service logs cache keys like 'user:abc-123' at DEBUG level. "
                "If a log aggregation service indexes all application logs, "
                "are user IDs exposed in the log index?"
            ),
            choices={
                "A": "No — DEBUG logs are filtered out by the WARNING-level configuration",
                "B": "Yes — the cache service's DEBUG logs include user IDs in cache keys "
                     "and these are emitted regardless of the global log level setting",
                "C": "No — the log aggregation service only indexes WARNING and above",
                "D": "Only if the log aggregation service is configured to include DEBUG",
            },
            correct="B",
        ),
        Question(
            text=(
                "To reduce log volume, the ops team sets LOG_LEVEL=ERROR in production.env. "
                "How much will this reduce the cache-related log output?"
            ),
            choices={
                "A": "Completely — ERROR level will suppress all cache DEBUG messages",
                "B": "Not at all — the cache service sets its own logger level independently "
                     "of the global configuration",
                "C": "Partially — only cache ERROR messages will remain",
                "D": "Completely — basicConfig(level=ERROR) overrides all module-level settings",
            },
            correct="B",
        ),
    ],
)
