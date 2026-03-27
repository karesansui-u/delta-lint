"""⑤ Paired-Setting Override — 3重大度シナリオ.

high:   Redis TTL=3600s vs application cache invalidation=300s → stale data window
medium: max_connections=100 in app config vs connection pool=20 in hidden DB config
low:    retry_count=3 in API client vs max_retries=1 in hidden circuit breaker
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ⑤ × high — Cache TTL vs invalidation interval
# =====================================================================

_H_CONFIG_A = """\
# config/cache.py
\"\"\"Cache configuration.\"\"\"

CACHE_BACKEND = "redis"
REDIS_URL = "redis://cache:6379/0"

# Cache TTL settings
USER_PROFILE_TTL = 3600       # 1 hour
PRODUCT_CATALOG_TTL = 1800    # 30 minutes
SESSION_TTL = 86400           # 24 hours

# Cache invalidation
INVALIDATION_CHECK_INTERVAL = 300   # Check for stale entries every 5 minutes
"""

_H_CONFIG_B = """\
# config/cache.py
\"\"\"Cache configuration.\"\"\"

CACHE_BACKEND = "redis"
REDIS_URL = "redis://cache:6379/0"

# Cache TTL settings
USER_PROFILE_TTL = 3600       # 1 hour
PRODUCT_CATALOG_TTL = 1800    # 30 minutes
SESSION_TTL = 86400           # 24 hours

# Cache invalidation
INVALIDATION_CHECK_INTERVAL = 300   # Check for stale entries every 5 minutes

# ⚠ PAIRED-SETTING CONFLICT: USER_PROFILE_TTL (3600s) and
# INVALIDATION_CHECK_INTERVAL (300s) look related but they serve
# different purposes. The REAL problem is in services/cache_manager.py:
# it sets Redis TTL to 3600s but the invalidation job uses a SEPARATE
# "last_modified" timestamp with 300s granularity. When a user profile
# is updated, the invalidation job detects the change within 300s,
# BUT it only marks the key as "stale" — it does NOT delete it.
# The stale key is served for up to 3600s total (remaining TTL).
# There is a 0-3300 second window where stale data is served.
"""

_H_API = """\
# api/users.py
from services.cache_manager import CacheManager
from services.user_service import UserService

cache = CacheManager()
user_service = UserService()

def get_profile(request, user_id: str):
    \"\"\"GET /users/:id/profile — Returns cached user profile.\"\"\"
    cached = cache.get(f"profile:{user_id}")
    if cached:
        return cached, 200

    profile = user_service.get_profile(user_id)
    if not profile:
        return {"error": "Not found"}, 404

    cache.set(f"profile:{user_id}", profile.to_dict())
    return profile.to_dict(), 200

def update_profile(request, user_id: str):
    \"\"\"PATCH /users/:id/profile\"\"\"
    data = request.json
    profile = user_service.update_profile(user_id, data)
    # Note: cache is NOT explicitly invalidated here — relies on invalidation job
    return profile.to_dict(), 200
"""

_H_JOB = """\
# jobs/cache_invalidation.py
\"\"\"Background job: check for stale cache entries.\"\"\"
import time
from config.cache import INVALIDATION_CHECK_INTERVAL
from services.cache_manager import CacheManager

def run():
    cache = CacheManager()
    while True:
        stale_keys = cache.find_stale()
        for key in stale_keys:
            cache.mark_stale(key)
        time.sleep(INVALIDATION_CHECK_INTERVAL)
"""

_H_MODEL = """\
# models/user_profile.py
from dataclasses import dataclass
from datetime import datetime

@dataclass
class UserProfile:
    user_id: str = ""
    display_name: str = ""
    bio: str = ""
    avatar_url: str = ""
    updated_at: datetime = None
"""

P05_HIGH = Scenario(
    pattern="⑤",
    pattern_name="Paired-Setting Override",
    severity="high",
    description="Cache TTL=3600s but invalidation only marks stale, doesn't delete; up to 55min stale data",
    visible_files={
        "config/cache.py": _H_CONFIG_A,
        "api/users.py": _H_API,
        "jobs/cache_invalidation.py": _H_JOB,
        "models/user_profile.py": _H_MODEL,
    },
    annotated_files={
        "config/cache.py": _H_CONFIG_B,
    },
    hidden_file_name="services/cache_manager.py",
    hidden_file_description="mark_stale() only sets a flag; get() returns flagged entries until TTL expires",
    questions=[
        Question(
            text=(
                "A user updates their display name via PATCH /users/:id/profile. "
                "Another user views their profile 10 minutes later via GET /users/:id/profile. "
                "Do they see the updated name?"
            ),
            choices={
                "A": "Yes — the invalidation job detected the change within 5 minutes and refreshed the cache",
                "B": "No — the old name is served from cache; the invalidation job marks it stale "
                     "but doesn't delete it, and the 1-hour TTL hasn't expired yet",
                "C": "Yes — the update endpoint invalidates the cache immediately",
                "D": "Depends on whether Redis evicted the key due to memory pressure",
            },
            correct="B",
        ),
        Question(
            text=(
                "The invalidation job finds stale keys every 5 minutes and calls mark_stale(). "
                "How long after a profile update could the old data still be served?"
            ),
            choices={
                "A": "At most 5 minutes — the invalidation job refreshes stale keys",
                "B": "Up to 55 minutes — the invalidation marks keys stale within 5 min, "
                     "but stale keys continue to be served until their 1-hour TTL expires",
                "C": "Exactly 1 hour — the TTL always runs its full duration",
                "D": "At most 10 minutes — mark_stale reduces the remaining TTL to 5 minutes",
            },
            correct="B",
        ),
        Question(
            text=(
                "The team reduces INVALIDATION_CHECK_INTERVAL from 300s to 30s, hoping to "
                "reduce stale data. Does this fix the stale data problem?"
            ),
            choices={
                "A": "Yes — checking more frequently means stale data is caught and refreshed faster",
                "B": "No — the interval only affects how quickly staleness is detected; the actual "
                     "problem is that mark_stale() doesn't remove the cached value, so it's still "
                     "served until TTL expires",
                "C": "Partially — it reduces the detection window but not the serving window",
                "D": "Yes — but it increases Redis CPU usage proportionally",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑤ × medium — max_connections vs pool size
# =====================================================================

_M_CONFIG_A = """\
# config/database.py
\"\"\"Database configuration.\"\"\"

DATABASE_URL = "postgresql://app:pass@db:5432/myapp"
MAX_CONNECTIONS = 100       # Application-level max connections
CONNECTION_TIMEOUT = 30     # Seconds to wait for a connection
QUERY_TIMEOUT = 60          # Maximum query execution time
POOL_RECYCLE = 3600         # Recycle connections after 1 hour
"""

_M_CONFIG_B = """\
# config/database.py
\"\"\"Database configuration.\"\"\"

DATABASE_URL = "postgresql://app:pass@db:5432/myapp"
MAX_CONNECTIONS = 100       # Application-level max connections
CONNECTION_TIMEOUT = 30     # Seconds to wait for a connection
QUERY_TIMEOUT = 60          # Maximum query execution time
POOL_RECYCLE = 3600         # Recycle connections after 1 hour

# ⚠ PAIRED-SETTING CONFLICT: MAX_CONNECTIONS=100 but the actual connection
# pool in services/db_pool.py is hardcoded to pool_size=20, max_overflow=5.
# This means only 25 connections can exist simultaneously.
# Under load, the app config suggests 100 connections should work,
# but workers will block on pool exhaustion after 25 concurrent queries.
# CONNECTION_TIMEOUT (30s) applies to query execution, NOT pool waiting.
# Pool wait timeout is hardcoded at 10s in db_pool.py.
"""

_M_APP = """\
# app.py
from config.database import MAX_CONNECTIONS, CONNECTION_TIMEOUT
import logging

logger = logging.getLogger(__name__)

def startup():
    logger.info(f"Starting with max {MAX_CONNECTIONS} DB connections, "
                f"timeout={CONNECTION_TIMEOUT}s")
    # ... app initialization
"""

_M_HEALTH = """\
# api/health.py
from config.database import MAX_CONNECTIONS
from services.db_pool import get_pool

def health_check(request):
    pool = get_pool()
    in_use = pool.checked_out()
    return {
        "status": "healthy",
        "db_connections_in_use": in_use,
        "db_connections_max": MAX_CONNECTIONS,
        "db_utilization": f"{in_use / MAX_CONNECTIONS:.0%}",
    }, 200
"""

_M_WORKER = """\
# workers/report_worker.py
\"\"\"Worker process for generating reports.\"\"\"
from config.database import MAX_CONNECTIONS
import logging

logger = logging.getLogger(__name__)

MAX_CONCURRENT_REPORTS = MAX_CONNECTIONS // 4  # 25 concurrent reports

def process_report(report_id: str):
    # Each report needs 1 DB connection
    logger.info(f"Processing report {report_id}, "
                f"capacity: {MAX_CONCURRENT_REPORTS} concurrent")
    # ... report generation
"""

P05_MEDIUM = Scenario(
    pattern="⑤",
    pattern_name="Paired-Setting Override",
    severity="medium",
    description="App config says max_connections=100 but DB pool is hardcoded to 25; health check reports wrong utilization",
    visible_files={
        "config/database.py": _M_CONFIG_A,
        "app.py": _M_APP,
        "api/health.py": _M_HEALTH,
        "workers/report_worker.py": _M_WORKER,
    },
    annotated_files={
        "config/database.py": _M_CONFIG_B,
    },
    hidden_file_name="services/db_pool.py",
    hidden_file_description="pool_size=20, max_overflow=5 (total max 25); pool wait timeout=10s",
    questions=[
        Question(
            text=(
                "During peak load, 30 concurrent requests each need a DB connection. "
                "The health check shows db_utilization=30%. Based on the config, "
                "MAX_CONNECTIONS=100 so 30/100=30% seems fine. Is the system actually fine?"
            ),
            choices={
                "A": "Yes — 30% utilization with 70 connections still available",
                "B": "No — the actual pool limit is 25, so 30 concurrent requests exceeds it; "
                     "5 requests will block and timeout despite the health check showing 30%",
                "C": "Yes — the connection pool auto-scales to match MAX_CONNECTIONS",
                "D": "No — but because of query timeout, not connection limits",
            },
            correct="B",
        ),
        Question(
            text=(
                "The report worker calculates MAX_CONCURRENT_REPORTS = 100 // 4 = 25 concurrent reports. "
                "Each report uses 1 DB connection. Can 25 reports actually run concurrently?"
            ),
            choices={
                "A": "Yes — 25 connections are available out of the 100 max",
                "B": "Barely — the real pool max is 25 total, and 25 reports would consume "
                     "ALL connections, leaving zero for API requests",
                "C": "No — the pool only allows 20 concurrent connections",
                "D": "Yes — but some reports may be queued if other connections are in use",
            },
            correct="B",
        ),
        Question(
            text=(
                "An ops engineer increases MAX_CONNECTIONS from 100 to 200 in config/database.py "
                "to handle growing traffic. Does the actual connection capacity change?"
            ),
            choices={
                "A": "Yes — the pool reads MAX_CONNECTIONS and adjusts its size",
                "B": "No — the pool size is hardcoded in db_pool.py and ignores the config value; "
                     "only the health check and worker calculations change (becoming even more misleading)",
                "C": "Yes — but requires a restart to take effect",
                "D": "Partially — only max_overflow increases, not pool_size",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑤ × low — retry count vs circuit breaker
# =====================================================================

_L_CLIENT_A = """\
# clients/payment_client.py
\"\"\"Client for external payment API.\"\"\"
import requests
import logging

logger = logging.getLogger(__name__)

PAYMENT_API_URL = "https://payments.example.com/v1"
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # seconds

class PaymentClient:
    def charge(self, amount: float, token: str) -> dict:
        \"\"\"Charge a payment with automatic retry on failure.\"\"\"
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.post(
                    f"{PAYMENT_API_URL}/charges",
                    json={"amount": amount, "token": token},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code >= 500:
                    logger.warning(f"Payment API error (attempt {attempt+1}/{MAX_RETRIES})")
                    continue
                return resp.json()
            except requests.Timeout:
                logger.warning(f"Payment API timeout (attempt {attempt+1}/{MAX_RETRIES})")
                continue
        raise RuntimeError(f"Payment failed after {MAX_RETRIES} retries")
"""

_L_CLIENT_B = """\
# clients/payment_client.py
\"\"\"Client for external payment API.\"\"\"
import requests
import logging

logger = logging.getLogger(__name__)

PAYMENT_API_URL = "https://payments.example.com/v1"
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # seconds

# ⚠ PAIRED-SETTING CONFLICT: MAX_RETRIES=3 here but the circuit breaker
# in middleware/circuit_breaker.py trips after just 1 consecutive failure.
# When the payment API has a transient error, the first call fails,
# the circuit breaker opens, and retries 2 and 3 are immediately rejected
# by the breaker (not even sent to the payment API).
# The effective retry count is always 1, not 3.

class PaymentClient:
    def charge(self, amount: float, token: str) -> dict:
        \"\"\"Charge a payment with automatic retry on failure.\"\"\"
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.post(
                    f"{PAYMENT_API_URL}/charges",
                    json={"amount": amount, "token": token},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code >= 500:
                    logger.warning(f"Payment API error (attempt {attempt+1}/{MAX_RETRIES})")
                    continue
                return resp.json()
            except requests.Timeout:
                logger.warning(f"Payment API timeout (attempt {attempt+1}/{MAX_RETRIES})")
                continue
        raise RuntimeError(f"Payment failed after {MAX_RETRIES} retries")
"""

_L_CONFIG = """\
# config/external_apis.py
PAYMENT_API_KEY = "sk_live_..."
PAYMENT_API_URL = "https://payments.example.com/v1"
PAYMENT_TIMEOUT = 10
PAYMENT_MAX_RETRIES = 3
"""

_L_ORDER = """\
# api/checkout.py
from clients.payment_client import PaymentClient

payment = PaymentClient()

def process_checkout(request):
    try:
        result = payment.charge(
            amount=request.json["total"],
            token=request.json["payment_token"],
        )
        return {"status": "success", "charge_id": result["id"]}, 200
    except RuntimeError as e:
        return {"error": str(e)}, 502
"""

_L_MONITOR = """\
# monitoring/alerts.py
\"\"\"Alert configuration.\"\"\"

ALERTS = {
    "payment_failure_rate": {
        "threshold": 0.1,  # Alert if >10% of payments fail
        "window": "5m",
        "description": "High payment failure rate (after retries)",
    },
}
"""

P05_LOW = Scenario(
    pattern="⑤",
    pattern_name="Paired-Setting Override",
    severity="low",
    description="Client retries 3 times but circuit breaker trips after 1 failure, making retries ineffective",
    visible_files={
        "clients/payment_client.py": _L_CLIENT_A,
        "config/external_apis.py": _L_CONFIG,
        "api/checkout.py": _L_ORDER,
        "monitoring/alerts.py": _L_MONITOR,
    },
    annotated_files={
        "clients/payment_client.py": _L_CLIENT_B,
    },
    hidden_file_name="middleware/circuit_breaker.py",
    hidden_file_description="Circuit breaker with failure_threshold=1; opens after single failure",
    questions=[
        Question(
            text=(
                "The payment API has a brief 2-second outage. A customer's first charge attempt "
                "fails (500 error). The client is configured with MAX_RETRIES=3. "
                "How many actual requests reach the payment API?"
            ),
            choices={
                "A": "3 — the client retries twice after the initial failure",
                "B": "1 — the circuit breaker opens after the first failure, and retries 2 and 3 "
                     "are rejected locally without reaching the payment API",
                "C": "2 — the second attempt succeeds because the 2-second outage is over",
                "D": "3 — but with exponential backoff between each attempt",
            },
            correct="B",
        ),
        Question(
            text=(
                "The ops team sees logs showing 'Payment failed after 3 retries' but the payment "
                "API's dashboard shows only 1 request received. What explains the discrepancy?"
            ),
            choices={
                "A": "Network issues caused retries 2 and 3 to be lost in transit",
                "B": "The circuit breaker rejected retries 2 and 3 locally — they never left the "
                     "application, so the payment API only saw the initial request",
                "C": "The payment API's dashboard has a counting bug",
                "D": "The retries were sent to a different payment API endpoint",
            },
            correct="B",
        ),
        Question(
            text=(
                "To improve payment reliability, the team increases MAX_RETRIES from 3 to 5 "
                "in config. Will this actually improve the success rate during transient failures?"
            ),
            choices={
                "A": "Yes — more retries means more chances for the request to succeed",
                "B": "No — the circuit breaker still trips after 1 failure, so retries 2-5 "
                     "are all rejected locally; the effective retry count remains 1",
                "C": "Partially — it helps for timeout errors but not 500 errors",
                "D": "Yes — but only if the circuit breaker reset time is shorter than the retry delay",
            },
            correct="B",
        ),
    ],
)
