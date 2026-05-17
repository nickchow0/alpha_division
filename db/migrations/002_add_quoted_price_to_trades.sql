-- Migration 002: Add quoted_price to trades for slippage tracking
-- quoted_price captures the Alpaca ask (buy) or bid (sell) at order submission time.
-- Slippage = (quoted_price - price) * qty for buys; (price - quoted_price) * qty for sells.
-- NULL for trades placed before this migration.

ALTER TABLE trades ADD COLUMN IF NOT EXISTS quoted_price DECIMAL(10,4);
COMMENT ON COLUMN trades.quoted_price IS 'ask (buy) or bid (sell) from quote API at submission';
