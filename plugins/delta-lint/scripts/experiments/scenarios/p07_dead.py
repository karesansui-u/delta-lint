"""⑦ Dead Code — 3重大度シナリオ.

high:   旧 API handler が routes に残っているが、新 handler に置き換え済み → 旧が shadow
medium: フィーチャーフラグが OFF だが、dead code 内で副作用（メトリクス送信）が発生
low:    未使用の helper 関数が古い依存ライブラリを import → 脆弱性スキャンで検出
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ⑦ × high — 旧 handler が残って shadow routing
# =====================================================================

_H_ROUTES_A = """\
# routes.py
from api import users_v1, users_v2, products, health

def register_routes(app):
    # Health
    app.route("/health", health.check)

    # Users API v1 (legacy)
    app.route("/api/users", users_v1.list_users)
    app.route("/api/users/<id>", users_v1.get_user)
    app.route("/api/users", users_v1.create_user, methods=["POST"])
    app.route("/api/users/<id>", users_v1.delete_user, methods=["DELETE"])

    # Users API v2 (current)
    app.route("/api/v2/users", users_v2.list_users)
    app.route("/api/v2/users/<id>", users_v2.get_user)
    app.route("/api/v2/users", users_v2.create_user, methods=["POST"])
    app.route("/api/v2/users/<id>", users_v2.delete_user, methods=["DELETE"])

    # Products
    app.route("/api/products", products.list_products)
    app.route("/api/products/<id>", products.get_product)
"""

_H_ROUTES_B = """\
# routes.py
from api import users_v1, users_v2, products, health

# ⚠ DEAD CODE / SHADOW ROUTING: The v1 user endpoints are still registered
# at /api/users despite v2 being the "current" API. users_v1 module has NO
# authentication middleware (it predates the auth system), while users_v2
# requires bearer tokens. External clients that discover /api/users (e.g.,
# via directory scanning) can access user data without authentication.
# The v1 handlers also lack rate limiting and input validation.

def register_routes(app):
    # Health
    app.route("/health", health.check)

    # Users API v1 (legacy)
    app.route("/api/users", users_v1.list_users)
    app.route("/api/users/<id>", users_v1.get_user)
    app.route("/api/users", users_v1.create_user, methods=["POST"])
    app.route("/api/users/<id>", users_v1.delete_user, methods=["DELETE"])

    # Users API v2 (current)
    app.route("/api/v2/users", users_v2.list_users)
    app.route("/api/v2/users/<id>", users_v2.get_user)
    app.route("/api/v2/users", users_v2.create_user, methods=["POST"])
    app.route("/api/v2/users/<id>", users_v2.delete_user, methods=["DELETE"])

    # Products
    app.route("/api/products", products.list_products)
    app.route("/api/products/<id>", products.get_product)
"""

_H_V2 = """\
# api/users_v2.py
from middleware.auth import require_auth, require_admin
from services.user_service import UserService

user_service = UserService()

@require_auth
def list_users(request):
    return {"users": user_service.list_active()}, 200

@require_auth
def get_user(request, user_id: str):
    user = user_service.get_by_id(user_id)
    if not user:
        return {"error": "Not found"}, 404
    return user.to_dict(), 200

@require_auth
def create_user(request):
    data = request.json
    user = user_service.create(**data)
    return user.to_dict(), 201

@require_admin
def delete_user(request, user_id: str):
    user_service.delete(user_id)
    return "", 204
"""

_H_AUTH = """\
# middleware/auth.py
import jwt
from functools import wraps

def require_auth(f):
    @wraps(f)
    def wrapper(request, *args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return {"error": "Unauthorized"}, 401
        try:
            payload = jwt.decode(token, "secret", algorithms=["HS256"])
            request.user = payload
        except jwt.InvalidTokenError:
            return {"error": "Invalid token"}, 401
        return f(request, *args, **kwargs)
    return wrapper

def require_admin(f):
    @wraps(f)
    @require_auth
    def wrapper(request, *args, **kwargs):
        if request.user.get("role") != "admin":
            return {"error": "Forbidden"}, 403
        return f(request, *args, **kwargs)
    return wrapper
"""

_H_NGINX = """\
# deploy/nginx.conf
server {
    location /api/ {
        proxy_pass http://app:8080;
    }
    location /health {
        proxy_pass http://app:8080;
    }
}
"""

P07_HIGH = Scenario(
    pattern="⑦",
    pattern_name="Dead Code",
    severity="high",
    description="Legacy v1 API routes still registered without auth; v2 has auth but v1 is a security bypass",
    visible_files={
        "routes.py": _H_ROUTES_A,
        "api/users_v2.py": _H_V2,
        "middleware/auth.py": _H_AUTH,
        "deploy/nginx.conf": _H_NGINX,
    },
    annotated_files={
        "routes.py": _H_ROUTES_B,
    },
    hidden_file_name="api/users_v1.py",
    hidden_file_description="No auth middleware; direct DB access without validation or rate limiting",
    questions=[
        Question(
            text=(
                "An attacker sends GET /api/users (without an Authorization header). "
                "The v2 endpoint at /api/v2/users requires authentication. "
                "Does the attacker get user data?"
            ),
            choices={
                "A": "No — all /api/ routes require authentication via middleware",
                "B": "Yes — the v1 endpoint at /api/users is still registered and has no "
                     "authentication middleware; it returns user data without any token",
                "C": "No — nginx rejects requests without Authorization headers",
                "D": "No — the v1 routes are registered but marked as deprecated and disabled",
            },
            correct="B",
        ),
        Question(
            text=(
                "The team runs a security audit and confirms all /api/v2/* endpoints "
                "properly require authentication. They conclude the API is secure. "
                "Is their conclusion correct?"
            ),
            choices={
                "A": "Yes — v2 is the only active API version",
                "B": "No — the v1 routes at /api/users are still active and accessible "
                     "without authentication, creating an unaudited access path",
                "C": "Yes — v1 routes are only used for internal service-to-service calls",
                "D": "Mostly — the only risk is that v1 returns outdated data formats",
            },
            correct="B",
        ),
        Question(
            text=(
                "An attacker calls DELETE /api/users/abc-123 (v1 endpoint) "
                "without authentication. The v2 delete endpoint requires admin role. "
                "What happens?"
            ),
            choices={
                "A": "403 Forbidden — delete always requires admin, regardless of API version",
                "B": "The user is deleted — the v1 delete handler has no authentication or "
                     "authorization checks and directly calls the user service",
                "C": "401 Unauthorized — all write operations require auth globally",
                "D": "404 — the v1 delete route is not actually registered",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑦ × medium — Feature flag OFF but dead code has side effects
# =====================================================================

_M_FLAGS_A = """\
# config/feature_flags.py
\"\"\"Feature flag configuration.\"\"\"

FEATURE_FLAGS = {
    "new_recommendation_engine": False,  # Disabled — old engine still in use
    "beta_dashboard": True,
    "dark_mode": True,
    "experimental_search": False,
}

def is_enabled(flag_name: str) -> bool:
    return FEATURE_FLAGS.get(flag_name, False)
"""

_M_FLAGS_B = """\
# config/feature_flags.py
\"\"\"Feature flag configuration.\"\"\"

FEATURE_FLAGS = {
    "new_recommendation_engine": False,  # Disabled — old engine still in use
    "beta_dashboard": True,
    "dark_mode": True,
    "experimental_search": False,
}

# ⚠ DEAD CODE WITH SIDE EFFECTS: new_recommendation_engine is disabled,
# but services/recommendation_service.py ALWAYS runs its data pipeline
# on import (module-level code), regardless of the feature flag.
# The flag only controls whether the API returns new-engine results.
# The pipeline sends user browsing data to a third-party ML API ($0.01/request)
# and has been silently running since the flag was set to False 3 months ago.
# Estimated cost: ~$500/month in wasted API calls.

def is_enabled(flag_name: str) -> bool:
    return FEATURE_FLAGS.get(flag_name, False)
"""

_M_REC_API = """\
# api/recommendations.py
from config.feature_flags import is_enabled
from services.recommendation_service import RecommendationService

rec_service = RecommendationService()

def get_recommendations(request, user_id: str):
    \"\"\"GET /recommendations/:user_id\"\"\"
    if is_enabled("new_recommendation_engine"):
        # Use new ML-powered engine
        recs = rec_service.get_ml_recommendations(user_id)
    else:
        # Use old collaborative filtering
        recs = rec_service.get_cf_recommendations(user_id)
    return {"recommendations": recs}, 200
"""

_M_BILLING = """\
# monitoring/cost_dashboard.py
\"\"\"Track external API costs.\"\"\"

EXPECTED_COSTS = {
    "ml_recommendation_api": 0,    # Disabled via feature flag
    "payment_processor": 500,       # Expected monthly cost
    "email_service": 100,           # Expected monthly cost
}
"""

_M_INIT = """\
# app.py
from api import recommendations, products, users

def create_app():
    app = App()
    app.register(recommendations)
    app.register(products)
    app.register(users)
    return app
"""

P07_MEDIUM = Scenario(
    pattern="⑦",
    pattern_name="Dead Code",
    severity="medium",
    description="Feature flag disables new engine output but module-level code still runs ML pipeline ($500/mo waste)",
    visible_files={
        "config/feature_flags.py": _M_FLAGS_A,
        "api/recommendations.py": _M_REC_API,
        "monitoring/cost_dashboard.py": _M_BILLING,
        "app.py": _M_INIT,
    },
    annotated_files={
        "config/feature_flags.py": _M_FLAGS_B,
    },
    hidden_file_name="services/recommendation_service.py",
    hidden_file_description="Module-level code runs ML data pipeline on import regardless of feature flag",
    questions=[
        Question(
            text=(
                "The new_recommendation_engine feature flag is set to False. "
                "The team believes the ML recommendation pipeline is not running. "
                "Is the pipeline actually stopped?"
            ),
            choices={
                "A": "Yes — the feature flag prevents the ML engine from executing",
                "B": "No — the ML pipeline runs at module import time in RecommendationService, "
                     "regardless of the feature flag; the flag only controls which results are returned",
                "C": "Yes — RecommendationService checks the flag in __init__",
                "D": "Partially — the pipeline runs but doesn't send data externally",
            },
            correct="B",
        ),
        Question(
            text=(
                "The cost dashboard shows ml_recommendation_api expected cost as $0 "
                "(disabled). But the actual cloud bill shows ~$500/month for that API. "
                "What's the cause?"
            ),
            choices={
                "A": "A billing error from the ML API provider",
                "B": "The recommendation service's module-level pipeline continues to call the ML API "
                     "even though the feature flag is off; the cost dashboard reflects the expected "
                     "(flag-based) cost, not actual usage",
                "C": "Another service is using the same ML API key",
                "D": "Cached API responses from before the flag was turned off are being re-billed",
            },
            correct="B",
        ),
        Question(
            text=(
                "To stop the ML pipeline cost, the team sets the feature flag to False "
                "(it already is). They also update the cost dashboard to $0. "
                "Will the ML API costs stop?"
            ),
            choices={
                "A": "Yes — confirming the flag is False will stop all ML API calls",
                "B": "No — the pipeline runs at import time regardless of the flag; they need to "
                     "either remove the import of RecommendationService or fix the module-level code",
                "C": "Yes — after restarting the application to clear the module cache",
                "D": "No — but they need to also set experimental_search to False",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑦ × low — Unused helper imports vulnerable dependency
# =====================================================================

_L_UTILS_A = """\
# utils/data_helpers.py
\"\"\"Data processing utilities.\"\"\"
import json
import csv
from datetime import datetime

def parse_csv(filepath: str) -> list[dict]:
    with open(filepath) as f:
        return list(csv.DictReader(f))

def format_date(dt: datetime, fmt: str = "%Y-%m-%d") -> str:
    return dt.strftime(fmt)

def parse_xml(content: str) -> dict:
    \"\"\"Parse XML content into dict. Legacy — only used by old import job.\"\"\"
    import xmltodict  # xmltodict==0.12.0 in requirements.txt
    return xmltodict.parse(content)

def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
"""

_L_UTILS_B = """\
# utils/data_helpers.py
\"\"\"Data processing utilities.\"\"\"
import json
import csv
from datetime import datetime

def parse_csv(filepath: str) -> list[dict]:
    with open(filepath) as f:
        return list(csv.DictReader(f))

def format_date(dt: datetime, fmt: str = "%Y-%m-%d") -> str:
    return dt.strftime(fmt)

def parse_xml(content: str) -> dict:
    \"\"\"Parse XML content into dict. Legacy — only used by old import job.\"\"\"
    import xmltodict  # xmltodict==0.12.0 in requirements.txt
    return xmltodict.parse(content)

# ⚠ DEAD CODE: parse_xml() is never called anywhere in the codebase.
# The old import job that used it was removed 6 months ago. However,
# xmltodict==0.12.0 is still in requirements.txt because of this function.
# This version has a known XML entity expansion vulnerability (CVE-2023-XXXX)
# that the security scanner flags. Removing parse_xml() would allow removing
# xmltodict from requirements.txt, eliminating the vulnerability.

def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
"""

_L_REQ = """\
# requirements.txt
flask==3.0.0
sqlalchemy==2.0.23
redis==5.0.1
xmltodict==0.12.0
requests==2.31.0
pyjwt==2.8.0
"""

_L_SCAN = """\
# security/scan_report.txt
Vulnerability Scan Results (2024-01-15)

HIGH: xmltodict==0.12.0 — XML Entity Expansion (CVE-2023-XXXX)
  Affected file: utils/data_helpers.py
  Recommendation: Update to xmltodict>=0.13.0 or remove if unused

LOW: requests==2.31.0 — Minor header injection (CVE-2024-YYYY)
  Recommendation: Update to requests>=2.32.0
"""

_L_IMPORTS = """\
# api/import_handler.py
from utils.data_helpers import parse_csv, safe_json_loads

def import_data(request):
    \"\"\"POST /import — Import data from CSV or JSON.\"\"\"
    if request.content_type == "text/csv":
        data = parse_csv(request.file)
    else:
        data = safe_json_loads(request.body)
    return {"imported": len(data)}, 200
"""

P07_LOW = Scenario(
    pattern="⑦",
    pattern_name="Dead Code",
    severity="low",
    description="Unused parse_xml() keeps vulnerable xmltodict in requirements; security scanner flags it",
    visible_files={
        "utils/data_helpers.py": _L_UTILS_A,
        "requirements.txt": _L_REQ,
        "security/scan_report.txt": _L_SCAN,
        "api/import_handler.py": _L_IMPORTS,
    },
    annotated_files={
        "utils/data_helpers.py": _L_UTILS_B,
    },
    hidden_file_name="scripts/find_unused.py",
    hidden_file_description="Analysis shows parse_xml() has zero callers; xmltodict only needed by this function",
    questions=[
        Question(
            text=(
                "The security scanner flags xmltodict==0.12.0 as vulnerable. "
                "A developer checks utils/data_helpers.py and sees parse_xml() uses it. "
                "They plan to update xmltodict to 0.13.0. Is this the right fix?"
            ),
            choices={
                "A": "Yes — updating to the patched version eliminates the vulnerability",
                "B": "It works but is wasteful — parse_xml() is never called, so removing "
                     "the function and xmltodict from requirements.txt entirely would be better",
                "C": "No — the vulnerability is in how the function is called, not the library version",
                "D": "Yes — and they should also update the function to use defusedxml",
            },
            correct="B",
        ),
        Question(
            text=(
                "The import_handler.py only imports parse_csv and safe_json_loads from data_helpers. "
                "Is parse_xml() used anywhere else in the codebase?"
            ),
            choices={
                "A": "Yes — it's used by the import job for XML data",
                "B": "No — the old import job that used it was removed; parse_xml() has zero callers "
                     "and is dead code keeping a vulnerable dependency in the project",
                "C": "Unknown — it might be called via dynamic import or reflection",
                "D": "Yes — the security scan report references it, meaning it's in active use",
            },
            correct="B",
        ),
        Question(
            text=(
                "The team decides to keep parse_xml() 'just in case' and updates xmltodict to 0.13.0. "
                "Six months later, a new vulnerability is found in xmltodict 0.13.0. "
                "Is the team affected?"
            ),
            choices={
                "A": "No — the function is never called so the vulnerability can't be exploited",
                "B": "Yes — even though parse_xml() is dead code, xmltodict is still installed "
                     "and could be exploited via other attack vectors (e.g., if another module "
                     "accidentally imports it, or via supply chain attacks on the installed package)",
                "C": "No — the security scanner would catch it before deployment",
                "D": "Depends on whether the vulnerability is in parsing or installation",
            },
            correct="B",
        ),
    ],
)
