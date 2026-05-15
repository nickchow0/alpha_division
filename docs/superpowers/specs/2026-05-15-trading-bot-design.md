# AlphaDivision Trading Bot — Design Spec
**Date:** 2026-05-15
**Status:** Approved

---

## Overview

AlphaDivision is a swing trading bot for US stocks. It uses a hybrid approach: technical indicators filter candidate symbols, and Claude AI makes the final buy/sell/hold decision. The system is built as a microservices architecture running on Oracle Cloud's free ARM tier, accessible remotely via Tailscale.

---

## 1. Architecture

### Decision: Microservices + Redis Message Bus

Five independent services communicate through a Redis message bus. Each service runs in its own Docker container and can be restarted, updated, or debugged independently.

**Services:**

| Service | Responsibility |
|---|---|
| Data Service | Polls Alpaca, Finnhub, FRED on a schedule. Publishes market snapshots to Redis |
| Analysis Service | Consumes snapshots, runs technical filters, calls Claude AI, publishes signals |
| Execution Service | Consumes signals, applies risk rules, places orders via Alpaca |
| Alert Service | Listens for trade events and errors, sends Discord and email notifications |
| Dashboard Service | Flask web app showing positions, P&L, AI decisions, trade history |

**Shared infrastructure:**
- **Redis** — message bus between all services
- **PostgreSQL** — persistent store for trades, signals, decisions, P&L

### Alternatives Considered

**A — Single Script**
One Python file handling everything. Fastest to build, easiest to debug. Rejected because it can't support a proper dashboard, is hard to extend, and becomes fragile as complexity grows. Good for prototyping, wrong for learning distributed systems.

**B — Modular Pipeline + SQLite**
Separate modules in one codebase sharing a SQLite database. Cleaner than a single script, easier than microservices. Rejected in favour of Option C because the user's explicit goal is to learn how complex distributed systems are built. SQLite was also considered as a PostgreSQL replacement to reduce memory usage — see Infrastructure section.

**C — Microservices + Redis (chosen)**
Maximum complexity, maximum learning value. Each service has a single clear purpose, communicates through well-defined interfaces, and can be understood and tested independently.

---

## 2. Data Pipeline

### Schedule
- **Price & indicators** — every 15 minutes during NYSE market hours (9:30am–4pm ET, weekdays)
- **News** — every 1 hour
- **Macro** — once per day

### Sources
- **Alpaca** — OHLCV bars for watchlist symbols
- **Finnhub** (free tier) — 5 most recent headlines per symbol
- **FRED** — Fed funds rate and CPI

### Indicators
Calculated locally from Alpaca bar data using `pandas-ta`:
- RSI (14)
- SMA (20)
- SMA (50)

### Watchlist
Defined in a config file. Symbols can be added or removed without restarting the service. Default starting list: AAPL, MSFT, GOOGL.

### Reliability
Failed fetches log an error and retry on the next cycle. The bot continues running — a single data source failure does not halt the system.

### Alternatives Considered

**Polygon.io (~$30/mo)** — more reliable, faster WebSocket streaming, better data quality. Rejected for now because the free tier sources (Alpaca + Finnhub + FRED) are sufficient for swing trading at hourly/15-minute resolution. Can be swapped in later if data quality becomes a bottleneck.

**Yahoo Finance (yfinance)** — free and unlimited but uses an unofficial API that can break without notice. Unsuitable for a live trading system.

**WebSocket streaming** — eliminates polling latency. Overkill for swing trading where 15-minute resolution is sufficient. Worth revisiting if the strategy moves toward intraday.

---

## 3. Analysis

### Two-Stage Hybrid Approach

**Stage 1 — Technical filter**
Fast, cheap, no AI involved. Only symbols passing all three rules proceed to Stage 2:
- RSI between 30–70 (avoid overbought/oversold extremes for swing entries)
- Price above SMA 50 (uptrend confirmation)
- Price crossed SMA 20 in the last 3 bars (momentum trigger)

**Stage 2 — Claude AI decision**
A prompt is built from price data, indicators, recent news, and macro context and sent to Claude. Response is structured JSON:
```json
{"decision": "buy", "confidence": 0.78, "reasoning": "..."}
```

Two models are used depending on complexity:
- **Claude Haiku** — standard daily analysis (~$0.001/call, fast)
- **Claude Sonnet** — triggered when news sentiment conflicts with technical signals, or when an open position is down more than 5% (higher-stakes decision warrants better reasoning)

Decisions below a confidence threshold of 0.65 are logged but not acted on. All decisions — including skipped ones — are written to PostgreSQL.

### Alternatives Considered

**Technical signals only** — no AI involved, fully deterministic. Cheaper and faster but misses news and macro context that affects swing trades significantly. Rejected because incorporating AI reasoning was a stated goal.

**AI-only (no technical filter)** — every symbol goes to Claude on every cycle. Much higher API costs at scale (potentially $100s/month at frequent intervals) and weaker signal quality because the AI has no pre-filtering. Rejected in favour of the hybrid approach.

**Local AI model (Llama, Mistral)** — no API costs, full privacy, runs on-device. Rejected because:
- 7B–13B parameter models lag behind Claude on multi-step financial reasoning
- The Oracle VM's ARM CPU would make inference slow
- A GPU capable of running a quality local model costs $300–1500+
- Claude Haiku costs ~$0.001/call — negligible for swing trading frequency

**GPT-4o** — comparable reasoning quality to Claude Sonnet. Slightly higher cost. Rejected simply because Claude was the preferred choice, not a technical limitation.

---

## 4. Execution

### Risk Rules

**Layer 1 — Position checks**
- Do not buy a symbol already held (prevents accidental position doubling across analysis cycles)
- Do not sell a symbol not held
- Maximum 5 open positions at once (concentration risk — keeps dry powder available and positions manageable)

**Layer 2 — Position sizing**
- Risk no more than 2% of portfolio per trade
- Formula: `floor((portfolio_value × 0.02) / entry_price)` shares
- The 2% rule means 35+ consecutive full losses before losing half the account — gives enough runway to identify strategy problems before serious damage

**Layer 3 — Daily circuit breaker**
- Daily P&L tracked in PostgreSQL
- If losses exceed $200 in a single day, halt all new orders and trigger an alert
- Resets at market open the next trading day

### Order Handling
- Market orders used for simplicity
- No orders placed in the first 30 minutes after market open (9:30–10:00am ET) due to elevated volatility
- On service restart, reconciles against Alpaca's actual positions to prevent duplicate orders

### Paper Trading
`ALPACA_BASE_URL` is set via environment variable. Switching from paper to live trading requires only a config change — no code changes.

### Alternatives Considered

**Limit orders** — more control over fill price, reduces slippage. Added complexity for order management (handling partial fills, cancellations, timeouts). Deferred as a future improvement once the core system is stable.

**Stop-loss orders** — automatic downside protection at the broker level. Not implemented in V1 because the Analysis Service monitors positions on each cycle and can issue sell signals. Can be added as an additional safety layer later.

---

## 5. Alerts & Dashboard

### Alert Service
Listens to Redis for three event types:

| Event | Channel |
|---|---|
| Trade placed | Discord webhook |
| Circuit breaker triggered | Discord + Email |
| Service error/crash | Discord + Email |

- **Discord** — free, instant, no infrastructure needed beyond a webhook URL
- **Email** — SendGrid free tier (100 emails/day). Used for high-priority events that need attention away from Discord

### Dashboard Service
Flask web app on port 8080, reading from PostgreSQL. Four pages:

- **Overview** — current positions, total P&L, daily P&L, available cash
- **Trades** — full order history with entry/exit prices and return per trade
- **Decisions** — every AI analysis including skipped ones, with Claude's reasoning
- **Watchlist** — current indicator values for all tracked symbols

### Remote Access
Dashboard is not exposed to the public internet. Accessible securely from iPhone and iPad via **Tailscale** — the Oracle VM joins the user's existing Tailscale network as an additional device (within the free plan's 100-device limit, no additional cost).

### Alternatives Considered

**Public URL with basic auth + HTTPS** — accessible from any browser without installing Tailscale. Adds attack surface since the dashboard exposes trading positions and P&L. Rejected in favour of Tailscale for a financial dashboard.

**Telegram bot** — alternative to Discord for alerts. Both are free. Discord chosen because the user is likely already familiar with it.

**Email-only alerts** — simpler, no Discord setup. Rejected because email is too slow for trade notifications where you may want to act quickly.

---

## 6. Infrastructure

### Hosting: Oracle Cloud Free Tier (ARM — Ampere A1)

| Spec | Value |
|---|---|
| CPU | 4 ARM cores |
| RAM | 24 GB |
| Storage | 200 GB |
| Bandwidth | 10 TB/month |
| Cost | $0 permanently |

### Orchestration: Docker Compose

All services run as Docker containers on a single VM. Docker Compose manages networking, environment variables, restart policies, and volume mounts.

### Project Structure
```
alphadivision/
├── services/
│   ├── data/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── main.py
│   ├── analysis/
│   ├── execution/
│   ├── alerts/
│   └── dashboard/
├── docker-compose.yml
├── .env.example
└── docs/
```

### Networking
- All inter-service communication is internal to Docker's network
- Only the dashboard port (8080) is accessible, and only via Tailscale
- Oracle Cloud firewall blocks all public inbound traffic

### Persistence
- PostgreSQL data volume mounted to VM disk — survives container restarts
- Redis configured with AOF (Append Only File) persistence — message queue survives restarts

### Alternatives Considered

**Google Cloud e2-micro (free tier)** — 1GB RAM. Rejected for this architecture because the full stack (5 services + Redis + PostgreSQL + OS overhead) requires approximately 930MB, leaving no headroom for spikes. Would require compromises: replacing PostgreSQL with SQLite (~150MB saving) and merging Alert + Dashboard services (~80MB saving). These compromises reduce the learning value and architectural cleanliness.

**Google Cloud e2-small (~$13/mo)** — 2GB RAM. Sufficient for the stack with comfortable headroom. Rejected only because Oracle Cloud offers 24GB for free — strictly better for this use case.

**AWS t2.micro (free tier, 12 months only)** — 1GB RAM, same constraints as GCP e2-micro, and billing starts after 12 months. Rejected on both RAM and cost grounds.

**Railway / Render / Fly.io** — PaaS platforms with free tiers. All have RAM limits of 256–512MB per service, which is insufficient for even a single service in this stack. Services also sleep on inactivity, which is incompatible with a 24/7 trading bot.

**Kubernetes** — industry standard for microservices orchestration at scale. Rejected in favour of Docker Compose because Kubernetes adds significant operational complexity (control plane, YAML verbosity, networking concepts) that isn't warranted for 5 services on a single VM. Docker Compose is a better learning stepping stone and can be migrated to Kubernetes later if needed.

**SQLite instead of PostgreSQL** — would save ~150MB RAM and eliminate one service. Rejected because PostgreSQL is the right tool for concurrent writes from multiple services, offers better query capabilities for the dashboard, and is more representative of production systems. Learning to run PostgreSQL in Docker is also valuable.

**Self-hosted Redis alternatives (KeyDB, Valkey)** — drop-in Redis replacements with better multi-threading on ARM. Not worth the added unfamiliarity at this stage. Can be swapped in later without changing application code.

---

## API Keys Required

| Service | Key | Free Tier |
|---|---|---|
| Alpaca | API key + secret | Yes (paper trading) |
| Anthropic | API key | No — pay per token |
| Finnhub | API key | Yes (60 calls/min) |
| FRED | API key | Yes (unlimited) |
| SendGrid | API key | Yes (100 emails/day) |
| Tailscale | Auth key | Yes (100 devices) |

---

## What's Out of Scope (V1)

- Backtesting engine — strategy will be validated on paper trading first
- Options or crypto — US equities only
- Short selling — long positions only
- Limit orders — market orders only
- Multiple brokers — Alpaca only
- Authentication on the dashboard — Tailscale provides network-level access control
