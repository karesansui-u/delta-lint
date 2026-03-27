"""⑥ Lifecycle Ordering — 3重大度シナリオ.

high:   DB migration runs AFTER app starts → new code references non-existent column
medium: Cache warm-up happens AFTER first requests → cold cache stampede
low:    Cleanup hook runs BEFORE pending writes flush → data loss on shutdown
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ⑥ × high — Migration runs after app start
# =====================================================================

_H_DEPLOY_A = """\
# deploy/startup.sh
#!/bin/bash
echo "Starting application..."

# Start the application
python app.py &
APP_PID=$!

# Run database migrations
echo "Running database migrations..."
python manage.py migrate

# Wait for app
wait $APP_PID
"""

_H_DEPLOY_B = """\
# deploy/startup.sh
#!/bin/bash
echo "Starting application..."

# Start the application
python app.py &
APP_PID=$!

# Run database migrations
echo "Running database migrations..."
python manage.py migrate

# ⚠ LIFECYCLE ORDERING: The app starts BEFORE migrations run.
# If a migration adds a new column (e.g., users.preferences JSONB),
# the app code that references this column will get "column does not exist"
# errors until the migration completes. The race window is typically 5-30
# seconds but can be minutes for large tables. During this window,
# all requests touching the new column return 500 errors.

# Wait for app
wait $APP_PID
"""

_H_APP = """\
# app.py
import logging
from api import users, products

logger = logging.getLogger(__name__)

def create_app():
    app = App()
    app.register(users)
    app.register(products)
    logger.info("App ready, accepting requests")
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8080)
"""

_H_MIGRATION = """\
# migrations/0042_add_user_preferences.py
\"\"\"Add preferences JSONB column to users table.\"\"\"

def upgrade():
    op.add_column("users", sa.Column("preferences", sa.JSON, default={}))
    # For large tables, this ALTER TABLE can take 10-30 seconds

def downgrade():
    op.drop_column("users", "preferences")
"""

_H_API = """\
# api/users.py
from db import session
from models.user import User

def get_user_preferences(request, user_id: str):
    \"\"\"GET /users/:id/preferences\"\"\"
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        return {"error": "Not found"}, 404
    return {"preferences": user.preferences or {}}, 200

def update_preferences(request, user_id: str):
    \"\"\"PATCH /users/:id/preferences\"\"\"
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        return {"error": "Not found"}, 404
    user.preferences = request.json
    session.commit()
    return {"preferences": user.preferences}, 200
"""

_H_DOCKER = """\
# deploy/Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["bash", "deploy/startup.sh"]
"""

_H_HEALTH = """\
# api/health.py
def healthcheck(request):
    return {"status": "ok"}, 200
"""

P06_HIGH = Scenario(
    pattern="⑥",
    pattern_name="Lifecycle Ordering",
    severity="high",
    description="App starts accepting requests before DB migration adds new column; 500 errors during race window",
    visible_files={
        "deploy/startup.sh": _H_DEPLOY_A,
        "app.py": _H_APP,
        "migrations/0042_add_user_preferences.py": _H_MIGRATION,
        "api/users.py": _H_API,
        "deploy/Dockerfile": _H_DOCKER,
        "api/health.py": _H_HEALTH,
    },
    annotated_files={
        "deploy/startup.sh": _H_DEPLOY_B,
    },
    hidden_file_name="deploy/orchestrator.py",
    hidden_file_description="Starts app.py in background, then runs migrations; no readiness gate",
    questions=[
        Question(
            text=(
                "A user calls GET /users/:id/preferences immediately after a new deployment. "
                "The deployment adds the 'preferences' column via migration 0042. "
                "What happens?"
            ),
            choices={
                "A": "Success — the migration runs before the app starts, so the column exists",
                "B": "500 error — the app starts before the migration runs, and the query "
                     "references a column that doesn't exist yet",
                "C": "Returns empty preferences — the column defaults to null before migration",
                "D": "The app waits for migrations to complete before accepting requests",
            },
            correct="B",
        ),
        Question(
            text=(
                "The load balancer health check calls GET /health which returns 200. "
                "The load balancer routes traffic to the new instance. "
                "Is the instance actually ready to serve user requests?"
            ),
            choices={
                "A": "Yes — if the health check passes, all endpoints are ready",
                "B": "Not necessarily — the health check only verifies the app is running, not "
                     "that migrations are complete; endpoints using new columns will fail",
                "C": "Yes — the ORM handles missing columns gracefully",
                "D": "No — but the load balancer automatically retries on a different instance",
            },
            correct="B",
        ),
        Question(
            text=(
                "The team runs 3 replicas behind a load balancer. During rolling deployment, "
                "replica 1 starts and begins serving requests while its migration runs. "
                "Replicas 2 and 3 are still on the old version. What happens?"
            ),
            choices={
                "A": "Traffic is evenly distributed and all replicas work fine",
                "B": "Replica 1 returns 500 errors for preference endpoints while waiting for "
                     "its migration; once the migration runs, it modifies the shared database, "
                     "which is fine for new-column additions but risky for schema changes",
                "C": "The load balancer detects 500 errors and removes replica 1 from rotation",
                "D": "Replicas 2 and 3 crash because the shared database schema changed",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑥ × medium — Cache warm-up after first requests
# =====================================================================

_M_STARTUP_A = """\
# app.py
import logging
from threading import Thread

logger = logging.getLogger(__name__)

def create_app():
    app = App()
    app.register_routes()

    # Start cache warm-up in background
    Thread(target=warm_up_cache, daemon=True).start()

    logger.info("App accepting requests")
    return app

def warm_up_cache():
    \"\"\"Pre-populate frequently accessed cache entries.\"\"\"
    from services.cache_warmer import CacheWarmer
    warmer = CacheWarmer()
    warmer.warm_popular_products(limit=1000)
    warmer.warm_category_counts()
    warmer.warm_featured_collections()
"""

_M_STARTUP_B = """\
# app.py
import logging
from threading import Thread

logger = logging.getLogger(__name__)

# ⚠ LIFECYCLE ORDERING: The app starts accepting requests BEFORE the cache
# warm-up completes. warm_up_cache() runs in a background thread and takes
# 30-90 seconds to populate 1000 products + categories + collections.
# During this window, EVERY request is a cache miss that hits the database.
# With typical load of 500 req/s at startup, this creates a "thundering herd"
# of ~15,000-45,000 uncached DB queries, often overwhelming the database.

def create_app():
    app = App()
    app.register_routes()

    # Start cache warm-up in background
    Thread(target=warm_up_cache, daemon=True).start()

    logger.info("App accepting requests")
    return app

def warm_up_cache():
    \"\"\"Pre-populate frequently accessed cache entries.\"\"\"
    from services.cache_warmer import CacheWarmer
    warmer = CacheWarmer()
    warmer.warm_popular_products(limit=1000)
    warmer.warm_category_counts()
    warmer.warm_featured_collections()
"""

_M_PRODUCT = """\
# api/products.py
from services.cache_manager import cache
from services.product_service import ProductService

product_service = ProductService()

def get_product(request, product_id: str):
    cached = cache.get(f"product:{product_id}")
    if cached:
        return cached, 200
    product = product_service.get_by_id(product_id)
    cache.set(f"product:{product_id}", product.to_dict(), ttl=300)
    return product.to_dict(), 200
"""

_M_LB = """\
# deploy/load_balancer.conf
upstream app {
    server app1:8080;
    server app2:8080;
    server app3:8080;
}

server {
    location / {
        proxy_pass http://app;
    }

    location /health {
        proxy_pass http://app/health;
    }
}
"""

P06_MEDIUM = Scenario(
    pattern="⑥",
    pattern_name="Lifecycle Ordering",
    severity="medium",
    description="Cache warm-up runs in background thread; app serves cold-cache requests causing DB stampede",
    visible_files={
        "app.py": _M_STARTUP_A,
        "api/products.py": _M_PRODUCT,
        "deploy/load_balancer.conf": _M_LB,
    },
    annotated_files={
        "app.py": _M_STARTUP_B,
    },
    hidden_file_name="services/cache_warmer.py",
    hidden_file_description="warm_popular_products() takes 30-90s; runs in background thread",
    questions=[
        Question(
            text=(
                "The app starts and immediately receives 500 requests/second. "
                "The cache warm-up is running in a background thread. "
                "What percentage of initial requests hit the database directly?"
            ),
            choices={
                "A": "~0% — the cache warm-up completes before the app accepts requests",
                "B": "~100% — the cache is empty when requests arrive; every request is a cache miss "
                     "that queries the database until warm-up completes",
                "C": "~50% — half the products are warmed up by the time traffic starts",
                "D": "~0% — requests wait for the cache to be populated before proceeding",
            },
            correct="B",
        ),
        Question(
            text=(
                "During startup, the database connection pool is exhausted by cache-miss queries. "
                "The warm-up thread also needs DB connections to populate the cache. "
                "What happens?"
            ),
            choices={
                "A": "The warm-up thread has priority and completes quickly",
                "B": "Deadlock-like situation — request threads consume all DB connections, "
                     "the warm-up thread can't get a connection to populate the cache, "
                     "so requests keep missing the cache indefinitely until connections free up",
                "C": "The warm-up thread is cancelled to free connections for requests",
                "D": "The database auto-scales to handle the increased connection demand",
            },
            correct="B",
        ),
        Question(
            text=(
                "The team adds a readiness probe that returns 200 only after warm_up_cache() completes. "
                "The load balancer only routes traffic to ready instances. "
                "Does this fix the thundering herd problem?"
            ),
            choices={
                "A": "No — the background thread is a daemon and may not block the readiness signal",
                "B": "Yes — traffic only reaches the instance after the cache is warm, "
                     "so all requests hit the cache from the start",
                "C": "Partially — it fixes single-instance startup but not rolling deployments",
                "D": "No — the readiness probe can't check cache state",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑥ × low — Cleanup before write flush
# =====================================================================

_L_SHUTDOWN_A = """\
# app.py
import signal
import logging

logger = logging.getLogger(__name__)

def create_app():
    app = App()
    signal.signal(signal.SIGTERM, handle_shutdown)
    return app

def handle_shutdown(signum, frame):
    \"\"\"Graceful shutdown handler.\"\"\"
    logger.info("Received SIGTERM, shutting down...")

    # Cleanup resources
    cleanup_temp_files()
    close_connections()

    # Flush pending writes
    flush_write_buffer()

    logger.info("Shutdown complete")
    exit(0)

def cleanup_temp_files():
    import shutil
    shutil.rmtree("/tmp/app_cache", ignore_errors=True)

def close_connections():
    from services.db_pool import get_pool
    get_pool().dispose()

def flush_write_buffer():
    from services.write_buffer import WriteBuffer
    WriteBuffer().flush()
"""

_L_SHUTDOWN_B = """\
# app.py
import signal
import logging

logger = logging.getLogger(__name__)

# ⚠ LIFECYCLE ORDERING: handle_shutdown() calls close_connections() BEFORE
# flush_write_buffer(). The write buffer uses the DB connection pool to
# flush pending inserts/updates. By the time flush runs, the pool is
# already disposed, so pending writes silently fail (WriteBuffer catches
# the exception and logs at DEBUG level).
# Affected data: analytics events, audit logs, async metric updates.
# Typically 10-100 pending writes are lost per graceful shutdown.

def create_app():
    app = App()
    signal.signal(signal.SIGTERM, handle_shutdown)
    return app

def handle_shutdown(signum, frame):
    \"\"\"Graceful shutdown handler.\"\"\"
    logger.info("Received SIGTERM, shutting down...")

    # Cleanup resources
    cleanup_temp_files()
    close_connections()

    # Flush pending writes
    flush_write_buffer()

    logger.info("Shutdown complete")
    exit(0)

def cleanup_temp_files():
    import shutil
    shutil.rmtree("/tmp/app_cache", ignore_errors=True)

def close_connections():
    from services.db_pool import get_pool
    get_pool().dispose()

def flush_write_buffer():
    from services.write_buffer import WriteBuffer
    WriteBuffer().flush()
"""

_L_BUFFER = """\
# api/analytics.py
from services.write_buffer import WriteBuffer

buffer = WriteBuffer()

def track_event(request):
    \"\"\"POST /analytics/track — Buffer analytics event for batch write.\"\"\"
    buffer.add({
        "event": request.json["event"],
        "user_id": request.json.get("user_id"),
        "timestamp": request.json.get("timestamp"),
    })
    return {"status": "queued"}, 202
"""

_L_CONFIG = """\
# config/app.py
WRITE_BUFFER_SIZE = 100       # Flush after 100 pending writes
WRITE_BUFFER_INTERVAL = 30    # Or flush every 30 seconds
GRACEFUL_SHUTDOWN_TIMEOUT = 10  # Max seconds for shutdown
"""

P06_LOW = Scenario(
    pattern="⑥",
    pattern_name="Lifecycle Ordering",
    severity="low",
    description="Shutdown handler closes DB connections before flushing write buffer; pending writes lost",
    visible_files={
        "app.py": _L_SHUTDOWN_A,
        "api/analytics.py": _L_BUFFER,
        "config/app.py": _L_CONFIG,
    },
    annotated_files={
        "app.py": _L_SHUTDOWN_B,
    },
    hidden_file_name="services/write_buffer.py",
    hidden_file_description="flush() uses DB pool to write; if pool is disposed, catches exception and logs at DEBUG",
    questions=[
        Question(
            text=(
                "The app receives SIGTERM for a graceful restart. There are 50 pending "
                "analytics events in the write buffer. After shutdown completes, "
                "how many of these events are persisted to the database?"
            ),
            choices={
                "A": "All 50 — flush_write_buffer() persists them before exit",
                "B": "0 — close_connections() disposes the DB pool before flush_write_buffer() runs; "
                     "the flush silently fails because no connections are available",
                "C": "All 50 — the write buffer uses its own dedicated connection, not the pool",
                "D": "Some — depending on how many were written before the connection pool timeout",
            },
            correct="B",
        ),
        Question(
            text=(
                "The shutdown log shows 'Shutdown complete' with no errors or warnings. "
                "But analytics data for the last 30 seconds is missing from the database. "
                "Is the shutdown log misleading?"
            ),
            choices={
                "A": "No — if the log shows 'complete' then all data was flushed successfully",
                "B": "Yes — the write buffer catches the DB connection error at DEBUG level, "
                     "so no visible error appears; the data loss is silent",
                "C": "No — the missing data was in transit and will appear after replication lag",
                "D": "Yes — but only because the flush timeout was too short",
            },
            correct="B",
        ),
        Question(
            text=(
                "A developer swaps the order: calls flush_write_buffer() BEFORE close_connections(). "
                "Does this fix the data loss on shutdown?"
            ),
            choices={
                "A": "No — the write buffer has a separate issue that causes data loss",
                "B": "Yes — flushing before disposing the pool ensures all pending writes "
                     "have active connections to use",
                "C": "Partially — it fixes the ordering but the GRACEFUL_SHUTDOWN_TIMEOUT may still kill the process",
                "D": "No — cleanup_temp_files() also deletes cached write data",
            },
            correct="B",
        ),
    ],
)
