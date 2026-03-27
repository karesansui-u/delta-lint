"""Phase 0 calibration scenarios.

6 patterns × 3 severities = 18 cells.
Each scenario follows the partial-context protocol (§5.2.1):
  - visible_code: files the LLM can see
  - hidden_behavior: what the hidden file actually does (used in condition B annotation)
  - questions: 3 questions with A/B/C options, one correct answer
  - Correct answer MUST require knowledge of hidden_behavior
"""

# ---------------------------------------------------------------------------
# Scenario format
# ---------------------------------------------------------------------------
# {
#     "id": "①-high",
#     "pattern": "①",
#     "pattern_name": "Asymmetric Defaults",
#     "severity": "high",
#     "visible_code": "...",
#     "hidden_behavior": "...",    # annotation for condition B
#     "questions": [
#         {
#             "q": "question text",
#             "options": {"A": "...", "B": "...", "C": "..."},
#             "correct": "B",
#         },
#         ...
#     ],
# }

SCENARIOS = [
    # ===================================================================
    # ① Asymmetric Defaults
    # ===================================================================
    {
        "id": "①-high",
        "pattern": "①",
        "pattern_name": "Asymmetric Defaults",
        "severity": "high",
        "visible_code": """
# payment_api.py — Payment REST API
from flask import Flask, request, jsonify
from payment_service import charge_customer

app = Flask(__name__)

@app.route("/api/payments", methods=["POST"])
def create_payment():
    \"\"\"Create a payment. Amount is in dollars (e.g. 29.99).\"\"\"
    data = request.get_json()
    amount = float(data["amount"])  # dollar amount from client
    customer_id = data["customer_id"]
    result = charge_customer(customer_id, amount)
    return jsonify({"status": "ok", "transaction_id": result["tx_id"]})

@app.route("/api/payments/<tx_id>", methods=["GET"])
def get_payment(tx_id):
    \"\"\"Returns payment details. amount field is in dollars.\"\"\"
    # ... lookup and return
    pass

@app.route("/api/refunds", methods=["POST"])
def create_refund():
    \"\"\"Refund a payment. Amount is in dollars.\"\"\"
    data = request.get_json()
    amount = float(data["amount"])
    tx_id = data["transaction_id"]
    result = charge_customer(data["customer_id"], -amount)
    return jsonify({"status": "refunded", "refund_id": result["tx_id"]})
""",
        "hidden_behavior": "payment_service.charge_customer() interprets the amount parameter in CENTS, not dollars. Internally it does `stripe.PaymentIntent.create(amount=amount)` where Stripe expects cents. So passing amount=29.99 from the API (intended as $29.99) actually charges $0.30 (29.99 cents, rounded to 30 cents).",
        "questions": [
            {
                "q": "A customer submits a payment of amount=100 through POST /api/payments. How much is actually charged to their card?",
                "options": {
                    "A": "$100.00",
                    "B": "$1.00 (100 cents)",
                    "C": "$10.00",
                },
                "correct": "B",
            },
            {
                "q": "A $50 purchase is processed through the API. The customer then requests a full refund via POST /api/refunds with amount=50. What is the net effect on the customer's account?",
                "options": {
                    "A": "Net zero — $50 charged then $50 refunded",
                    "B": "$0.50 charged then $0.50 refunded (net zero but wrong amounts)",
                    "C": "$0.50 charged then $50.00 refunded (customer gains $49.50)",
                },
                "correct": "B",
            },
            {
                "q": "The team notices revenue is 100x lower than expected. Where is the bug?",
                "options": {
                    "A": "The API is sending the wrong currency code to Stripe",
                    "B": "charge_customer() treats the dollar amount as cents, so all charges are 1/100th of intended",
                    "C": "The API is accidentally applying a 99% discount",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "①-medium",
        "pattern": "①",
        "pattern_name": "Asymmetric Defaults",
        "severity": "medium",
        "visible_code": """
# config_loader.py — Application configuration
import os

def load_config():
    return {
        "db_host": os.getenv("DB_HOST", "localhost"),
        "db_port": int(os.getenv("DB_PORT", "5432")),
        "request_timeout": int(os.getenv("REQUEST_TIMEOUT", "30")),
        "max_retries": int(os.getenv("MAX_RETRIES", "3")),
    }

# http_client.py — HTTP client used by all services
import requests
from config_loader import load_config

config = load_config()

def fetch(url, **kwargs):
    \"\"\"Fetch URL with configured timeout.\"\"\"
    return requests.get(
        url,
        timeout=config["request_timeout"],
        **kwargs
    )
""",
        "hidden_behavior": "The requests.get() timeout parameter accepts seconds, but the external API gateway the app connects to has a hard 5-second timeout. Any request taking longer than 5s is killed by the gateway, but the client keeps the socket open for the full 30 seconds before raising a timeout error. This means the client waits 25 seconds for a response that will never come on gateway-timeout scenarios.",
        "questions": [
            {
                "q": "An external API call takes 8 seconds to respond. What does the user experience?",
                "options": {
                    "A": "The response arrives after 8 seconds normally",
                    "B": "The request fails after 5 seconds with a gateway timeout, but the client immediately surfaces the error",
                    "C": "The gateway kills the request at 5 seconds, but the client waits the full 30 seconds before showing a timeout error",
                },
                "correct": "C",
            },
            {
                "q": "Setting REQUEST_TIMEOUT=5 would fix the slow timeout issue. Why wasn't this the default?",
                "options": {
                    "A": "The developer set 30s as a safe default without knowing the gateway has a 5s limit",
                    "B": "30s is needed for batch operations that bypass the gateway",
                    "C": "The timeout value is ignored by the requests library",
                },
                "correct": "A",
            },
            {
                "q": "Under load, the application shows many connections in CLOSE_WAIT state. What is the likely cause?",
                "options": {
                    "A": "The database connection pool is exhausted",
                    "B": "The HTTP client holds sockets open for 30s after the gateway already closed them at 5s",
                    "C": "The max_retries setting is too high",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "①-low",
        "pattern": "①",
        "pattern_name": "Asymmetric Defaults",
        "severity": "low",
        "visible_code": """
# logger.py — Application logger
import logging
from datetime import datetime

def setup_logger(name):
    logger = logging.getLogger(name)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger

# The log output uses the server's local timezone (JST, UTC+9) by default.
# Example output: 2026-03-27 14:30:00 [INFO] app: Request processed
""",
        "hidden_behavior": "The log aggregation service (Datadog) parses all timestamps as UTC. When correlating logs across services, JST timestamps are interpreted as UTC, causing a 9-hour offset. An event logged at 14:30 JST appears as 14:30 UTC (actually 23:30 JST) in the dashboard.",
        "questions": [
            {
                "q": "An incident occurred at 2026-03-27 06:00 UTC. An engineer searches Datadog for logs from that time window. What happens?",
                "options": {
                    "A": "The correct logs are found immediately",
                    "B": "Logs from 15:00 JST (06:00 UTC) are shown, which are actually from 9 hours later than the incident",
                    "C": "No logs are found because the service was down",
                },
                "correct": "B",
            },
            {
                "q": "Cross-service tracing shows Service A's log entry appears 9 hours after Service B's for the same request. What is happening?",
                "options": {
                    "A": "Service A has extreme latency",
                    "B": "The tracing system has a bug in time ordering",
                    "C": "Service A logs in JST but Datadog interprets it as UTC, creating a 9-hour phantom offset",
                },
                "correct": "C",
            },
            {
                "q": "What is the simplest fix for this log correlation issue?",
                "options": {
                    "A": "Switch Datadog to JST timezone",
                    "B": "Change the logger to output timestamps in UTC (or include timezone info in the format)",
                    "C": "Add 9 hours to all Datadog queries manually",
                },
                "correct": "B",
            },
        ],
    },

    # ===================================================================
    # ② Semantic Mismatch
    # ===================================================================
    {
        "id": "②-high",
        "pattern": "②",
        "pattern_name": "Semantic Mismatch",
        "severity": "high",
        "visible_code": """
# auth_middleware.py — Authorization check
from user_service import get_user

def require_admin(handler):
    def wrapper(request):
        user = get_user(request.user_id)
        if user["role"] == "admin":
            return handler(request)
        return {"status": 403, "error": "Admin access required"}
    return wrapper

@require_admin
def delete_all_users(request):
    \"\"\"Dangerous operation: only admins can do this.\"\"\"
    # ... delete logic
    pass

@require_admin
def export_user_data(request):
    \"\"\"GDPR export: admin only.\"\"\"
    # ... export logic
    pass
""",
        "hidden_behavior": "user_service.get_user() returns the `role` field as a COMMA-SEPARATED STRING of all roles the user has (e.g. 'admin,editor,viewer'). It is NOT a single role value. So `user['role'] == 'admin'` only matches users who have EXACTLY the single role 'admin', not users who have admin among multiple roles like 'admin,editor'.",
        "questions": [
            {
                "q": "A user with roles 'admin,editor' tries to access delete_all_users. What happens?",
                "options": {
                    "A": "Access granted — the user has admin role",
                    "B": "Access denied — 'admin,editor' != 'admin' (string equality fails)",
                    "C": "Error — the role field can't hold multiple values",
                },
                "correct": "B",
            },
            {
                "q": "Only users with the single role 'admin' (no other roles) can perform admin operations. Is this the intended behavior?",
                "options": {
                    "A": "Yes, this is a security feature — pure admins only",
                    "B": "No, it's a bug — the check uses string equality instead of substring/list check on a comma-separated role field",
                    "C": "It depends on the RBAC policy configuration",
                },
                "correct": "B",
            },
            {
                "q": "A new super-admin user is created with role='admin'. They can access admin endpoints. Later, they're given editor access too (role becomes 'admin,editor'). What changes?",
                "options": {
                    "A": "Nothing — they still have admin access",
                    "B": "They lose all admin access because the string comparison breaks",
                    "C": "They get additional editor permissions on top of admin",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "②-medium",
        "pattern": "②",
        "pattern_name": "Semantic Mismatch",
        "severity": "medium",
        "visible_code": """
# cache_manager.py — Application cache
import hashlib

class CacheManager:
    def __init__(self):
        self._store = {}

    def get(self, key: str):
        \"\"\"Get cached value by key.\"\"\"
        return self._store.get(key)

    def set(self, key: str, value, ttl: int = 3600):
        \"\"\"Set cached value. key is a string identifier like 'user:123'.\"\"\"
        self._store[key] = {"value": value, "ttl": ttl}

    def invalidate(self, key: str):
        \"\"\"Remove a cache entry by key.\"\"\"
        self._store.pop(key, None)

cache = CacheManager()

# Usage in user_service.py:
# cache.set(f"user:{user_id}", user_data)
# cached = cache.get(f"user:{user_id}")
""",
        "hidden_behavior": "The session_service module also uses the same CacheManager instance, but it uses `key` to mean a cryptographic SESSION KEY (a 32-byte random token), not a cache identifier. It does `cache.set(session_key, session_data)` where session_key is like 'a3f8b2...'. When cache.invalidate() is called during cleanup with a session key, it works fine. But the collision risk is that session keys could theoretically match cache identifiers like 'user:123', and more importantly, a bulk cache flush intended for user data would also destroy all active sessions.",
        "questions": [
            {
                "q": "An admin runs a 'flush user cache' operation that calls cache.invalidate() for all keys starting with 'user:'. What happens to active sessions?",
                "options": {
                    "A": "Sessions are unaffected — they use different key patterns",
                    "B": "All sessions are destroyed because they share the same cache store",
                    "C": "Sessions are preserved because session keys are hex strings that never start with 'user:'",
                },
                "correct": "C",
            },
            {
                "q": "A developer adds a cache.clear_all() method and calls it during deployment. What breaks?",
                "options": {
                    "A": "Only cached user data is lost — minor inconvenience",
                    "B": "All active user sessions are also destroyed, logging everyone out",
                    "C": "Nothing breaks — sessions are stored in a separate database",
                },
                "correct": "B",
            },
            {
                "q": "The team wants to add cache size monitoring. They count entries and see 50,000 items but only expect 5,000 user cache entries. Why?",
                "options": {
                    "A": "Cache eviction is not working properly",
                    "B": "The remaining 45,000 entries are active session keys stored in the same cache instance",
                    "C": "There's a memory leak in the cache implementation",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "②-low",
        "pattern": "②",
        "pattern_name": "Semantic Mismatch",
        "severity": "low",
        "visible_code": """
# models/order.py — Order model
class Order:
    def __init__(self, id, status, items):
        self.id = id
        self.status = status  # "active", "completed", "cancelled"
        self.items = items

    def is_active(self):
        return self.status == "active"

# In order_service.py:
# active_orders = [o for o in orders if o.is_active()]
# → Returns orders that are currently being processed (not yet shipped)
""",
        "hidden_behavior": "In subscription_service.py, `is_active()` on a Subscription object returns True when the subscription has not expired (i.e., end_date > now). When the dashboard calls `get_active_items()` which aggregates both orders and subscriptions using `item.is_active()`, 'active' means 'in-progress' for orders but 'not-expired' for subscriptions. A completed order is not active, but a paid subscription that is just sitting there IS active. This creates confusing counts in the dashboard.",
        "questions": [
            {
                "q": "The dashboard shows '150 active items'. There are 50 in-progress orders and 200 paid subscriptions (180 not expired, 20 expired). What is the actual breakdown?",
                "options": {
                    "A": "50 active orders + 100 active subscriptions = 150",
                    "B": "50 active orders + 180 active subscriptions = 230 (dashboard is wrong)",
                    "C": "50 in-progress orders + 180 non-expired subscriptions = 230, but the dashboard only counts orders",
                },
                "correct": "B",
            },
            {
                "q": "A manager asks 'how many active orders do we have?' and the developer queries get_active_items(). The result includes subscriptions. Why?",
                "options": {
                    "A": "The query has a bug in its SQL WHERE clause",
                    "B": "get_active_items() aggregates both orders and subscriptions using is_active(), which means different things for each type",
                    "C": "Subscriptions are a type of order in the data model",
                },
                "correct": "B",
            },
            {
                "q": "What is the root cause of the dashboard confusion?",
                "options": {
                    "A": "The database schema is incorrectly normalized",
                    "B": "'active' has different semantic meaning in Order (in-progress) vs Subscription (not-expired) but both use the same is_active() interface",
                    "C": "The dashboard frontend has a rendering bug",
                },
                "correct": "B",
            },
        ],
    },

    # ===================================================================
    # ③ External Spec Divergence
    # ===================================================================
    {
        "id": "③-high",
        "pattern": "③",
        "pattern_name": "External Spec Divergence",
        "severity": "high",
        "visible_code": """
# oauth2_server.py — OAuth2 token endpoint (RFC 6749 compliant)
from flask import Flask, request, jsonify
import secrets
import time

app = Flask(__name__)

@app.route("/oauth/token", methods=["POST"])
def token():
    \"\"\"Issue access token per RFC 6749 §5.1.\"\"\"
    grant_type = request.form.get("grant_type")
    if grant_type != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400

    code = request.form.get("code")
    if not validate_code(code):
        return jsonify({"error": "invalid_grant"}), 400

    access_token = secrets.token_urlsafe(32)
    return jsonify({
        "access_token": access_token,
        "expires_in": 3600,
    })
""",
        "hidden_behavior": "RFC 6749 §5.1 REQUIRES the token response to include a `token_type` field (typically 'Bearer'). The implementation omits `token_type` from the response. Spec-compliant OAuth2 clients (like most libraries) expect this field and will either raise an error or fail to properly construct Authorization headers, since they don't know whether to use 'Bearer', 'MAC', or another scheme.",
        "questions": [
            {
                "q": "A developer integrates this OAuth2 server with a standard Python `requests-oauthlib` client. The token request succeeds (200 OK) but subsequent API calls fail with 401. Why?",
                "options": {
                    "A": "The access token expired immediately",
                    "B": "The response is missing `token_type`, so the client doesn't know to send 'Bearer <token>' in the Authorization header",
                    "C": "The CORS configuration is blocking the Authorization header",
                },
                "correct": "B",
            },
            {
                "q": "The custom frontend app works fine with this OAuth2 server because it hardcodes 'Authorization: Bearer <token>'. What happens when a mobile team uses the standard Google OAuth2 library?",
                "options": {
                    "A": "It works the same way — the library handles missing fields gracefully",
                    "B": "The library throws a 'missing token_type' error during token parsing",
                    "C": "The library defaults to Bearer and it works",
                },
                "correct": "B",
            },
            {
                "q": "An audit flags the OAuth2 server as 'non-compliant with RFC 6749'. Which specific requirement is violated?",
                "options": {
                    "A": "The token is not signed (should be JWT)",
                    "B": "The token response omits the REQUIRED `token_type` field (§5.1)",
                    "C": "The server doesn't support refresh tokens",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "③-medium",
        "pattern": "③",
        "pattern_name": "External Spec Divergence",
        "severity": "medium",
        "visible_code": """
# http_cache.py — HTTP response caching middleware
class CacheMiddleware:
    def __init__(self, app):
        self.app = app
        self._cache = {}

    def __call__(self, request):
        cache_control = request.headers.get("Cache-Control", "")

        if "no-cache" in cache_control:
            # Client says don't use cache — skip cache entirely
            self._cache.pop(request.url, None)
            response = self.app(request)
            return response

        cached = self._cache.get(request.url)
        if cached and not cached.is_stale():
            return cached

        response = self.app(request)
        if response.status == 200:
            self._cache[request.url] = response
        return response
""",
        "hidden_behavior": "Per HTTP spec (RFC 7234 §5.2.1.4), `no-cache` does NOT mean 'don't cache'. It means 'you may cache, but MUST revalidate with the origin server before using the cached response' (via If-None-Match/If-Modified-Since). The implementation treats `no-cache` as `no-store` (purges cache and never stores). The correct behavior for `no-store` is what's implemented for `no-cache`. This means: (1) cached responses are unnecessarily purged, (2) revalidation is never attempted (missing conditional requests), (3) `no-store` is not implemented at all.",
        "questions": [
            {
                "q": "A client sends `Cache-Control: no-cache` expecting the server to revalidate the cached version with an If-None-Match check. What actually happens?",
                "options": {
                    "A": "The server revalidates as expected and returns 304 if unchanged",
                    "B": "The server purges the cached response and fetches a full new response, wasting bandwidth",
                    "C": "The server ignores the header and returns the stale cached version",
                },
                "correct": "B",
            },
            {
                "q": "A CDN in front of this server sends `Cache-Control: no-store` to prevent sensitive data from being cached. Does this work?",
                "options": {
                    "A": "Yes — no-store is handled correctly",
                    "B": "No — the server has no handler for no-store, so sensitive data IS cached",
                    "C": "Partially — it only works for GET requests",
                },
                "correct": "B",
            },
            {
                "q": "After 'fixing' the no-cache handling, bandwidth usage drops 40%. Why?",
                "options": {
                    "A": "The fix enabled gzip compression",
                    "B": "Proper no-cache revalidation returns 304 Not Modified for unchanged resources instead of full re-downloads",
                    "C": "The fix reduced the number of cache entries",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "③-low",
        "pattern": "③",
        "pattern_name": "External Spec Divergence",
        "severity": "low",
        "visible_code": """
# csv_export.py — Export data to CSV (RFC 4180)
import io

def export_to_csv(records, fields):
    \"\"\"Export records to CSV format per RFC 4180.\"\"\"
    output = io.StringIO()

    # Header
    output.write(",".join(fields) + "\\n")

    # Data rows
    for record in records:
        row = []
        for field in fields:
            value = str(record.get(field, ""))
            if "," in value or '"' in value:
                value = '"' + value.replace('"', '""') + '"'
            row.append(value)
        output.write(",".join(row) + "\\n")

    return output.getvalue()
""",
        "hidden_behavior": "RFC 4180 specifies CRLF (\\r\\n) as the line ending, not LF (\\n). The implementation uses LF only. Most modern CSV parsers tolerate this, but: (1) strict parsers (some financial/government systems) reject the file, (2) when the CSV is opened in older Windows tools, all rows appear on one line, (3) automated validation against RFC 4180 schema will flag every line as non-compliant.",
        "questions": [
            {
                "q": "A bank's automated CSV import system rejects the exported file with 'invalid line terminator'. What is the issue?",
                "options": {
                    "A": "The CSV has trailing whitespace in fields",
                    "B": "The file uses LF (\\n) line endings instead of CRLF (\\r\\n) as required by RFC 4180",
                    "C": "The header row is missing required bank-specific columns",
                },
                "correct": "B",
            },
            {
                "q": "The CSV works fine in Google Sheets and Python pandas, but looks like a single long line in Windows Notepad (older versions). Why?",
                "options": {
                    "A": "The file encoding is wrong (should be UTF-16)",
                    "B": "Old Windows Notepad requires CRLF to display line breaks; the file only has LF",
                    "C": "The CSV is too large for Notepad to render",
                },
                "correct": "B",
            },
            {
                "q": "An RFC 4180 compliance validator flags 100% of the rows. The field quoting and escaping are correct. What is failing?",
                "options": {
                    "A": "The BOM (byte order mark) is missing",
                    "B": "Every line uses LF instead of the required CRLF terminator",
                    "C": "The header row should not have a line ending",
                },
                "correct": "B",
            },
        ],
    },

    # ===================================================================
    # ④ Guard Non-Propagation
    # ===================================================================
    {
        "id": "④-high",
        "pattern": "④",
        "pattern_name": "Guard Non-Propagation",
        "severity": "high",
        "visible_code": """
# api_server.py — REST API with rate limiting
from flask import Flask, request
from rate_limiter import RateLimiter

app = Flask(__name__)
limiter = RateLimiter(max_requests=100, window=60)  # 100 req/min

@app.before_request
def check_rate_limit():
    client_ip = request.remote_addr
    if not limiter.allow(client_ip):
        return {"error": "rate limit exceeded"}, 429

@app.route("/api/data", methods=["GET"])
def get_data():
    return {"data": fetch_all_data()}

@app.route("/api/data/<id>", methods=["GET"])
def get_item(id):
    return {"item": fetch_item(id)}

@app.route("/api/data", methods=["POST"])
def create_item():
    return {"created": create_new_item(request.json)}
""",
        "hidden_behavior": "The application also has a WebSocket endpoint (ws_handler.py) that provides real-time data streaming. The WebSocket handler is registered as a separate ASGI app and does NOT go through Flask's before_request hooks. It has no rate limiting at all. A client can open unlimited WebSocket connections and request unlimited data through the streaming endpoint, completely bypassing the REST API's rate limiter.",
        "questions": [
            {
                "q": "An attacker finds the /ws/stream WebSocket endpoint. Can they bypass the rate limiter?",
                "options": {
                    "A": "No — the rate limiter applies to all connections at the network level",
                    "B": "Yes — the WebSocket handler is a separate ASGI app that doesn't go through Flask's before_request hooks",
                    "C": "Partially — WebSocket connections count toward the rate limit but at a different rate",
                },
                "correct": "B",
            },
            {
                "q": "The ops team sees 50,000 requests/min from one IP but the rate limiter shows no violations. How is this possible?",
                "options": {
                    "A": "The rate limiter has a bug that miscounts requests",
                    "B": "The requests are going through the WebSocket endpoint which bypasses the rate limiter entirely",
                    "C": "The IP is whitelisted in the rate limiter configuration",
                },
                "correct": "B",
            },
            {
                "q": "To fix this, what is the correct approach?",
                "options": {
                    "A": "Increase the rate limit to handle more traffic",
                    "B": "Add rate limiting to the WebSocket handler independently, or move rate limiting to a shared middleware layer that covers both REST and WebSocket",
                    "C": "Block WebSocket connections entirely",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "④-medium",
        "pattern": "④",
        "pattern_name": "Guard Non-Propagation",
        "severity": "medium",
        "visible_code": """
# web_controller.py — Form submission handler
from flask import Flask, request, render_template
from sanitizer import sanitize_html
from db import save_comment

app = Flask(__name__)

@app.route("/comment", methods=["POST"])
def submit_comment():
    \"\"\"Handle comment form submission.\"\"\"
    body = request.form.get("body", "")
    # Sanitize HTML to prevent XSS
    clean_body = sanitize_html(body)
    user_id = request.session["user_id"]
    save_comment(user_id=user_id, body=clean_body)
    return render_template("comment_success.html")
""",
        "hidden_behavior": "There is also an API endpoint (api_controller.py) that accepts comments via JSON POST to /api/comments. This endpoint does NOT call sanitize_html() — it saves the raw body directly to the database. Since both endpoints store to the same `comments` table and the frontend renders comment bodies as HTML, any comment submitted via the API endpoint can contain malicious scripts that execute when viewed.",
        "questions": [
            {
                "q": "A user submits `<script>alert('xss')</script>` via POST /api/comments. What happens when another user views the comment?",
                "options": {
                    "A": "The script is escaped and displayed as text",
                    "B": "The script executes in the viewer's browser (stored XSS) because the API endpoint doesn't sanitize input",
                    "C": "The comment is rejected by the API's input validation",
                },
                "correct": "B",
            },
            {
                "q": "A security audit finds XSS in the comments feature. The form submission handler is reviewed and found to have proper sanitization. Where should the auditor look next?",
                "options": {
                    "A": "The database query for SQL injection",
                    "B": "The API endpoint that also writes comments but skips sanitization",
                    "C": "The template rendering engine for output encoding issues",
                },
                "correct": "B",
            },
            {
                "q": "What is the root cause of this vulnerability?",
                "options": {
                    "A": "The sanitizer library has a bypass vulnerability",
                    "B": "Input sanitization is applied in the form handler but not propagated to the parallel API endpoint that writes to the same table",
                    "C": "The database should be sanitizing data on write",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "④-low",
        "pattern": "④",
        "pattern_name": "Guard Non-Propagation",
        "severity": "low",
        "visible_code": """
# user_service.py — User data access
def get_user_profile(user_id):
    \"\"\"Get user profile with null safety.\"\"\"
    user = db.query("SELECT * FROM users WHERE id = %s", [user_id])
    if user is None:
        return {"error": "User not found", "status": 404}

    profile = db.query("SELECT * FROM profiles WHERE user_id = %s", [user_id])
    if profile is None:
        # Return user with empty profile defaults
        return {
            "user": user,
            "profile": {"bio": "", "avatar": "/default.png"},
        }

    return {"user": user, "profile": profile}
""",
        "hidden_behavior": "The notification_service.py also loads user profiles via a different code path: `user = db.query(...); send_email(user['email'], ...)`. This path does NOT check if user is None before accessing user['email']. When a user is deleted but still has pending notifications, this code path throws a TypeError: 'NoneType' is not subscriptable, crashing the notification worker and blocking all pending notifications for all users.",
        "questions": [
            {
                "q": "A user account is deleted. 5 minutes later, the notification worker crashes. Why?",
                "options": {
                    "A": "The notification references a deleted email address in the SMTP server",
                    "B": "notification_service loads the deleted user without a null check, causing a TypeError when accessing user['email']",
                    "C": "The database cascading delete removed the notification queue",
                },
                "correct": "B",
            },
            {
                "q": "After the crash, 500 pending notifications for OTHER users are also stuck. Why?",
                "options": {
                    "A": "The email server is down",
                    "B": "The null pointer exception crashes the entire notification worker process, blocking the queue for all users",
                    "C": "The notifications were linked to the deleted user through a foreign key",
                },
                "correct": "B",
            },
            {
                "q": "user_service.get_user_profile has proper null handling. Why doesn't this protect the notification service?",
                "options": {
                    "A": "The notification service imports a different version of the function",
                    "B": "The notification service uses a different code path to load users and does not have the same null check",
                    "C": "The null check only works for HTTP requests, not background jobs",
                },
                "correct": "B",
            },
        ],
    },

    # ===================================================================
    # ⑤ Paired-Setting Override
    # ===================================================================
    {
        "id": "⑤-high",
        "pattern": "⑤",
        "pattern_name": "Paired-Setting Override",
        "severity": "high",
        "visible_code": """
# database.py — Database connection pool
import os

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "max_connections": int(os.getenv("DB_MAX_CONNECTIONS", "100")),
    "database": os.getenv("DB_NAME", "myapp"),
}

# The ops team has set DB_MAX_CONNECTIONS=100 in production
# to handle peak traffic of ~80 concurrent users.
""",
        "hidden_behavior": "The connection pool library (pgbouncer config) has a separate `pool_size` parameter set to 10 in the pgbouncer.ini config file. `pool_size` acts as a hard upper limit that OVERRIDES `max_connections`. Even though the app thinks it can use 100 connections, pgbouncer silently queues anything above 10. Under load with 80 concurrent users, 70 connections are stuck waiting in the pgbouncer queue, causing cascading timeouts and the app appearing to 'hang' despite the database being healthy.",
        "questions": [
            {
                "q": "Under peak load (80 concurrent users), the application hangs but the PostgreSQL server shows only 10 active connections and low CPU. Why?",
                "options": {
                    "A": "The application has a deadlock in its connection handling",
                    "B": "PgBouncer's pool_size=10 silently caps connections, queuing 70 requests regardless of the app's max_connections=100",
                    "C": "PostgreSQL's max_connections setting is too low",
                },
                "correct": "B",
            },
            {
                "q": "The ops team increases DB_MAX_CONNECTIONS from 100 to 200 to fix the hanging issue. Does this help?",
                "options": {
                    "A": "Yes — more connections are available now",
                    "B": "No — pgbouncer's pool_size=10 is the actual bottleneck; changing the app config has no effect",
                    "C": "Partially — it helps but doesn't fully resolve the issue",
                },
                "correct": "B",
            },
            {
                "q": "What makes this bug particularly hard to diagnose?",
                "options": {
                    "A": "The error messages are in a different language",
                    "B": "The app config says 100 connections and no error is raised, but a separate infrastructure config silently overrides it to 10 — no warnings or logs indicate the cap",
                    "C": "The database connection protocol is encrypted so traffic can't be inspected",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "⑤-medium",
        "pattern": "⑤",
        "pattern_name": "Paired-Setting Override",
        "severity": "medium",
        "visible_code": """
# cache_config.py — Redis cache configuration
import os

CACHE_CONFIG = {
    "ttl": int(os.getenv("CACHE_TTL", "3600")),      # 1 hour
    "prefix": os.getenv("CACHE_PREFIX", "myapp"),
    "serializer": "json",
}

# TTL is set to 3600 seconds (1 hour) to balance freshness vs DB load.
# Most data updates every few hours, so 1 hour cache is reasonable.

def cache_set(key, value):
    redis.setex(f"{CACHE_CONFIG['prefix']}:{key}", CACHE_CONFIG['ttl'], serialize(value))

def cache_get(key):
    data = redis.get(f"{CACHE_CONFIG['prefix']}:{key}")
    return deserialize(data) if data else None
""",
        "hidden_behavior": "Redis is configured with `maxmemory-policy allkeys-lru` and `maxmemory 256mb`. When the cache grows beyond 256MB, Redis evicts the least recently used keys regardless of their TTL. With the current data growth, the cache fills up in ~15 minutes. So even though TTL is set to 3600s (1 hour), most entries are evicted within 15 minutes by the LRU policy, making the TTL setting effectively meaningless. The cache hit rate is ~20% instead of the expected ~90%.",
        "questions": [
            {
                "q": "Cache TTL is 3600 seconds but monitoring shows most entries disappear after ~15 minutes. No code deletes them. Why?",
                "options": {
                    "A": "Redis has a bug in its TTL implementation",
                    "B": "Redis's maxmemory LRU eviction is removing entries before TTL expires because the 256MB limit is reached in ~15 minutes",
                    "C": "The serializer is corrupting keys, making them unfindable",
                },
                "correct": "B",
            },
            {
                "q": "The team increases CACHE_TTL to 7200 to improve cache hit rate. The hit rate stays at ~20%. Why?",
                "options": {
                    "A": "The application is reading different keys each time",
                    "B": "TTL doesn't matter when entries are evicted by memory pressure — the maxmemory limit is the actual constraint",
                    "C": "The Redis connection is dropping intermittently",
                },
                "correct": "B",
            },
            {
                "q": "What is the correct fix to achieve the intended 1-hour cache behavior?",
                "options": {
                    "A": "Set TTL to a very large value so entries are never expired",
                    "B": "Increase Redis maxmemory to accommodate 1 hour of data, or reduce cache entry size",
                    "C": "Switch to a different caching library",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "⑤-low",
        "pattern": "⑤",
        "pattern_name": "Paired-Setting Override",
        "severity": "low",
        "visible_code": """
# logging_config.py — Application logging
import os
import logging

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")

def setup_logging():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    logger = logging.getLogger("myapp")
    logger.info(f"Logging initialized at {LOG_LEVEL} level")
    return logger

# Developer sets LOG_LEVEL=DEBUG in .env for local development
# to see all debug messages for troubleshooting.
""",
        "hidden_behavior": "The application framework (e.g., gunicorn/uvicorn config) has its own `--log-level warning` flag in the Procfile/docker-compose, which overrides the root logger's level AFTER setup_logging() runs. So even though LOG_LEVEL=DEBUG is set and the 'Logging initialized at DEBUG level' message appears at startup, the actual effective level becomes WARNING. Debug and info messages are silently dropped after the framework's logger reconfiguration.",
        "questions": [
            {
                "q": "A developer sets LOG_LEVEL=DEBUG and sees 'Logging initialized at DEBUG level' at startup. But debug messages from request handlers never appear. Why?",
                "options": {
                    "A": "The request handlers use a different logger instance",
                    "B": "The application framework overrides the log level to WARNING after setup_logging() runs, silently dropping DEBUG/INFO messages",
                    "C": "Debug messages are buffered and only flushed on error",
                },
                "correct": "B",
            },
            {
                "q": "The startup message appears at INFO level, confirming the logger works. But subsequent INFO messages from business logic are missing. What's happening?",
                "options": {
                    "A": "The business logic is wrapped in try/except that swallows log calls",
                    "B": "The startup message is logged BEFORE the framework reconfigures the log level; subsequent messages are after the override to WARNING",
                    "C": "INFO messages are being sent to a different log file",
                },
                "correct": "B",
            },
            {
                "q": "Changing LOG_LEVEL to any value (DEBUG, INFO, WARNING) doesn't change the visible log output. Only WARNING and above always appear. What should the developer investigate?",
                "options": {
                    "A": "The logging library version for compatibility issues",
                    "B": "The application framework's own log-level configuration, which overrides the app's logging setup",
                    "C": "Whether the LOG_LEVEL environment variable is being read correctly",
                },
                "correct": "B",
            },
        ],
    },

    # ===================================================================
    # ⑥ Lifecycle Ordering
    # ===================================================================
    {
        "id": "⑥-high",
        "pattern": "⑥",
        "pattern_name": "Lifecycle Ordering",
        "severity": "high",
        "visible_code": """
# app.py — Application startup
from db import DatabasePool
from cache import CacheWarmer
from api import start_api_server

def main():
    print("Starting application...")

    # Initialize components
    db = DatabasePool(host="db.internal", pool_size=20)
    cache = CacheWarmer(db)
    api = start_api_server(port=8080, db=db, cache=cache)

    print("Application started")
    api.serve_forever()

if __name__ == "__main__":
    main()
""",
        "hidden_behavior": "DatabasePool.__init__() starts an async connection establishment in the background and returns immediately. The pool is NOT ready when the constructor returns — connections are still being established. CacheWarmer.__init__() immediately calls db.query() to pre-load hot data, but the pool has 0 ready connections at this point. The query hangs for 5-10 seconds waiting for the first connection, then loads stale data from a connection that was mid-setup. Meanwhile, start_api_server() begins accepting requests before the cache is warmed, serving cold-cache responses.",
        "questions": [
            {
                "q": "The first 10 seconds after startup, API responses take 5-10x longer than normal. After that, performance normalizes. Why?",
                "options": {
                    "A": "JIT compilation is warming up the application code",
                    "B": "DatabasePool connections are still being established in the background — the cache warmer and early API requests compete for half-ready connections",
                    "C": "The API server takes time to bind to the port",
                },
                "correct": "B",
            },
            {
                "q": "The cache warmer logs show it loaded 500 items in 8 seconds at startup. Normally this takes 200ms. What is happening?",
                "options": {
                    "A": "The database is under heavy load from other services",
                    "B": "The cache warmer runs before the connection pool is ready, so every query waits for connections to be established",
                    "C": "The cache entries are larger than expected",
                },
                "correct": "B",
            },
            {
                "q": "A health check at startup returns 200 OK but the app can't serve real requests for another 10 seconds. Why is the health check misleading?",
                "options": {
                    "A": "The health check endpoint doesn't require authentication",
                    "B": "start_api_server() makes the port available before dependencies (DB pool, cache) are actually ready",
                    "C": "The health check uses a different network interface",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "⑥-medium",
        "pattern": "⑥",
        "pattern_name": "Lifecycle Ordering",
        "severity": "medium",
        "visible_code": """
# worker.py — Background job processor
import signal
import sys

class JobWorker:
    def __init__(self):
        self.running = True
        self.current_job = None
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        print("Shutdown signal received, stopping gracefully...")
        self.running = False

    def run(self):
        while self.running:
            self.current_job = self.fetch_next_job()
            if self.current_job:
                self.process_job(self.current_job)
                self.mark_complete(self.current_job)
        print("Worker stopped")
        sys.exit(0)
""",
        "hidden_behavior": "When SIGTERM arrives during process_job(), the signal handler sets self.running = False. But process_job() may take 30+ seconds for large jobs. The sys.exit(0) in run() is called AFTER the current job completes. However, Kubernetes has a 30-second terminationGracePeriodSeconds. If the job takes more than 30 seconds, Kubernetes sends SIGKILL which terminates the process immediately. The job is neither completed nor marked as failed — it stays in 'processing' state forever, becoming a zombie job that blocks the queue slot.",
        "questions": [
            {
                "q": "A long-running job (45 seconds) is in progress when a Kubernetes rolling update starts. What happens to the job?",
                "options": {
                    "A": "The job completes normally and the pod terminates after",
                    "B": "The job is interrupted cleanly and retried on the new pod",
                    "C": "SIGTERM sets running=False, but Kubernetes sends SIGKILL after 30s, leaving the job stuck in 'processing' state permanently",
                },
                "correct": "C",
            },
            {
                "q": "After several deployments, the team notices 5 jobs stuck in 'processing' state with no worker assigned. What happened?",
                "options": {
                    "A": "Database lock contention caused the workers to deadlock",
                    "B": "These jobs were killed by SIGKILL during rolling updates before mark_complete() could run, and no cleanup recovers them",
                    "C": "The job queue has a memory leak",
                },
                "correct": "B",
            },
            {
                "q": "The graceful shutdown handler looks correct (sets running=False, finishes current job). What is it missing?",
                "options": {
                    "A": "It should catch SIGINT as well",
                    "B": "It doesn't account for the Kubernetes termination grace period — if the current job exceeds 30 seconds, SIGKILL forcefully terminates before cleanup can happen",
                    "C": "It should use threading instead of signal handling",
                },
                "correct": "B",
            },
        ],
    },
    {
        "id": "⑥-low",
        "pattern": "⑥",
        "pattern_name": "Lifecycle Ordering",
        "severity": "low",
        "visible_code": """
# event_bus.py — Pub/sub event system
class EventBus:
    def __init__(self):
        self._handlers = {}

    def on(self, event_name, handler):
        self._handlers.setdefault(event_name, []).append(handler)

    def emit(self, event_name, data):
        for handler in self._handlers.get(event_name, []):
            handler(data)

# app_setup.py
bus = EventBus()

def init_app():
    # Register handlers
    bus.on("user_created", send_welcome_email)
    bus.on("user_created", create_default_settings)
    bus.on("user_created", log_analytics_event)
""",
        "hidden_behavior": "The handlers are called in registration order: send_welcome_email → create_default_settings → log_analytics_event. send_welcome_email() reads the user's settings to decide the email language. But create_default_settings() hasn't run yet at that point, so the settings don't exist. send_welcome_email() falls back to English regardless of the user's locale. This only affects the welcome email — all subsequent emails use the correct language because settings exist by then.",
        "questions": [
            {
                "q": "Japanese users report their welcome email is in English, but all other emails are in Japanese. Why?",
                "options": {
                    "A": "The welcome email template doesn't support Japanese",
                    "B": "send_welcome_email runs before create_default_settings in the handler chain, so user settings (including locale) don't exist yet when the welcome email is sent",
                    "C": "The email service has a bug with locale detection on first send",
                },
                "correct": "B",
            },
            {
                "q": "A developer adds a test: create user → check welcome email language. The test passes (email is in English, user is English-speaking). Why doesn't the test catch this bug?",
                "options": {
                    "A": "The test environment uses a different email service",
                    "B": "The test creates an English-speaking user, so the English fallback happens to produce the correct result — the bug only manifests for non-English users",
                    "C": "The test mocks the email function",
                },
                "correct": "B",
            },
            {
                "q": "Swapping the handler registration order (create_default_settings before send_welcome_email) fixes the bug. What does this reveal about the system?",
                "options": {
                    "A": "The event bus has a race condition",
                    "B": "The handlers have an implicit ordering dependency that is not documented or enforced by the event bus API",
                    "C": "The event bus should use async handlers",
                },
                "correct": "B",
            },
        ],
    },
]
