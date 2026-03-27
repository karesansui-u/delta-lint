"""③ External Spec Divergence — 3重大度シナリオ.

high:   API docs say rate limit is 100/min, but hidden limiter uses 10/min
medium: OpenAPI spec says field is required, but hidden validator allows null
low:    README says default sort is "created_at DESC", but service sorts by "name ASC"
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ③ × high — Rate limit: docs say 100/min, code enforces 10/min
# =====================================================================

_H_DOCS_A = """\
# docs/api_reference.md
## Rate Limiting

All API endpoints are rate-limited to prevent abuse.

| Tier | Limit | Window |
|------|-------|--------|
| Free | 100 requests/minute | Rolling window |
| Pro | 1000 requests/minute | Rolling window |
| Enterprise | Unlimited | N/A |

When the rate limit is exceeded, the API returns HTTP 429 with a
`Retry-After` header indicating seconds until the next allowed request.

## Authentication

Include your API key in the `X-API-Key` header:
```
X-API-Key: your_api_key_here
```
"""

_H_DOCS_B = """\
# docs/api_reference.md
## Rate Limiting

All API endpoints are rate-limited to prevent abuse.

| Tier | Limit | Window |
|------|-------|--------|
| Free | 100 requests/minute | Rolling window |
| Pro | 1000 requests/minute | Rolling window |
| Enterprise | Unlimited | N/A |

When the rate limit is exceeded, the API returns HTTP 429 with a
`Retry-After` header indicating seconds until the next allowed request.

# ⚠ SPEC DIVERGENCE: The rate limiter (middleware/rate_limiter.py) actually
# enforces 10 requests/minute for Free tier (not 100 as documented above).
# The limiter was tightened from 100 to 10 during an abuse incident but the
# documentation was never updated. Pro tier is correct at 1000/min.
# Clients building to the documented 100/min limit will hit 429 errors
# after just 10 requests.

## Authentication

Include your API key in the `X-API-Key` header:
```
X-API-Key: your_api_key_here
```
"""

_H_MIDDLEWARE_A = """\
# api/app.py
from middleware.rate_limiter import RateLimiter
from middleware.auth import authenticate

rate_limiter = RateLimiter()

def handle_request(request):
    auth_result = authenticate(request)
    if auth_result.error:
        return auth_result.error, 401

    limit_result = rate_limiter.check(
        key=auth_result.api_key,
        tier=auth_result.tier,
    )
    if not limit_result.allowed:
        return {"error": "Rate limit exceeded"}, 429

    return route(request)
"""

_H_MIDDLEWARE_B = """\
# api/app.py
from middleware.rate_limiter import RateLimiter
from middleware.auth import authenticate

# ⚠ The RateLimiter uses hardcoded limits that differ from docs/api_reference.md.
# Free tier: 10/min (docs say 100/min)
rate_limiter = RateLimiter()

def handle_request(request):
    auth_result = authenticate(request)
    if auth_result.error:
        return auth_result.error, 401

    limit_result = rate_limiter.check(
        key=auth_result.api_key,
        tier=auth_result.tier,
    )
    if not limit_result.allowed:
        return {"error": "Rate limit exceeded"}, 429

    return route(request)
"""

_H_SDK = """\
# sdk/python/client.py
\"\"\"Official Python SDK for the API.\"\"\"
import time
import requests

class APIClient:
    RATE_LIMITS = {
        "free": 100,   # per minute, from docs
        "pro": 1000,
        "enterprise": float("inf"),
    }

    def __init__(self, api_key: str, tier: str = "free"):
        self.api_key = api_key
        self.tier = tier
        self._request_count = 0
        self._window_start = time.time()

    def _throttle(self):
        \"\"\"Client-side rate limiting based on documented limits.\"\"\"
        now = time.time()
        if now - self._window_start >= 60:
            self._request_count = 0
            self._window_start = now
        if self._request_count >= self.RATE_LIMITS.get(self.tier, 100):
            sleep_time = 60 - (now - self._window_start)
            time.sleep(max(0, sleep_time))
            self._request_count = 0
            self._window_start = time.time()

    def get(self, path: str) -> dict:
        self._throttle()
        self._request_count += 1
        resp = requests.get(f"https://api.example.com{path}",
                          headers={"X-API-Key": self.api_key})
        return resp.json()
"""

_H_TEST = """\
# tests/test_rate_limit.py
\"\"\"Integration tests for rate limiting.\"\"\"

def test_free_tier_allows_100_per_minute():
    \"\"\"Verify free tier can make 100 requests per minute per docs.\"\"\"
    # TODO: This test keeps failing — 429 after ~10 requests
    # Skipped pending investigation
    pass

def test_pro_tier_allows_1000_per_minute():
    \"\"\"Verify pro tier limit.\"\"\"
    pass
"""

P03_HIGH = Scenario(
    pattern="③",
    pattern_name="External Spec Divergence",
    severity="high",
    description="API docs say 100 req/min for free tier but rate limiter enforces 10/min",
    visible_files={
        "docs/api_reference.md": _H_DOCS_A,
        "api/app.py": _H_MIDDLEWARE_A,
        "sdk/python/client.py": _H_SDK,
        "tests/test_rate_limit.py": _H_TEST,
    },
    annotated_files={
        "docs/api_reference.md": _H_DOCS_B,
        "api/app.py": _H_MIDDLEWARE_B,
    },
    hidden_file_name="middleware/rate_limiter.py",
    hidden_file_description="Hardcodes free tier limit as 10/min, not 100/min as documented",
    questions=[
        Question(
            text=(
                "A developer builds an integration using the official Python SDK. "
                "Their app is on the free tier and sends 50 requests in the first minute. "
                "The SDK's client-side throttle allows this (limit=100). What happens?"
            ),
            choices={
                "A": "All 50 requests succeed — the free tier allows 100/min",
                "B": "The first 10 succeed, then requests 11-50 get 429 errors — "
                     "the actual server limit is 10/min, not 100/min as documented",
                "C": "All 50 succeed but with increasing latency due to server-side queuing",
                "D": "The SDK retries failed requests automatically, so all eventually succeed",
            },
            correct="B",
        ),
        Question(
            text=(
                "The test test_free_tier_allows_100_per_minute() is marked as 'TODO: keeps failing'. "
                "What is the root cause of the test failure?"
            ),
            choices={
                "A": "A bug in the test setup — the test isn't authenticating properly",
                "B": "The server enforces 10/min for free tier, not 100/min; the test is correct "
                     "per the docs but the implementation doesn't match",
                "C": "Network latency causes some requests to be counted in the next window",
                "D": "The rate limiter uses a fixed window instead of rolling window",
            },
            correct="B",
        ),
        Question(
            text=(
                "Customer support receives complaints from free-tier users getting 429 errors "
                "'way too early'. Support checks the API docs and confirms the 100/min limit. "
                "They tell users to check their implementation. Is support's response correct?"
            ),
            choices={
                "A": "Yes — the users must be exceeding 100/min somehow",
                "B": "No — the actual limit is 10/min; the docs are wrong and support is "
                     "giving incorrect guidance based on outdated documentation",
                "C": "Partially — the limit is 100/min but the rolling window calculation "
                     "sometimes double-counts requests",
                "D": "Yes — but the users should also check for concurrent request issues",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ③ × medium — Required field が null 許容
# =====================================================================

_M_SPEC_A = """\
# docs/openapi.yaml (excerpt)
paths:
  /api/events:
    post:
      summary: Create a new event
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - title
                - start_time
                - organizer_email
              properties:
                title:
                  type: string
                  minLength: 1
                start_time:
                  type: string
                  format: date-time
                end_time:
                  type: string
                  format: date-time
                organizer_email:
                  type: string
                  format: email
                description:
                  type: string
      responses:
        '201':
          description: Event created
        '400':
          description: Validation error
"""

_M_SPEC_B = """\
# docs/openapi.yaml (excerpt)
paths:
  /api/events:
    post:
      summary: Create a new event
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - title
                - start_time
                - organizer_email
              properties:
                title:
                  type: string
                  minLength: 1
                start_time:
                  type: string
                  format: date-time
                end_time:
                  type: string
                  format: date-time
                organizer_email:
                  type: string
                  format: email
                description:
                  type: string
      responses:
        '201':
          description: Event created
        '400':
          description: Validation error

# ⚠ SPEC DIVERGENCE: The OpenAPI spec marks organizer_email as required,
# but services/event_validator.py only checks for title and start_time.
# organizer_email can be null or missing. Events without organizer_email
# are created successfully but email notifications silently fail.
"""

_M_API = """\
# api/events.py
from services.event_service import EventService

event_service = EventService()

def create_event(request):
    \"\"\"POST /api/events — per OpenAPI spec, title/start_time/organizer_email required.\"\"\"
    data = request.json
    errors = event_service.validate_and_create(data)
    if errors:
        return {"errors": errors}, 400
    event = event_service.create(data)
    return event.to_dict(), 201
"""

_M_NOTIF = """\
# services/notification_service.py
import logging

logger = logging.getLogger(__name__)

def send_event_confirmation(event):
    if not event.organizer_email:
        logger.debug(f"Skipping notification for event {event.id}: no organizer email")
        return
    # send email...
"""

_M_MODEL = """\
# models/event.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

@dataclass
class Event:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    organizer_email: Optional[str] = None
    description: str = ""
"""

P03_MEDIUM = Scenario(
    pattern="③",
    pattern_name="External Spec Divergence",
    severity="medium",
    description="OpenAPI spec requires organizer_email but validator only checks title and start_time",
    visible_files={
        "docs/openapi.yaml": _M_SPEC_A,
        "api/events.py": _M_API,
        "services/notification_service.py": _M_NOTIF,
        "models/event.py": _M_MODEL,
    },
    annotated_files={
        "docs/openapi.yaml": _M_SPEC_B,
    },
    hidden_file_name="services/event_validator.py",
    hidden_file_description="Only validates title and start_time; organizer_email is not checked",
    questions=[
        Question(
            text=(
                "A client sends POST /api/events with title and start_time but omits "
                "organizer_email entirely. Per the OpenAPI spec, organizer_email is required. "
                "What HTTP status code does the server return?"
            ),
            choices={
                "A": "400 — validation fails because organizer_email is required per the spec",
                "B": "201 — the event is created because the validator doesn't actually check "
                     "organizer_email despite the spec marking it as required",
                "C": "422 — the server returns a schema validation error",
                "D": "500 — a null reference error when trying to send the confirmation email",
            },
            correct="B",
        ),
        Question(
            text=(
                "An event is created without organizer_email. "
                "Does the organizer receive a confirmation email?"
            ),
            choices={
                "A": "Yes — the notification service uses a default email from the user profile",
                "B": "No — the notification service silently skips the email because "
                     "organizer_email is None, and only logs at DEBUG level",
                "C": "No — the notification service throws an error that is caught upstream",
                "D": "Yes — but it's sent to the admin contact instead",
            },
            correct="B",
        ),
        Question(
            text=(
                "A team generates client code from the OpenAPI spec (e.g., using openapi-generator). "
                "The generated client marks organizer_email as required and refuses to send "
                "requests without it. Is the generated client correct?"
            ),
            choices={
                "A": "Yes — the generated client matches the spec and the server enforces it",
                "B": "The client is correct per the spec, but the server doesn't enforce it — "
                     "the client is stricter than the server",
                "C": "No — the generated client should treat all fields as optional",
                "D": "Yes — and requests without organizer_email will fail with 400",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ③ × low — Default sort order の乖離
# =====================================================================

_L_README_A = """\
# docs/README.md
## API Endpoints

### GET /api/articles

Returns a list of articles.

**Default behavior:**
- Sorted by `created_at` descending (newest first)
- Paginated: 20 per page

**Query parameters:**
- `page` (int): Page number (default: 1)
- `sort` (string): Sort field (default: "created_at")
- `order` (string): Sort direction, "asc" or "desc" (default: "desc")
- `category` (string): Filter by category
"""

_L_README_B = """\
# docs/README.md
## API Endpoints

### GET /api/articles

Returns a list of articles.

**Default behavior:**
- Sorted by `created_at` descending (newest first)
- Paginated: 20 per page

# ⚠ SPEC DIVERGENCE: The actual service sorts by `title` ascending (alphabetical)
# by default, not by `created_at` descending. The service was refactored to use
# alphabetical sorting for a UI redesign, but the README was not updated.
# Users expecting newest-first will see articles in alphabetical order instead.

**Query parameters:**
- `page` (int): Page number (default: 1)
- `sort` (string): Sort field (default: "created_at")
- `order` (string): Sort direction, "asc" or "desc" (default: "desc")
- `category` (string): Filter by category
"""

_L_API = """\
# api/articles.py
from services.article_service import ArticleService

article_service = ArticleService()

def list_articles(request):
    page = int(request.args.get("page", 1))
    sort = request.args.get("sort")
    order = request.args.get("order")
    category = request.args.get("category")

    articles = article_service.list(
        page=page,
        sort_by=sort,
        sort_order=order,
        category=category,
    )
    return {"articles": [a.to_dict() for a in articles]}, 200
"""

_L_MODEL = """\
# models/article.py
from dataclasses import dataclass, field
from datetime import datetime
import uuid

@dataclass
class Article:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    body: str = ""
    category: str = ""
    author_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
"""

_L_FRONTEND = """\
# frontend/article_list.py
\"\"\"Article list component — expects articles sorted by newest first.\"\"\"

def render_article_list(articles: list) -> str:
    # Assumes first article is the most recent
    if articles:
        featured = articles[0]
        rest = articles[1:]
    else:
        featured = None
        rest = []
    return f"Featured: {featured['title'] if featured else 'None'}"
"""

P03_LOW = Scenario(
    pattern="③",
    pattern_name="External Spec Divergence",
    severity="low",
    description="README says default sort is created_at DESC but service sorts by title ASC",
    visible_files={
        "docs/README.md": _L_README_A,
        "api/articles.py": _L_API,
        "models/article.py": _L_MODEL,
        "frontend/article_list.py": _L_FRONTEND,
    },
    annotated_files={
        "docs/README.md": _L_README_B,
    },
    hidden_file_name="services/article_service.py",
    hidden_file_description="Default sort is title ASC (alphabetical), not created_at DESC",
    questions=[
        Question(
            text=(
                "A frontend developer calls GET /api/articles without sort parameters, "
                "expecting newest articles first (per the README). The 'featured' article "
                "in the UI component uses articles[0]. What article is featured?"
            ),
            choices={
                "A": "The most recently created article (newest first, per README)",
                "B": "The article whose title comes first alphabetically — the service "
                     "defaults to title ASC, not created_at DESC",
                "C": "A random article — no default sort is applied",
                "D": "The oldest article — the service sorts by created_at ASC by default",
            },
            correct="B",
        ),
        Question(
            text=(
                "A mobile app caches the first page of articles and shows a 'New' badge "
                "on the first item, assuming it's the most recent. Is this correct?"
            ),
            choices={
                "A": "Yes — the default sort is created_at DESC so the first item is newest",
                "B": "No — the first item is alphabetically first by title, which may be an old article; "
                     "the 'New' badge would be misleading",
                "C": "Depends on whether the cache invalidation uses the same sort order",
                "D": "Yes — but only if the app explicitly passes sort=created_at&order=desc",
            },
            correct="B",
        ),
        Question(
            text=(
                "To fix a reported issue of 'wrong article order', a developer reads the README "
                "and confirms 'created_at DESC is the default'. They conclude the bug is in "
                "the frontend. Is their diagnosis correct?"
            ),
            choices={
                "A": "Yes — the API returns articles in the documented order, so it's a frontend bug",
                "B": "No — the actual service default is title ASC, not created_at DESC; "
                     "the README is wrong and the bug is in the backend service",
                "C": "Partially — the frontend and backend both have bugs",
                "D": "Yes — the database index determines the default sort order",
            },
            correct="B",
        ),
    ],
)
