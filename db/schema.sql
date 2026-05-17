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
    price DECIMAL(10,4),                  -- last bar close at submission (sizing reference)
    quoted_price DECIMAL(10,4),           -- ask (buy) or bid (sell) from quote API at submission
    alpaca_order_id VARCHAR(100),
    signal_id INTEGER REFERENCES signals(id),
    confidence DECIMAL(4,3),              -- copied from signals.confidence at trade time
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
