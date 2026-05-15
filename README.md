# AlphaDivision

A swing trading bot for US stocks, powered by Claude AI. Uses a hybrid approach — technical indicators filter candidates, Claude makes the final decision. Built as a microservices architecture on Oracle Cloud's free ARM tier.

---

## Architecture

```mermaid
graph TD
    subgraph External["External APIs"]
        A[Alpaca API]
        F[Finnhub API]
        FR[FRED API]
        AN[Anthropic API]
    end

    subgraph Services["Docker Containers"]
        DS[Data Service]
        AS[Analysis Service]
        ES[Execution Service]
        AL[Alert Service]
        DB2[Dashboard Service]
    end

    subgraph Infra["Infrastructure"]
        R[(Redis)]
        PG[(PostgreSQL)]
    end

    subgraph Notify["Notifications"]
        DC[Discord]
        EM[Email]
    end

    A -->|price + bars| DS
    F -->|news headlines| DS
    FR -->|macro data| DS

    DS -->|market snapshot| R
    R -->|market snapshot| AS
    AN -->|buy/sell/hold| AS
    AS -->|trade signal| R
    R -->|trade signal| ES
    ES -->|place order| A
    ES -->|trade event| R
    R -->|trade event| AL
    AL --> DC
    AL --> EM

    ES -->|write| PG
    AS -->|write| PG
    DB2 -->|read| PG
```

---

## Data Flow

```mermaid
sequenceDiagram
    participant D as Data Service
    participant R as Redis
    participant A as Analysis Service
    participant C as Claude AI
    participant E as Execution Service
    participant AL as Alert Service

    loop Every 15 min (market hours)
        D->>D: Fetch price, news, macro
        D->>D: Calculate RSI, SMA20, SMA50
        D->>R: Publish market snapshot
    end

    R->>A: Consume snapshot
    A->>A: Apply technical filter (RSI, SMA)

    alt Symbol passes filter
        A->>C: Send prompt with data
        C->>A: Return decision + confidence
        A->>R: Publish trade signal
        R->>E: Consume signal

        alt Confidence >= 0.65
            E->>E: Apply risk rules
            E->>AL: Place order via Alpaca
            AL->>AL: Send Discord + email alert
        else Confidence too low
            E->>E: Log and skip
        end
    else Symbol filtered out
        A->>A: Log and skip
    end
```

---

## Risk Rules

```mermaid
flowchart TD
    S[Trade Signal Received] --> P1{Already holding\nthis symbol?}
    P1 -->|Yes| SKIP1[Skip — no double positions]
    P1 -->|No| P2{Max 5 positions\nreached?}
    P2 -->|Yes| SKIP2[Skip — at capacity]
    P2 -->|No| P3{Daily loss\n> $200?}
    P3 -->|Yes| HALT[Halt — circuit breaker triggered]
    P3 -->|No| SIZE[Calculate position size\n2% of portfolio]
    SIZE --> ORDER[Place market order]
    ORDER --> LOG[Write to PostgreSQL\nAlert via Discord]
```

---

## Service Health

```mermaid
flowchart LR
    W[Watchdog\nevery 2 min] --> HR{Heartbeat\nreceived?}
    HR -->|Yes| OK[✅ Healthy]
    HR -->|No - 1st miss| RT[Restart container]
    RT --> HR2{Heartbeat\nreceived?}
    HR2 -->|Yes| OK
    HR2 -->|No - 3rd miss| CRIT[🔴 CRITICAL ALERT\nManual intervention needed]
```

---

## Project Structure

```
alphadivision/
├── services/
│   ├── data/               # Fetches price, news, macro — publishes to Redis
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py
│   │   └── tests/
│   ├── analysis/           # Technical filter + Claude AI decisions
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py
│   │   └── tests/
│   ├── execution/          # Risk rules + order placement via Alpaca
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py
│   │   └── tests/
│   ├── alerts/             # Discord + email notifications
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py
│   │   └── tests/
│   └── dashboard/          # Flask web UI — responsive, mobile friendly
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── main.py
│       └── tests/
├── tests/
│   └── integration/        # Cross-service integration tests
├── docker-compose.yml
├── docker-compose.test.yml
├── .env.example
├── CLAUDE.md
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-05-15-trading-bot-design.md
```

---

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/nickchow0/alphadivision.git
cd alphadivision
cp .env.example .env
# Fill in your API keys in .env
```

### 2. API keys required

| Service | Where to get it | Free tier |
|---|---|---|
| Alpaca | alpaca.markets | Yes (paper trading) |
| Anthropic | console.anthropic.com | Pay per token |
| Finnhub | finnhub.io | Yes (60 calls/min) |
| FRED | fred.stlouisfed.org | Yes (unlimited) |
| SendGrid | sendgrid.com | Yes (100 emails/day) |
| Tailscale | tailscale.com | Yes (100 devices) |

### 3. Run (paper trading)

```bash
docker-compose up -d
```

### 4. Run tests

```bash
# Unit tests
pytest services/

# Integration tests
docker-compose -f docker-compose.test.yml up -d
pytest tests/integration/
docker-compose -f docker-compose.test.yml down
```

### 5. Access the dashboard

Connect via Tailscale, then open `http://<vm-tailscale-ip>:8080` on any device.

---

## Deployment (Oracle Cloud)

```mermaid
flowchart LR
    DEV[Local dev] -->|git push| GH[GitHub]
    GH -->|git pull| VM[Oracle Cloud ARM VM]
    VM -->|docker-compose build| BUILD[Rebuild changed image]
    BUILD -->|docker-compose up -d --no-deps service| DEPLOY[Restart service only]
    DEPLOY --> LIVE[Bot running 24/7]
```

> ⚠️ Never run `docker-compose down -v` in production — this deletes all data volumes.

---

## Design Spec

Full architecture decisions, alternatives considered, failure modes, and recovery procedures:
[`docs/superpowers/specs/2026-05-15-trading-bot-design.md`](docs/superpowers/specs/2026-05-15-trading-bot-design.md)
