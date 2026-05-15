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

| Option | Description | Pros | Cons | Decision |
|---|---|---|---|---|
| A — Single Script | One Python file handles everything | Fast to build, easy to debug, no moving parts | Hard to extend, no real dashboard, fragile as complexity grows | Rejected |
| B — Modular Pipeline + SQLite | Separate modules sharing a SQLite database | Cleaner than single script, easier than microservices | Limited learning value for distributed systems | Rejected |
| **C — Microservices + Redis** | 5 independent services via Redis message bus | Max learning value, clean separation, each service independently testable | More upfront complexity | **Chosen** |

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

| Option | Cost | Reliability | Latency | Decision |
|---|---|---|---|---|
| **Alpaca + Finnhub + FRED** | Free | Good | 15 min polling | **Chosen** — sufficient for swing trading |
| Polygon.io | ~$30/mo | Better, official WebSocket | Real-time streaming | Rejected — overkill at this stage, can upgrade later |
| Yahoo Finance (yfinance) | Free | Poor — unofficial API, breaks without notice | Poll-based | Rejected — unsuitable for a live trading system |
| WebSocket streaming | Varies | N/A | Real-time | Rejected — overkill for 15-min swing trading resolution |

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

| Option | API Cost | Decision Quality | Speed | Decision |
|---|---|---|---|---|
| **Hybrid: Tech filter + Claude** | Low (~$0.001/call) | Best of both — fast filter, smart final call | Fast | **Chosen** |
| Technical signals only | Free | Misses news and macro context | Fastest | Rejected — AI reasoning was a stated goal |
| AI-only (no technical filter) | High ($100s/mo at scale) | Good but no pre-filtering wastes calls | Slower | Rejected — costly and lower signal quality |
| Local AI (Llama/Mistral 7B–13B) | Hardware only | Weaker multi-step financial reasoning | Slow on ARM CPU | Rejected — quality gap not worth hardware cost |
| GPT-4o | Slightly higher than Claude | Comparable to Claude Sonnet | Similar | Rejected — preference for Claude, not a technical limitation |

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

| Option | Price Control | Complexity | In V1? | Decision |
|---|---|---|---|---|
| **Market orders** | None — fills at market price | Simple, no edge cases | Yes | **Chosen** |
| Limit orders | High — set exact fill price | Complex: partial fills, cancellations, timeouts | No | Deferred — good future improvement once core is stable |
| Broker-level stop-loss orders | High — automatic downside protection | Moderate | No | Deferred — Analysis Service handles exits via sell signals for now |

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

**Remote access:**

| Option | Security | Setup | Works on iPhone/iPad | Decision |
|---|---|---|---|---|
| **Tailscale** | Best — private network, not public-facing | Tailscale app on each device | Yes | **Chosen** |
| Public URL + basic auth + HTTPS | Moderate — public-facing, password protected | Nginx + Let's Encrypt | Yes, any browser | Rejected — financial dashboard shouldn't be public-facing |

**Alert channel:**

| Option | Speed | Cost | Setup | Decision |
|---|---|---|---|---|
| **Discord webhook** | Instant | Free | Webhook URL only | **Chosen** |
| Telegram bot | Instant | Free | Bot registration required | Rejected — preference for Discord |
| Email-only | Slow (minutes) | Free (SendGrid) | None | Rejected — too slow for trade notifications |

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

**Hosting:**

| Option | RAM | Cost | 24/7? | Decision |
|---|---|---|---|---|
| **Oracle Cloud ARM (Ampere A1)** | 24 GB | $0 forever | Yes | **Chosen** |
| Google Cloud e2-micro | 1 GB | $0 forever | Yes | Rejected — full stack needs ~930MB, no headroom for spikes |
| Google Cloud e2-small | 2 GB | ~$13/mo | Yes | Rejected — Oracle gives 24GB for free |
| AWS t2.micro | 1 GB | $0 for 12 months only | Yes | Rejected — too little RAM and billing starts after year 1 |
| Railway / Render / Fly.io | 256–512 MB per service | Free (limited hours) | No — services sleep | Rejected — incompatible with 24/7 trading bot |

**Orchestration:**

| Option | Complexity | Learning Value | Right for 1 VM? | Decision |
|---|---|---|---|---|
| **Docker Compose** | Low | Good stepping stone to Kubernetes | Yes | **Chosen** |
| Kubernetes | High | Production-grade, complex | Overkill for 5 services | Rejected — too much overhead for a single VM |

**Database:**

| Option | RAM Usage | Concurrent Writes | Learning Value | Decision |
|---|---|---|---|---|
| **PostgreSQL** | ~150 MB | Excellent | High — production standard | **Chosen** |
| SQLite | ~20 MB | Poor — single writer only | Low | Rejected — wrong tool for multi-service concurrent writes |
| KeyDB / Valkey | Similar to Redis | Better ARM multi-threading | Unfamiliar overhead | Rejected — can swap in later without code changes |

---

## 7. Testing

### Philosophy
Each service is tested in isolation first, then as part of the full system. Real API calls are never made during tests — all external dependencies are mocked.

### Unit Tests
Each service has its own test suite covering core logic:

| Service | What's tested |
|---|---|
| Data Service | Correct parsing of API responses, indicator calculations, market hours logic |
| Analysis Service | Technical filter rules (RSI/SMA thresholds), prompt construction, JSON response parsing |
| Execution Service | Risk rule enforcement (position limits, sizing formula, circuit breaker), order deduplication on restart |
| Alert Service | Correct event routing (which events go to Discord vs email), message formatting |
| Dashboard Service | Database queries return correct data, pages render without errors |

All external API calls (Alpaca, Finnhub, FRED, Claude, SendGrid, Discord) are mocked using `pytest` with `unittest.mock`. Tests run without any API keys or network access.

### Integration Tests
Tests that verify services work together correctly via Redis and PostgreSQL:

- Data Service publishes a snapshot → Analysis Service receives and processes it
- Analysis Service publishes a signal → Execution Service receives and applies risk rules
- Execution Service places an order → Alert Service fires the correct notification
- Failed order → circuit breaker triggers → all services halt new trades

Integration tests run against real Redis and PostgreSQL instances spun up locally via Docker Compose using a separate `docker-compose.test.yml`.

### Paper Trading as System Test
Before switching to live trading, the full system runs in paper trading mode (Alpaca paper URL) for a minimum of **2 weeks**. This validates:
- End-to-end data flow under real market conditions
- AI decisions are consistent and reasonable
- Risk rules fire correctly
- Alerts arrive as expected
- Dashboard reflects accurate positions and P&L

### Running Tests
```bash
# Unit tests only (no Docker required)
pytest services/data/tests/
pytest services/analysis/tests/
pytest services/execution/tests/

# All unit tests
pytest services/

# Integration tests (requires Docker)
docker-compose -f docker-compose.test.yml up -d
pytest tests/integration/
docker-compose -f docker-compose.test.yml down
```

### What Is Not Tested
- Backtesting — strategy performance is validated via paper trading, not a backtesting engine (out of scope for V1)
- Live API response formats — monitored via health checks (see Section 8)

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
