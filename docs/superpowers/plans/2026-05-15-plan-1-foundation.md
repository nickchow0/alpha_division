# AlphaDivision — Plan 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the shared infrastructure all five microservices depend on — directory structure, PostgreSQL schema, shared library (logger, db, redis), Docker Compose, and base Dockerfiles.

**Architecture:** Five services (data, analysis, execution, alerts, dashboard) communicate via Redis streams and persist state to PostgreSQL. All services share a common logging format, database client, and Redis client via a `shared/` library mounted as a Docker volume. Docker Compose orchestrates everything on a single VM.

**Tech Stack:** Python 3.11, PostgreSQL 15, Redis 7, Docker Compose v2, psycopg2-binary, redis-py, pytest, python-dotenv

---

## File Map

**Create:**
- `shared/__init__.py`
- `shared/logger.py` — structured JSON logger
- `shared/db.py` — PostgreSQL connection pool
- `shared/redis_client.py` — Redis client singleton
- `shared/tests/__init__.py`
- `shared/tests/test_logger.py`
- `shared/tests/test_db.py`
- `shared/tests/test_redis_client.py`
- `db/schema.sql` — all PostgreSQL table definitions
- `services/data/Dockerfile`
- `services/data/requirements.txt`
- `services/data/main.py` — placeholder
- `services/analysis/Dockerfile`
- `services/analysis/requirements.txt`
- `services/analysis/main.py` — placeholder
- `services/execution/Dockerfile`
- `services/execution/requirements.txt`
- `services/execution/main.py` — placeholder
- `services/alerts/Dockerfile`
- `services/alerts/requirements.txt`
- `services/alerts/main.py` — placeholder
- `services/dashboard/Dockerfile`
- `services/dashboard/requirements.txt`
- `services/dashboard/main.py` — placeholder
- `docker-compose.yml`
- `docker-compose.test.yml`

**Modify:**
- `.env.example` — add all required env vars
- `.gitignore` — add `__pycache__`, `.pytest_cache`
- `README.md` — update setup instructions

**Delete:**
- `bot/` — entire old scaffold
- `requirements.txt` — replaced by per-service requirements

---

## Task 1: Remove Old Scaffold

**Files:**
- Delete: `bot/`
- Delete: `requirements.txt`

- [ ] **Step 1: Remove old bot directory and root requirements**

```bash
rm -rf /Users/nickchow/claude/alphadivision/bot
rm /Users/nickchow/claude/alphadivision/requirements.txt
```

- [ ] **Step 2: Verify removed**

```bash
ls /Users/nickchow/claude/alphadivision/
```
Expected: No `bot/` directory, no `requirements.txt`

- [ ] **Step 3: Commit**

```bash
cd /Users/nickchow/claude/alphadivision
git add -A
git commit -m "chore: remove old single-script scaffold"
```

---

## Task 2: Create Directory Structure

**Files:**
- Create: `services/data/`, `services/analysis/`, `services/execution/`, `services/alerts/`, `services/dashboard/`
- Create: `shared/tests/`
- Create: `db/`
- Create: `tests/integration/`

- [ ] **Step 1: Create all directories**

```bash
mkdir -p /Users/nickchow/claude/alphadivision/services/data
mkdir -p /Users/nickchow/claude/alphadivision/services/analysis
mkdir -p /Users/nickchow/claude/alphadivision/services/execution
mkdir -p /Users/nickchow/claude/alphadivision/services/alerts
mkdir -p /Users/nickchow/claude/alphadivision/services/dashboard
mkdir -p /Users/nickchow/claude/alphadivision/shared/tests
mkdir -p /Users/nickchow/claude/alphadivision/db
mkdir -p /Users/nickchow/claude/alphadivision/tests/integration
```

- [ ] **Step 2: Add __init__.py files**

```bash
touch /Users/nickchow/claude/alphadivision/shared/__init__.py
touch /Users/nickchow/claude/alphadivision/shared/tests/__init__.py
touch /Users/nickchow/claude/alphadivision/tests/__init__.py
touch /Users/nickchow/claude/alphadivision/tests/integration/__init__.py
```

- [ ] **Step 3: Verify structure**

```bash
find /Users/nickchow/claude/alphadivision -not -path '*/.git/*' -not -path '*/.superpowers/*' -not -path '*/.DS_Store' | sort
```

Expected: All directories visible, no old `bot/`

- [ ] **Step 4: Commit**

```bash
cd /Users/nickchow/claude/alphadivision
git add -A
git commit -m "chore: create microservices directory structure"
```

---

## Task 3: PostgreSQL Schema

**Files:**
- Create: `db/schema.sql`

- [ ] **Step 1: Write schema**

Create `/Users/nickchow/claude/alphadivision/db/schema.sql`:

```sql
-- API health checks written by Data Service health checker
CREATE TABLE IF NOT EXISTS api_health (
    id SERIAL PRIMARY KEY,
    api_name VARCHAR(50) NOT NULL,
    status VARCHAR(10) NOT NULL,          -- 'ok', 'warning', 'error'
    checked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    latency_ms INTEGER,
    error_message TEXT
);

-- AI decisions from Analysis Service (all, including skipped)
CREATE TABLE IF NOT EXISTS decisions (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    decision VARCHAR(10) NOT NULL,        -- 'buy', 'sell', 'hold'
    confidence DECIMAL(4,3),
    reasoning TEXT,
    model VARCHAR(50),                    -- 'claude-haiku' or 'claude-sonnet'
    acted_on BOOLEAN DEFAULT FALSE,
    skip_reason TEXT,                     -- why decision was not acted on
    decided_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Trade signals published by Analysis Service to Redis, logged here
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    decision VARCHAR(10) NOT NULL,
    confidence DECIMAL(4,3),
    decision_id INTEGER REFERENCES decisions(id),
    published_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Orders placed by Execution Service
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(4) NOT NULL,             -- 'buy' or 'sell'
    qty INTEGER NOT NULL,
    price DECIMAL(10,4),
    alpaca_order_id VARCHAR(100),
    signal_id INTEGER REFERENCES signals(id),
    status VARCHAR(20) DEFAULT 'submitted', -- 'submitted', 'filled', 'failed'
    placed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    filled_at TIMESTAMP WITH TIME ZONE
);

-- Daily P&L tracked for circuit breaker
CREATE TABLE IF NOT EXISTS daily_pnl (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    realized_pnl DECIMAL(10,2) DEFAULT 0,
    circuit_breaker_triggered BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for common dashboard queries
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_placed_at ON trades(placed_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol);
CREATE INDEX IF NOT EXISTS idx_decisions_decided_at ON decisions(decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_health_checked_at ON api_health(checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_daily_pnl_date ON daily_pnl(date DESC);
```

- [ ] **Step 2: Verify SQL is valid by dry-running with psql if available**

```bash
psql --version 2>/dev/null && echo "psql available" || echo "psql not installed locally — will validate via Docker in Task 7"
```

- [ ] **Step 3: Commit**

```bash
cd /Users/nickchow/claude/alphadivision
git add db/schema.sql
git commit -m "feat: add PostgreSQL schema for all services"
```

---

## Task 4: Shared Logger

**Files:**
- Create: `shared/logger.py`
- Create: `shared/tests/test_logger.py`

- [ ] **Step 1: Write failing test**

Create `/Users/nickchow/claude/alphadivision/shared/tests/test_logger.py`:

```python
import json
import logging
import pytest
from unittest.mock import patch
from shared.logger import get_logger, JSONFormatter


def test_get_logger_returns_logger_with_service_name():
    logger = get_logger("test-service")
    assert logger.name == "test-service"


def test_get_logger_has_handler():
    logger = get_logger("test-service-2")
    assert len(logger.handlers) > 0


def test_json_formatter_outputs_valid_json():
    formatter = JSONFormatter("test-service")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["service"] == "test-service"
    assert "timestamp" in parsed


def test_json_formatter_includes_exception_info():
    formatter = JSONFormatter("test-service")
    try:
        raise ValueError("test error")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="something failed",
        args=(),
        exc_info=exc_info,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert "exception" in parsed
    assert "ValueError" in parsed["exception"]
```

- [ ] **Step 2: Install test dependencies locally and run — verify it fails**

```bash
cd /Users/nickchow/claude/alphadivision
pip install pytest 2>/dev/null | tail -1
PYTHONPATH=. pytest shared/tests/test_logger.py -v 2>&1 | tail -10
```

Expected: `ImportError` or `ModuleNotFoundError` — `shared.logger` doesn't exist yet

- [ ] **Step 3: Write implementation**

Create `/Users/nickchow/claude/alphadivision/shared/logger.py`:

```python
import json
import logging
import os
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service_name,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def get_logger(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter(service_name))
        logger.addHandler(handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    return logger
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/nickchow/claude/alphadivision
PYTHONPATH=. pytest shared/tests/test_logger.py -v
```

Expected:
```
test_get_logger_returns_logger_with_service_name PASSED
test_get_logger_has_handler PASSED
test_json_formatter_outputs_valid_json PASSED
test_json_formatter_includes_exception_info PASSED
4 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/nickchow/claude/alphadivision
git add shared/logger.py shared/tests/test_logger.py
git commit -m "feat: add shared JSON logger"
```

---

## Task 5: Shared Database Client

**Files:**
- Create: `shared/db.py`
- Create: `shared/tests/test_db.py`

- [ ] **Step 1: Write failing test**

Create `/Users/nickchow/claude/alphadivision/shared/tests/test_db.py`:

```python
import pytest
from unittest.mock import patch, MagicMock, call
from shared.db import get_pool, get_conn


def test_get_pool_creates_pool_with_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    mock_pool = MagicMock()

    with patch("shared.db.SimpleConnectionPool", return_value=mock_pool) as mock_cls:
        import shared.db as db_module
        db_module._pool = None  # reset singleton
        pool = get_pool()
        mock_cls.assert_called_once_with(
            minconn=1,
            maxconn=5,
            dsn="postgresql://user:pass@localhost/testdb",
        )
        assert pool == mock_pool
        db_module._pool = None  # cleanup


def test_get_pool_returns_existing_pool(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    mock_pool = MagicMock()

    with patch("shared.db.SimpleConnectionPool", return_value=mock_pool) as mock_cls:
        import shared.db as db_module
        db_module._pool = mock_pool
        pool = get_pool()
        mock_cls.assert_not_called()
        assert pool == mock_pool
        db_module._pool = None  # cleanup


def test_get_conn_commits_on_success(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_conn

    with patch("shared.db.get_pool", return_value=mock_pool):
        with get_conn() as conn:
            assert conn == mock_conn

    mock_conn.commit.assert_called_once()
    mock_pool.putconn.assert_called_once_with(mock_conn)


def test_get_conn_rolls_back_on_exception(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_conn

    with patch("shared.db.get_pool", return_value=mock_pool):
        with pytest.raises(ValueError):
            with get_conn() as conn:
                raise ValueError("db error")

    mock_conn.rollback.assert_called_once()
    mock_conn.commit.assert_not_called()
    mock_pool.putconn.assert_called_once_with(mock_conn)
```

- [ ] **Step 2: Run — verify it fails**

```bash
cd /Users/nickchow/claude/alphadivision
PYTHONPATH=. pytest shared/tests/test_db.py -v 2>&1 | tail -5
```

Expected: `ImportError` — `shared.db` doesn't exist yet

- [ ] **Step 3: Install psycopg2-binary**

```bash
pip install psycopg2-binary
```

- [ ] **Step 4: Write implementation**

Create `/Users/nickchow/claude/alphadivision/shared/db.py`:

```python
import os
from contextlib import contextmanager
from psycopg2.pool import SimpleConnectionPool

_pool: SimpleConnectionPool | None = None


def get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=os.getenv("DATABASE_URL"),
        )
    return _pool


@contextmanager
def get_conn():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
cd /Users/nickchow/claude/alphadivision
PYTHONPATH=. pytest shared/tests/test_db.py -v
```

Expected:
```
test_get_pool_creates_pool_with_dsn PASSED
test_get_pool_returns_existing_pool PASSED
test_get_conn_commits_on_success PASSED
test_get_conn_rolls_back_on_exception PASSED
4 passed
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nickchow/claude/alphadivision
git add shared/db.py shared/tests/test_db.py
git commit -m "feat: add shared PostgreSQL connection pool"
```

---

## Task 6: Shared Redis Client

**Files:**
- Create: `shared/redis_client.py`
- Create: `shared/tests/test_redis_client.py`

- [ ] **Step 1: Write failing test**

Create `/Users/nickchow/claude/alphadivision/shared/tests/test_redis_client.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from shared.redis_client import get_redis


def test_get_redis_creates_client_from_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    mock_client = MagicMock()

    with patch("shared.redis_client.redis.Redis.from_url", return_value=mock_client) as mock_from_url:
        import shared.redis_client as rc_module
        rc_module._client = None  # reset singleton
        client = get_redis()
        mock_from_url.assert_called_once_with(
            "redis://localhost:6379",
            decode_responses=True,
        )
        assert client == mock_client
        rc_module._client = None  # cleanup


def test_get_redis_returns_existing_client(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    mock_client = MagicMock()

    with patch("shared.redis_client.redis.Redis.from_url") as mock_from_url:
        import shared.redis_client as rc_module
        rc_module._client = mock_client
        client = get_redis()
        mock_from_url.assert_not_called()
        assert client == mock_client
        rc_module._client = None  # cleanup


def test_get_redis_uses_default_url_when_env_not_set(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    mock_client = MagicMock()

    with patch("shared.redis_client.redis.Redis.from_url", return_value=mock_client) as mock_from_url:
        import shared.redis_client as rc_module
        rc_module._client = None
        get_redis()
        mock_from_url.assert_called_once_with(
            "redis://localhost:6379",
            decode_responses=True,
        )
        rc_module._client = None  # cleanup
```

- [ ] **Step 2: Run — verify it fails**

```bash
cd /Users/nickchow/claude/alphadivision
PYTHONPATH=. pytest shared/tests/test_redis_client.py -v 2>&1 | tail -5
```

Expected: `ImportError` — `shared.redis_client` doesn't exist yet

- [ ] **Step 3: Install redis-py**

```bash
pip install redis
```

- [ ] **Step 4: Write implementation**

Create `/Users/nickchow/claude/alphadivision/shared/redis_client.py`:

```python
import os
import redis

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"),
            decode_responses=True,
        )
    return _client
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
cd /Users/nickchow/claude/alphadivision
PYTHONPATH=. pytest shared/tests/test_redis_client.py -v
```

Expected:
```
test_get_redis_creates_client_from_url PASSED
test_get_redis_returns_existing_client PASSED
test_get_redis_uses_default_url_when_env_not_set PASSED
3 passed
```

- [ ] **Step 6: Run all shared tests together**

```bash
cd /Users/nickchow/claude/alphadivision
PYTHONPATH=. pytest shared/tests/ -v
```

Expected: `11 passed`

- [ ] **Step 7: Commit**

```bash
cd /Users/nickchow/claude/alphadivision
git add shared/redis_client.py shared/tests/test_redis_client.py
git commit -m "feat: add shared Redis client singleton"
```

---

## Task 7: Docker Compose

**Files:**
- Create: `docker-compose.yml`
- Create: `docker-compose.test.yml`
- Modify: `.env.example`

- [ ] **Step 1: Write docker-compose.yml**

Create `/Users/nickchow/claude/alphadivision/docker-compose.yml`:

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    restart: always
    environment:
      POSTGRES_DB: alphadivision
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d alphadivision"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: always
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  data:
    build: ./services/data
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: data
    volumes:
      - ./shared:/app/shared
      - logs:/var/log/alphadivision

  analysis:
    build: ./services/analysis
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: analysis
    volumes:
      - ./shared:/app/shared
      - logs:/var/log/alphadivision

  execution:
    build: ./services/execution
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: execution
    volumes:
      - ./shared:/app/shared
      - logs:/var/log/alphadivision

  alerts:
    build: ./services/alerts
    restart: always
    depends_on:
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: alerts
    volumes:
      - ./shared:/app/shared
      - logs:/var/log/alphadivision

  dashboard:
    build: ./services/dashboard
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: dashboard
    ports:
      - "8080:8080"
    volumes:
      - ./shared:/app/shared
      - logs:/var/log/alphadivision

volumes:
  postgres_data:
  redis_data:
  logs:
```

- [ ] **Step 2: Write docker-compose.test.yml**

Create `/Users/nickchow/claude/alphadivision/docker-compose.test.yml`:

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: alphadivision_test
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
    volumes:
      - ./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U test -d alphadivision_test"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports:
      - "6380:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
```

- [ ] **Step 3: Update .env.example**

Replace `/Users/nickchow/claude/alphadivision/.env.example` with:

```bash
# PostgreSQL
DATABASE_URL=postgresql://alphadivision:yourpassword@postgres:5432/alphadivision
POSTGRES_USER=alphadivision
POSTGRES_PASSWORD=yourpassword

# Redis
REDIS_URL=redis://redis:6379

# Alpaca (https://alpaca.markets)
ALPACA_API_KEY=your_alpaca_api_key
ALPACA_SECRET_KEY=your_alpaca_secret_key
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Anthropic (https://console.anthropic.com)
ANTHROPIC_API_KEY=your_anthropic_api_key

# Finnhub (https://finnhub.io)
FINNHUB_API_KEY=your_finnhub_api_key

# FRED (https://fred.stlouisfed.org/docs/api/api_key.html)
FRED_API_KEY=your_fred_api_key

# Alerts
DISCORD_WEBHOOK_URL=your_discord_webhook_url
SENDGRID_API_KEY=your_sendgrid_api_key
ALERT_EMAIL_TO=your@email.com
ALERT_EMAIL_FROM=alerts@yourdomain.com

# Logging
LOG_LEVEL=INFO

# Watchlist (comma-separated)
WATCHLIST=AAPL,MSFT,GOOGL
```

- [ ] **Step 4: Validate Docker Compose config**

```bash
cd /Users/nickchow/claude/alphadivision
docker compose config --quiet && echo "docker-compose.yml is valid"
```

Expected: `docker-compose.yml is valid`

- [ ] **Step 5: Commit**

```bash
cd /Users/nickchow/claude/alphadivision
git add docker-compose.yml docker-compose.test.yml .env.example
git commit -m "feat: add Docker Compose and environment configuration"
```

---

## Task 8: Base Service Dockerfiles and Placeholders

**Files:**
- Create: `services/*/Dockerfile`
- Create: `services/*/requirements.txt`
- Create: `services/*/main.py`

Each service gets the same Dockerfile template. `main.py` is a placeholder that logs a startup message and sleeps — real logic is added in Plans 2–6.

- [ ] **Step 1: Write shared Dockerfile template**

Create `/Users/nickchow/claude/alphadivision/services/data/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared/ is mounted as a volume in docker-compose.yml
# so it does not need to be COPY'd here

COPY main.py .

CMD ["python", "main.py"]
```

Create `/Users/nickchow/claude/alphadivision/services/analysis/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
```

Create `/Users/nickchow/claude/alphadivision/services/execution/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
```

Create `/Users/nickchow/claude/alphadivision/services/alerts/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
```

Create `/Users/nickchow/claude/alphadivision/services/dashboard/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
```

- [ ] **Step 2: Write requirements.txt for each service**

Create `/Users/nickchow/claude/alphadivision/services/data/requirements.txt`:

```
psycopg2-binary==2.9.9
redis==5.0.4
python-dotenv==1.0.1
requests==2.31.0
pandas==2.2.2
pandas-ta==0.3.14b
alpaca-trade-api==3.3.2
```

Create `/Users/nickchow/claude/alphadivision/services/analysis/requirements.txt`:

```
psycopg2-binary==2.9.9
redis==5.0.4
python-dotenv==1.0.1
anthropic==0.25.0
```

Create `/Users/nickchow/claude/alphadivision/services/execution/requirements.txt`:

```
psycopg2-binary==2.9.9
redis==5.0.4
python-dotenv==1.0.1
alpaca-trade-api==3.3.2
```

Create `/Users/nickchow/claude/alphadivision/services/alerts/requirements.txt`:

```
redis==5.0.4
python-dotenv==1.0.1
requests==2.31.0
sendgrid==6.11.0
```

Create `/Users/nickchow/claude/alphadivision/services/dashboard/requirements.txt`:

```
psycopg2-binary==2.9.9
python-dotenv==1.0.1
flask==3.0.3
```

- [ ] **Step 3: Write placeholder main.py for each service**

Create `/Users/nickchow/claude/alphadivision/services/data/main.py`:

```python
import sys
import time
sys.path.insert(0, "/app")

from shared.logger import get_logger

log = get_logger("data")

def main():
    log.info("Data Service starting — placeholder")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
```

Create `/Users/nickchow/claude/alphadivision/services/analysis/main.py`:

```python
import sys
import time
sys.path.insert(0, "/app")

from shared.logger import get_logger

log = get_logger("analysis")

def main():
    log.info("Analysis Service starting — placeholder")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
```

Create `/Users/nickchow/claude/alphadivision/services/execution/main.py`:

```python
import sys
import time
sys.path.insert(0, "/app")

from shared.logger import get_logger

log = get_logger("execution")

def main():
    log.info("Execution Service starting — placeholder")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
```

Create `/Users/nickchow/claude/alphadivision/services/alerts/main.py`:

```python
import sys
import time
sys.path.insert(0, "/app")

from shared.logger import get_logger

log = get_logger("alerts")

def main():
    log.info("Alert Service starting — placeholder")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
```

Create `/Users/nickchow/claude/alphadivision/services/dashboard/main.py`:

```python
import sys
sys.path.insert(0, "/app")

from shared.logger import get_logger
from flask import Flask

log = get_logger("dashboard")
app = Flask(__name__)

@app.route("/health")
def health():
    return {"status": "ok"}, 200

@app.route("/")
def index():
    return "<h1>AlphaDivision Dashboard</h1><p>Coming soon.</p>"

if __name__ == "__main__":
    log.info("Dashboard Service starting — placeholder")
    app.run(host="0.0.0.0", port=8080)
```

- [ ] **Step 4: Commit**

```bash
cd /Users/nickchow/claude/alphadivision
git add services/
git commit -m "feat: add base Dockerfiles, requirements, and placeholder services"
```

---

## Task 9: Smoke Test — Bring the Stack Up

- [ ] **Step 1: Copy .env.example to .env and fill in PostgreSQL credentials (only)**

```bash
cd /Users/nickchow/claude/alphadivision
cp .env.example .env
```

Edit `.env` — set only these two values (others can stay as placeholders for now):
```
POSTGRES_USER=alphadivision
POSTGRES_PASSWORD=changeme
DATABASE_URL=postgresql://alphadivision:changeme@postgres:5432/alphadivision
```

- [ ] **Step 2: Build and start postgres + redis only**

```bash
cd /Users/nickchow/claude/alphadivision
docker compose up -d postgres redis
```

- [ ] **Step 3: Wait for healthy and verify schema was applied**

```bash
sleep 10
docker compose exec postgres psql -U alphadivision -d alphadivision -c "\dt"
```

Expected output — all tables listed:
```
           List of relations
 Schema |    Name    | Type  |    Owner
--------+------------+-------+--------------
 public | api_health | table | alphadivision
 public | daily_pnl  | table | alphadivision
 public | decisions  | table | alphadivision
 public | signals    | table | alphadivision
 public | trades     | table | alphadivision
```

- [ ] **Step 4: Verify Redis is up**

```bash
docker compose exec redis redis-cli ping
```

Expected: `PONG`

- [ ] **Step 5: Build and start all services**

```bash
cd /Users/nickchow/claude/alphadivision
docker compose up -d --build
```

- [ ] **Step 6: Check all containers are running**

```bash
docker compose ps
```

Expected: all 7 services (postgres, redis, data, analysis, execution, alerts, dashboard) show `running`

- [ ] **Step 7: Check dashboard health endpoint**

```bash
curl http://localhost:8080/health
```

Expected: `{"status": "ok"}`

- [ ] **Step 8: Check logs look correct (structured JSON)**

```bash
docker compose logs data | head -5
```

Expected: JSON line like `{"timestamp": "...", "service": "data", "level": "INFO", "message": "Data Service starting — placeholder"}`

- [ ] **Step 9: Stop stack**

```bash
docker compose down
```

- [ ] **Step 10: Commit**

```bash
cd /Users/nickchow/claude/alphadivision
git add -A
git commit -m "feat: foundation complete — all services boot, schema applied, health check passes"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Microservices structure with 5 services
- ✅ PostgreSQL schema covers all tables (trades, signals, decisions, api_health, daily_pnl)
- ✅ Redis with AOF persistence (`--appendonly yes`)
- ✅ Named Docker volumes (`postgres_data`, `redis_data`) — survive `down`, not `down -v`
- ✅ `restart: always` on all services
- ✅ Health checks on postgres and redis with `depends_on condition: service_healthy`
- ✅ Shared logger with structured JSON output matching spec format
- ✅ `LOG_LEVEL` env var supported
- ✅ Dashboard on port 8080
- ✅ `.env.example` includes all API keys from spec
- ✅ `docker-compose.test.yml` for integration tests (Plans 2–6 will add tests)
- ✅ `WATCHLIST` env var for configurable symbol list

**Not in this plan (deferred to Plans 2–6):**
- Service business logic
- Health check probes
- Heartbeat publishing
- `/health` endpoints beyond dashboard placeholder
- Integration tests (infrastructure is ready, tests need real service logic)
