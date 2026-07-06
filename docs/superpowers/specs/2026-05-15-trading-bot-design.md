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

### Known Limitation: AI Self-Reported Confidence

In V1, the confidence score is entirely self-reported by Claude as part of its JSON response. This is a pragmatic starting point but has three weaknesses:

| Weakness | Implication |
|---|---|
| **Not calibrated** | A confidence of 0.78 does not mean the trade will be right 78% of the time |
| **Inconsistent** | The same market data on different days may produce different confidence scores |
| **Not backtestable** | Cannot verify historically whether high-confidence calls outperformed low-confidence ones |

### Future Improvement: Calculated Confidence

A more robust approach — to be implemented after sufficient paper trading data is collected — calculates confidence independently and uses Claude's score as one input among several:

```
Confidence = weighted combination of:
  - Technical signal strength    (RSI distance from midpoint, SMA spread)
  - News sentiment score         (dedicated NLP scoring, not Claude's interpretation)
  - Claude's stated confidence   (one input, not the sole arbiter)
  - Historical accuracy          (did past setups with similar parameters succeed?)
```

This makes confidence **measurable, auditable, and improvable over time**. The threshold (currently 0.65) can then be tuned based on real performance data rather than intuition.

**Prerequisite:** at least 2–3 months of paper trading data in PostgreSQL before this is worth implementing.

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

### Mobile Design
The dashboard is fully responsive — one codebase, one Flask app, CSS media queries handle layout adaptation for desktop, iPad, and iPhone. No separate mobile app or route.

On mobile the layout prioritises:
- P&L summary banner at the top
- 2-column stat grid (positions count, available cash)
- Stacked position list with colour-coded returns
- Bottom tab bar for navigation (Trades, Decisions, Watchlist)

### Remote Access
Dashboard is not exposed to the public internet. Accessible securely from iPhone and iPad via **Tailscale** — the Oracle VM joins the user's existing Tailscale network as an additional device (within the free plan's 100-device limit, no additional cost).

### Alternatives Considered

**Mobile dashboard:**

| Option | Maintenance | Mobile UX | Extra Work | Decision |
|---|---|---|---|---|
| **Responsive single dashboard** | One codebase | Good — adapts via CSS media queries | Minimal | **Chosen** |
| Separate mobile app/route | Two codebases, risk of drift | Best | Double the work | Rejected |
| Discord alerts only | None | None — no dashboard on mobile | None | Rejected — no way to check positions on the go |

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

### Deployment & Redis Persistence

**Does a `git pull` wipe Redis?**
No — `git pull` only updates code files on disk. Redis runs as a separate Docker container with its own memory and AOF persistence file. Pulling new code has zero effect on Redis state.

**What about `docker-compose up --build`?**
This is where care is needed. Rebuilding and restarting containers does briefly interrupt services, but Redis data is safe because:
- The AOF file is stored in a **named Docker volume** (`redis_data`), not inside the container image
- Named volumes persist across container rebuilds and restarts — they are only deleted if you explicitly run `docker-compose down -v` (the `-v` flag is the danger)

**Safe deployment workflow:**
```bash
git pull origin main                        # pull new code — Redis unaffected
docker-compose build                        # rebuild changed images — Redis unaffected
docker-compose up -d --no-deps <service>    # restart only the changed service
```

Restarting one service at a time means Redis keeps running throughout. The full stack never goes down simultaneously.

**What if Redis does restart?**
With AOF persistence enabled, Redis replays its log on startup and restores all keys within seconds. The only data at risk is messages written in the last second before shutdown — for a swing trading bot running on 15-minute cycles, this is negligible.

**Never run this in production:**
```bash
docker-compose down -v   # ⚠️ deletes ALL volumes including Redis AOF and PostgreSQL data
```

This command is only safe in development to reset a clean state.

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

## 8. API Health Checks

### Problem
The bot depends on four external APIs (Alpaca, Finnhub, FRED, Anthropic). If any go down silently — returning errors or malformed data without the bot noticing — trades could be missed, skipped, or made on stale data. Silent failures are the most dangerous kind.

### Health Check Service
A lightweight health checker runs as part of the Data Service on a separate thread, probing each API every 5 minutes with a minimal test request. Results are written to PostgreSQL and surfaced in the dashboard.

| API | Health Check Method | Failure Action |
|---|---|---|
| Alpaca (price data) | Fetch latest bar for a single symbol (AAPL) | Log error, skip cycle, alert Discord |
| Alpaca (order execution) | Call account endpoint, verify status is `ACTIVE` | Log error, halt new orders, alert Discord + email |
| Finnhub (news) | Fetch one headline for a single symbol | Log warning, skip news this cycle, continue with price data only |
| FRED (macro) | Fetch latest Fed funds rate observation | Log warning, use last known macro values, continue |
| Anthropic (Claude) | Send a minimal test prompt, verify response | Log error, skip AI analysis this cycle, alert Discord |

### Failure Behaviour by Severity

| Severity | Condition | Action |
|---|---|---|
| 🔴 Critical | Alpaca order API down | Halt all new orders immediately, alert Discord + email |
| 🔴 Critical | Anthropic API down | Skip analysis cycle, alert Discord |
| 🟡 Warning | Alpaca price data down | Skip data cycle, retry next interval, alert Discord |
| 🟡 Warning | Finnhub down | Continue without news, use technical signals only |
| 🟢 Info | FRED down | Use last known macro values, log info |

### Stale Data Detection
Beyond simple up/down checks, the Data Service validates that data is fresh before publishing to Redis:
- Price bars must have a timestamp within the last 20 minutes during market hours
- News articles older than 24 hours are excluded from the prompt
- Macro data older than 7 days triggers a warning

### Dashboard Visibility
The dashboard Overview page shows a live API status panel — green/yellow/red for each dependency — so problems are visible at a glance without needing to check logs.

---

## 9. Microservice Monitoring & Recovery

### How Docker Compose Handles Crashes
All services are configured with `restart: always` in `docker-compose.yml`. If a container crashes, Docker automatically restarts it — no manual intervention needed for transient failures (e.g. a one-off network error or OOM spike).

```yaml
services:
  data:
    restart: always
  analysis:
    restart: always
  execution:
    restart: always
```

### Detecting a Down Service
Docker's built-in restart handles crashes, but a service can be running yet stuck — deadlocked, spinning, or silently failing. Each service therefore:

- Publishes a **heartbeat** to Redis every 60 seconds (`heartbeat:<service-name>` key with a 90-second TTL)
- Exposes a `/health` HTTP endpoint returning `200 OK` when healthy

A dedicated **watchdog** (lightweight script running on the VM outside Docker) checks heartbeats every 2 minutes:

| Condition | Action |
|---|---|
| Heartbeat missing for >90s | Alert Discord + email, attempt `docker-compose restart <service>` |
| `/health` returns non-200 | Alert Discord, log details |
| Service restart fails 3 times | Alert Discord + email with `CRITICAL` flag — requires manual intervention |

### Per-Service Recovery Behaviour

| Service | If it goes down | Impact | Auto-recovers? |
|---|---|---|---|
| Data Service | No new market snapshots published | Analysis pauses, no new signals | ✅ Yes — restarts, resumes polling |
| Analysis Service | No new trade signals generated | No new orders placed | ✅ Yes — restarts, picks up next data snapshot |
| Execution Service | Signals queue up in Redis | Orders delayed until recovery | ✅ Yes — restarts, reconciles positions before placing new orders |
| Alert Service | No Discord/email notifications | Trades still execute, silently | ✅ Yes — restarts, resumes listening |
| Dashboard Service | UI unavailable | No visibility, bot still runs | ✅ Yes — restarts, reads fresh data from PostgreSQL |
| Redis | All inter-service messaging stops | Full trading halt | ✅ Yes — AOF persistence means no message loss on restart |
| PostgreSQL | No writes possible | Execution Service halts new orders | ✅ Yes — data volume persists, resumes on restart |

### Logging
All services use structured logging with consistent fields:

```json
{"timestamp": "2026-05-15T09:32:01Z", "service": "execution", "level": "INFO", "message": "Order placed", "symbol": "AAPL", "qty": 5, "side": "buy"}
```

Logs are written to stdout (captured by Docker) and to a mounted log volume on the VM at `/var/log/alphadivision/`. Log rotation is configured to keep 7 days of history before purging.

### Dashboard Visibility
The dashboard Overview page shows:
- Live status (green/yellow/red) for each service based on heartbeat age
- Last heartbeat timestamp per service
- Recent error log entries across all services

---

## 10. VM Failure & Database Recovery

### Scenario 1 — VM Reboots (Planned or Unexpected)
Oracle Cloud VMs occasionally reboot for maintenance or after a power event. This is the most common failure scenario.

**What happens:**
- All Docker containers stop
- Redis AOF file and PostgreSQL data volume remain intact on disk
- On reboot, Docker starts automatically (configured via `systemctl enable docker`)
- All containers restart via `restart: always` policy
- Redis replays AOF log and restores state within seconds
- Execution Service reconciles open positions against Alpaca before placing any new orders

**Recovery time:** ~2–3 minutes, fully automatic. No manual intervention needed.

**How to ensure Docker starts on boot:**
```bash
sudo systemctl enable docker
sudo systemctl enable containerd
```

---

### Scenario 2 — VM is Completely Down (Extended Outage)
Oracle Cloud has a strong uptime SLA but outages do happen. If the VM is unreachable for an extended period:

**Implications:**
- No new data is fetched — bot is blind to market moves
- No new orders are placed — existing positions remain open at Alpaca
- Open positions are exposed to market risk with no monitoring or stop-loss

**What to do:**
1. Log into Alpaca directly and manually review open positions
2. Close any positions where risk exposure is unacceptable
3. Wait for the VM to recover, or spin up a new Oracle instance from the backup (see below)

**This is why paper trading first matters** — you want to understand your typical open positions and exposure before a real outage happens with real money.

---

### Scenario 3 — PostgreSQL Data Corruption
Corruption can occur from a hard shutdown mid-write, disk failure, or a bug.

**Symptoms:** Services fail to start, dashboard shows no data, PostgreSQL logs show errors.

**Recovery options:**

| Option | Data Loss | Recovery Time | Effort |
|---|---|---|---|
| PostgreSQL auto-recovery (WAL replay) | None | Minutes | Automatic |
| Restore from daily backup | Up to 24 hours | ~30 minutes | Manual |
| Rebuild from Alpaca history | Trades only, no AI decisions | Hours | Manual |

**Backup strategy:**
- A daily cron job on the VM dumps the database to a compressed file:
  ```bash
  pg_dump alphadivision | gzip > /backups/alphadivision-$(date +%Y%m%d).sql.gz
  ```
- Backups are copied to Oracle Cloud Object Storage (free tier: 20GB) for off-VM redundancy
- Last 30 days of backups are retained

**Restoring from backup:**
```bash
gunzip < /backups/alphadivision-20260515.sql.gz | psql alphadivision
```

---

### Scenario 4 — Accidental `docker-compose down -v`
This deletes all Docker volumes including PostgreSQL data and Redis AOF. Recovery depends entirely on whether a backup exists.

- If a daily backup exists: restore PostgreSQL from the latest backup (up to 24 hours of data loss)
- Redis state is transient by design — messages in flight are lost but services rebuild state on next data cycle
- **Prevention:** the deployment workflow (Section 6) never uses `-v`. Document this clearly in the team runbook.

---

### Summary

| Scenario | Data Loss Risk | Auto-Recovers? | Manual Action Needed |
|---|---|---|---|
| VM reboot | None | ✅ Yes | None |
| Extended VM outage | None (data safe) | ✅ When VM returns | Monitor open positions manually |
| PostgreSQL corruption (soft) | None | ✅ WAL replay | None |
| PostgreSQL corruption (hard) | Up to 24 hours | ❌ No | Restore from backup |
| `docker-compose down -v` | Up to 24 hours | ❌ No | Restore from backup |

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
