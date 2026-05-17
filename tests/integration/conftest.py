import os
import sys

# Project structure
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test env vars BEFORE any imports that read them
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5433/alphadivision_test"
os.environ["REDIS_URL"] = "redis://localhost:6380"
os.environ["CONFIG_FILE"] = os.path.join(PROJECT_ROOT, "config.toml")

# Add service directories to sys.path for importing service modules
sys.path.insert(0, os.path.join(PROJECT_ROOT, "services/data"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "services/analysis"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "services/dashboard"))

import pytest
import psycopg2
import redis as redis_lib

TEST_DB_URL = os.environ["DATABASE_URL"]
TEST_REDIS_URL = os.environ["REDIS_URL"]


@pytest.fixture(autouse=True)
def reset_shared_singletons():
    """Reset shared module singletons before each test."""
    import shared.db as db_mod
    import shared.redis_client as redis_mod
    if db_mod._pool:
        db_mod._pool.closeall()
    db_mod._pool = None
    redis_mod._client = None
    yield
    if db_mod._pool:
        db_mod._pool.closeall()
    db_mod._pool = None
    redis_mod._client = None


@pytest.fixture(scope="session")
def raw_db():
    """Direct psycopg2 connection for test assertions (not via shared.db)."""
    conn = psycopg2.connect(TEST_DB_URL)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture
def db_cursor(raw_db):
    """Cursor with autocommit for test setup/assertions."""
    with raw_db.cursor() as cur:
        yield cur


@pytest.fixture(autouse=True)
def clean_tables(raw_db):
    """Truncate all tables before each test."""
    with raw_db.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE trades, signals, decisions, api_health, daily_pnl RESTART IDENTITY CASCADE"
        )
    yield


@pytest.fixture(scope="session")
def raw_redis():
    """Direct redis-py client for test assertions (not via shared.redis_client)."""
    r = redis_lib.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    r.close()


@pytest.fixture(autouse=True)
def clean_redis(raw_redis):
    """Flush Redis before each test."""
    raw_redis.flushall()
    yield
