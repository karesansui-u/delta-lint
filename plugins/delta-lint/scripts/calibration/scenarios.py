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
    # (Redesigned: use internal/niche specs LLM can't know from training)
    # ===================================================================
    {
        "id": "③-high",
        "pattern": "③",
        "pattern_name": "External Spec Divergence",
        "severity": "high",
        "visible_code": """
# sensor_api.py — IoT sensor data ingestion
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/api/v2/readings", methods=["POST"])
def submit_reading():
    data = request.get_json()
    sensor_id = data["sensor_id"]
    value = float(data["value"])
    unit = data.get("unit", "celsius")
    ts = data.get("timestamp")
    store_reading(sensor_id, value, unit, ts)
    return jsonify({"status": "ok"})

@app.route("/api/v2/readings/<sensor_id>", methods=["GET"])
def get_readings(sensor_id):
    readings = fetch_readings(sensor_id)
    return jsonify({"readings": readings})
""",
        "hidden_behavior": "The internal sensor protocol spec (v2.3) requires all temperature values to be submitted in millikelvin (mK), not celsius. store_reading() passes the value directly to the time-series database which indexes in millikelvin. A reading of value=23.5 (intended as 23.5°C = 296650 mK) is stored as 23.5 mK (≈ -273.13°C). Downstream alerting triggers on any value below 200000 mK as 'sensor failure', so every celsius reading is flagged as a broken sensor.",
        "questions": [
            {
                "q": "A sensor sends value=23.5 with unit='celsius'. The dashboard shows -273.13°C. Why?",
                "options": {
                    "A": "The dashboard has a rendering bug with decimal values",
                    "B": "The database stores in millikelvin per the v2.3 spec, so 23.5 is interpreted as 23.5 mK, not 23.5°C",
                    "C": "The sensor firmware is sending negative values",
                },
                "correct": "B",
            },
            {
                "q": "All 500 sensors are flagged as 'failed' in the alerting system, but they're physically working fine. What is happening?",
                "options": {
                    "A": "The alerting threshold is misconfigured",
                    "B": "Values arrive in celsius but the alert checks millikelvin — 23.5 mK is far below the 200000 mK failure threshold",
                    "C": "Network latency is causing stale readings",
                },
                "correct": "B",
            },
            {
                "q": "The v1 API worked correctly with celsius values. What changed in v2?",
                "options": {
                    "A": "v2 added authentication that breaks old sensors",
                    "B": "v2 requires values in millikelvin per the internal protocol spec, but the API still accepts the raw number without conversion",
                    "C": "v2 changed the JSON schema for readings",
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
# invoice_export.py — Export invoices to partner format
import xml.etree.ElementTree as ET

def export_invoice(invoice):
    root = ET.Element("Invoice")
    ET.SubElement(root, "Number").text = invoice["number"]
    ET.SubElement(root, "Date").text = invoice["date"]
    ET.SubElement(root, "Amount").text = str(invoice["amount"])
    ET.SubElement(root, "Currency").text = invoice["currency"]
    ET.SubElement(root, "Tax").text = str(invoice["tax"])
    return ET.tostring(root, encoding="unicode")
""",
        "hidden_behavior": "The partner's invoice schema (PartnerSpec v4.1) requires Amount to be net (before tax), with a separate GrossAmount field for the total. This code sets Amount=invoice['amount'] where invoice['amount'] is the gross total (including tax). The partner system treats Amount as net and adds Tax on top, resulting in double-taxation. A $100 invoice with $10 tax becomes $110 net + $10 tax = $120 in the partner's system.",
        "questions": [
            {
                "q": "An invoice for $100 (with $10 tax) is exported. The partner shows the total as $120. Why?",
                "options": {
                    "A": "Currency conversion is adding fees",
                    "B": "The code sends gross as Amount, but the partner expects net — so tax is applied twice ($100 + $10 + $10)",
                    "C": "The XML encoding is corrupting numeric values",
                },
                "correct": "B",
            },
            {
                "q": "Invoices with tax=0 export correctly but all taxed invoices are wrong. What does this tell you?",
                "options": {
                    "A": "The Tax field has a data type mismatch",
                    "B": "When tax=0, gross equals net so the bug is invisible — the Amount/net confusion only matters when tax > 0",
                    "C": "Zero-tax invoices use a different export path",
                },
                "correct": "B",
            },
            {
                "q": "The fix is to send Amount = invoice['amount'] - invoice['tax']. Why wasn't this obvious?",
                "options": {
                    "A": "The partner spec uses the same field names as the internal system but with different semantics",
                    "B": "The XML schema validation doesn't check numeric ranges",
                    "C": "The partner never documented the Amount field",
                },
                "correct": "A",
            },
        ],
    },
    {
        "id": "③-low",
        "pattern": "③",
        "pattern_name": "External Spec Divergence",
        "severity": "low",
        "visible_code": """
# report_sender.py — Send reports to compliance system
import requests

def send_report(report_data):
    \"\"\"Send report to compliance API.\"\"\"
    # NOTE: compliance API docs say field order matters
    # but our dict ordering should be fine (Python 3.7+ preserves insertion order)
    payload = {
        "report_id": report_data["id"],
        "entity": report_data["entity"],
        "period": report_data["period"],
        "figures": report_data["figures"],
    }
    resp = requests.post("https://compliance.internal/api/reports", json=payload)
    return resp.status_code == 200
""",
        "hidden_behavior": "The compliance API requires the 'figures' field to contain string-encoded decimals with exactly 2 decimal places (e.g. '1234.50'), not numeric types. Python's json.dumps converts report_data['figures'] (a dict of floats) to JSON numbers like 1234.5 (no trailing zero). The compliance system silently accepts the payload but marks the report as 'pending manual review' instead of 'auto-approved', because the figures fail strict decimal format validation.",
        "questions": [
            {
                "q": "Reports are submitted successfully (200 OK) but always land in 'pending manual review' instead of 'auto-approved'. Why?",
                "options": {
                    "A": "The entity field contains invalid characters",
                    "B": "The figures are sent as JSON numbers (1234.5) but the API requires string decimals with 2 places ('1234.50') for auto-approval",
                    "C": "The report_id format is wrong",
                },
                "correct": "B",
            },
            {
                "q": "A report with figures all being whole numbers (100.00, 200.00) is auto-approved, but one with 99.50 is not. Why?",
                "options": {
                    "A": "Amounts under 100 require extra approval",
                    "B": "JSON serializes 100.00 as 100.0 and 99.50 as 99.5 — both lack the required 2-decimal format, but whole numbers may pass a looser check",
                    "C": "The period field is invalid for that report",
                },
                "correct": "B",
            },
            {
                "q": "The comment in the code hints at a potential issue. What does it miss?",
                "options": {
                    "A": "Dict ordering is actually not preserved",
                    "B": "The real issue isn't field order but data types — the comment worries about ordering while figures need to be formatted strings, not floats",
                    "C": "The API URL is wrong",
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
    # ===================================================================
    # ⑤ Paired-Setting Override
    # (Redesigned: shorter options, varied correct answers, simpler hidden behavior)
    # ===================================================================
    {
        "id": "⑤-high",
        "pattern": "⑤",
        "pattern_name": "Paired-Setting Override",
        "severity": "high",
        "visible_code": """
# upload.py — File upload handler
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB

def handle_upload(file_obj):
    data = file_obj.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise ValueError("File too large")
    save_to_storage(data)
    return {"status": "ok", "size": len(data)}
""",
        "hidden_behavior": "The nginx reverse proxy in front of this app has `client_max_body_size 1m` (1 MB default). Nginx rejects any request body larger than 1 MB with a 413 error BEFORE it reaches the Python app. The app's 50 MB limit is never checked for files between 1-50 MB because nginx kills the request first. Users see '413 Request Entity Too Large' from nginx, not the app's error message.",
        "questions": [
            {
                "q": "A user uploads a 5 MB file. The app's 50 MB limit should allow it. What happens?",
                "options": {
                    "A": "Upload succeeds normally",
                    "B": "App rejects it as too large",
                    "C": "Nginx returns 413 before the app sees the request",
                },
                "correct": "C",
            },
            {
                "q": "Files under 1 MB upload fine. Files over 1 MB always fail with 413. Where is the bottleneck?",
                "options": {
                    "A": "The app's MAX_UPLOAD_SIZE is wrong",
                    "B": "The storage backend rejects large files",
                    "C": "Nginx's client_max_body_size is 1 MB, overriding the app's 50 MB limit",
                },
                "correct": "C",
            },
            {
                "q": "The developer raises MAX_UPLOAD_SIZE to 100 MB. Upload behavior does not change. Why?",
                "options": {
                    "A": "A separate nginx config caps uploads at 1 MB",
                    "B": "The file_obj.read() call has a buffer limit",
                    "C": "The storage backend has its own size limit",
                },
                "correct": "A",
            },
        ],
    },
    {
        "id": "⑤-medium",
        "pattern": "⑤",
        "pattern_name": "Paired-Setting Override",
        "severity": "medium",
        "visible_code": """
# worker.py — Background task processor
import os

WORKER_TIMEOUT = int(os.getenv("WORKER_TIMEOUT", "300"))  # 5 minutes

def process_task(task):
    # Long-running task, may take up to 5 minutes
    result = run_heavy_computation(task["data"])
    store_result(task["id"], result)
    return result
""",
        "hidden_behavior": "The task queue (Celery) is configured with `task_time_limit=60` in celeryconfig.py. Celery sends SIGKILL to any task running longer than 60 seconds. The app's WORKER_TIMEOUT=300 is never reached because Celery kills the worker process at 60 seconds. Tasks that take 1-5 minutes are silently killed and retried, creating duplicate partial results.",
        "questions": [
            {
                "q": "A task that takes 90 seconds is killed after 60 seconds. WORKER_TIMEOUT is 300. Why?",
                "options": {
                    "A": "The OS is killing the process for memory usage",
                    "B": "Celery's task_time_limit=60 kills the task before the app timeout",
                    "C": "The database connection times out",
                },
                "correct": "B",
            },
            {
                "q": "Increasing WORKER_TIMEOUT from 300 to 600 has no effect on task completion. Why?",
                "options": {
                    "A": "The env var is not being read correctly",
                    "B": "The actual kill comes from Celery at 60s, not from WORKER_TIMEOUT",
                    "C": "The task code has an infinite loop",
                },
                "correct": "B",
            },
            {
                "q": "Short tasks (<60s) always succeed. Long tasks (>60s) always fail. What causes this pattern?",
                "options": {
                    "A": "Long tasks hit a memory limit",
                    "B": "Long tasks exceed the network timeout",
                    "C": "A 60-second hard limit exists outside the app code",
                },
                "correct": "C",
            },
        ],
    },
    {
        "id": "⑤-low",
        "pattern": "⑤",
        "pattern_name": "Paired-Setting Override",
        "severity": "low",
        "visible_code": """
# rate_limiter.py — API rate limiting
RATE_LIMIT = 1000  # requests per minute

# TODO: check if the load balancer has its own rate limit
# (saw 429 errors in prod that don't match our limit)

def check_rate(client_ip):
    count = get_request_count(client_ip, window=60)
    if count > RATE_LIMIT:
        return False, "Rate limit exceeded"
    return True, None
""",
        "hidden_behavior": "The AWS ALB (Application Load Balancer) has a connection limit of 100 concurrent connections per target. When a single client makes many parallel requests, the ALB returns 503 after 100 concurrent connections, well before the app's 1000/min rate limit is reached. The TODO comment in the code hints at this issue but was never investigated.",
        "questions": [
            {
                "q": "A client making 150 parallel requests gets 503 errors, not 429. The app's rate limit is 1000/min. Why?",
                "options": {
                    "A": "The app has a bug in counting requests",
                    "B": "The ALB's 100-connection limit triggers before the app's rate limit",
                    "C": "The client is sending malformed requests",
                },
                "correct": "B",
            },
            {
                "q": "The TODO comment mentions unexplained 429 errors. The actual error is 503. What does this suggest?",
                "options": {
                    "A": "The load balancer is returning 503 instead of 429 for its own limit",
                    "B": "The app's rate limiter is broken",
                    "C": "The 429 and 503 errors are unrelated",
                },
                "correct": "A",
            },
            {
                "q": "Raising RATE_LIMIT to 5000 does not stop the 503 errors. What should be checked?",
                "options": {
                    "A": "The database connection pool",
                    "B": "Infrastructure-level limits outside the app code",
                    "C": "The HTTP client timeout settings",
                },
                "correct": "B",
            },
        ],
    },

    # ===================================================================
    # ⑥ Lifecycle Ordering
    # (Redesigned: clearer scenarios, shorter options, better calibration)
    # ===================================================================
    {
        "id": "⑥-high",
        "pattern": "⑥",
        "pattern_name": "Lifecycle Ordering",
        "severity": "high",
        "visible_code": """
# migrate.py — Database migration runner
def run_migrations():
    create_users_table()
    create_orders_table()
    seed_admin_user()
    add_foreign_keys()

def create_users_table():
    db.execute("CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT)")

def create_orders_table():
    db.execute("CREATE TABLE orders (id SERIAL, user_id INT, total DECIMAL)")

def seed_admin_user():
    db.execute("INSERT INTO users (name) VALUES ('admin')")

def add_foreign_keys():
    db.execute("ALTER TABLE orders ADD FOREIGN KEY (user_id) REFERENCES users(id)")
""",
        "hidden_behavior": "seed_admin_user() runs BEFORE add_foreign_keys(). The orders table has no foreign key constraint yet when the seeder runs. Another migration script (run in parallel by a different pod) inserts test orders with user_id=999 during this window. When add_foreign_keys() runs, it fails with 'insert or update on table orders violates foreign key constraint' because user_id=999 doesn't exist in users. The migration crashes halfway, leaving the database in an inconsistent state with some tables created but no foreign keys.",
        "questions": [
            {
                "q": "add_foreign_keys() fails with a constraint violation. All code looks correct. What happened?",
                "options": {
                    "A": "The users table has duplicate IDs",
                    "B": "Invalid orders were inserted between create_orders_table() and add_foreign_keys()",
                    "C": "The SQL syntax for FOREIGN KEY is wrong",
                },
                "correct": "B",
            },
            {
                "q": "The migration works in dev (single process) but fails in production. Why?",
                "options": {
                    "A": "Production has a different PostgreSQL version",
                    "B": "In production, parallel processes insert data during the window before foreign keys are added",
                    "C": "The production database has different permissions",
                },
                "correct": "B",
            },
            {
                "q": "Moving add_foreign_keys() to run right after create_orders_table() fixes the issue. Why?",
                "options": {
                    "A": "It reduces the total migration time",
                    "B": "It closes the window where invalid data can be inserted into orders",
                    "C": "Foreign keys must be added before any INSERT",
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
# pipeline.py — Data processing pipeline
class Pipeline:
    def run(self, data):
        validated = self.validate(data)
        enriched = self.enrich(validated)
        result = self.transform(enriched)
        self.publish(result)
        return result

    def validate(self, data):
        return [r for r in data if r.get("id") and r.get("value")]

    def enrich(self, data):
        for record in data:
            record["source"] = lookup_source(record["id"])
        return data

    def transform(self, data):
        return [{"id": r["id"], "amount": r["value"] * r.get("rate", 1.0)} for r in data]
""",
        "hidden_behavior": "lookup_source() in enrich() makes an HTTP call to an external service. This service has a rate limit of 10 requests/second. When data has 500+ records, enrich() fires 500 HTTP calls in rapid succession, gets rate-limited (429 errors) after the first 10, and returns None for the remaining 490 records' source field. transform() then runs on records where source is None, which doesn't cause an error (source isn't used in transform), but publish() sends 490 records with source=None to downstream consumers who reject them.",
        "questions": [
            {
                "q": "Processing 5 records works perfectly. Processing 500 records results in 490 records rejected by downstream. Why?",
                "options": {
                    "A": "transform() has a bug with large datasets",
                    "B": "enrich() gets rate-limited after 10 lookups, leaving 490 records with source=None",
                    "C": "The validate step filters out most records",
                },
                "correct": "B",
            },
            {
                "q": "The pipeline logs show no errors during the run, but downstream rejects 98% of records. What is silently failing?",
                "options": {
                    "A": "The publish step is corrupting data",
                    "B": "Rate-limited HTTP calls in enrich() return None without raising exceptions",
                    "C": "The transform step drops decimal precision",
                },
                "correct": "B",
            },
            {
                "q": "Adding a 100ms delay between lookups in enrich() fixes the issue. Why?",
                "options": {
                    "A": "The delay lets the database catch up",
                    "B": "It keeps requests under the external service's rate limit",
                    "C": "The delay reduces memory usage",
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
# notifications.py — User notification system
def on_new_order(order):
    # FIXME: sometimes email has wrong product name
    # (might be a timing issue with inventory update?)
    send_email(order["user"], f"Order confirmed: {order['product_name']}")
    update_inventory(order["product_id"], -1)
    log_order(order)

def update_inventory(product_id, delta):
    product = db.get(product_id)
    product["stock"] += delta
    if product["stock"] == 0:
        product["name"] = product["name"] + " [OUT OF STOCK]"
    db.save(product)
""",
        "hidden_behavior": "When stock reaches 0, update_inventory() appends ' [OUT OF STOCK]' to the product name in the database. If two orders for the last item arrive near-simultaneously: Order A calls send_email() (correct name), then update_inventory() (stock→0, name changed). Order B calls send_email() AFTER the name was modified by Order A's inventory update, so Order B's email says 'Widget [OUT OF STOCK]' instead of 'Widget'. The FIXME comment in the code hints at this exact issue.",
        "questions": [
            {
                "q": "A customer receives 'Order confirmed: Widget [OUT OF STOCK]' in their email. The order was valid. What happened?",
                "options": {
                    "A": "The email template has a formatting bug",
                    "B": "Another order's update_inventory() modified the product name before this order's send_email() ran",
                    "C": "The product was marked out of stock before the order was placed",
                },
                "correct": "B",
            },
            {
                "q": "The bug only appears during flash sales with high concurrency. In normal traffic it never occurs. Why?",
                "options": {
                    "A": "Flash sales use a different email service",
                    "B": "High concurrency creates overlapping order processing where one order's inventory update changes the name before another's email",
                    "C": "The database is slower during sales",
                },
                "correct": "B",
            },
            {
                "q": "Moving send_email() to after update_inventory() would make the bug worse. Why?",
                "options": {
                    "A": "Emails would be delayed too long",
                    "B": "The email would always read the post-update name, so every last-item order gets '[OUT OF STOCK]' in the email",
                    "C": "update_inventory might fail, and no email would be sent",
                },
                "correct": "B",
            },
        ],
    },
]
