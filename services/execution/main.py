import sys
import os
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app")

import alpaca_trade_api as tradeapi

from shared.logger import get_logger
from shared.redis_client import get_redis

from stream_reader import read_next_signals, ack_signal
from risk_checker import (
    check_trading_window,
    check_position_rules,
    check_position_limit,
    calculate_qty,
    check_circuit_breaker,
)
from position_manager import get_positions, get_portfolio_value, get_last_price, get_quote
from order_placer import place_order, get_last_buy_price, poll_for_fill, update_trade_fill, reconcile_submitted_trades
from pnl_tracker import (
    get_today_pnl,
    add_realized_pnl,
    is_circuit_breaker_triggered,
    trigger_circuit_breaker,
)
from health_server import start_health_server

log = get_logger("execution")

_ET = ZoneInfo("America/New_York")
_HEARTBEAT_KEY = "heartbeat:execution"
_HEARTBEAT_TTL = 90        # seconds — refreshed every 60s so TTL never expires
_HEARTBEAT_INTERVAL = 60   # seconds
_CIRCUIT_BREAKER_LIMIT = 200.0


def _get_env(key: str) -> str:
    """Retrieve a required environment variable, raising clearly if missing."""
    value = os.getenv(key, "")
    if not value:
        raise RuntimeError(f"Required environment variable '{key}' is not set")
    return value


def _publish_heartbeat() -> None:
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


def _process_signal(signal: dict, api) -> None:
    """
    Run one trade signal through the full risk-check and execution pipeline.

    All risk checks run before any order is placed. If any check fails,
    the signal is logged and acked without placing an order.

    The signal is always acked in the finally block — whether we traded,
    skipped, or hit an unexpected error — so the consumer group never stalls.
    """
    symbol = signal.get("symbol", "UNKNOWN")
    side = signal.get("decision", "")
    msg_id = signal.pop("_msg_id", None)
    if msg_id is None:
        log.warning(f"[{symbol}] Signal has no _msg_id — cannot ack, may be redelivered")

    try:
        now = datetime.now(_ET)
        today = now.date()

        # --- Trading window check ---
        allowed, reason = check_trading_window(now)
        if not allowed:
            log.info(f"[{symbol}] Trading window blocked: {reason}")
            return

        # --- Circuit breaker flag (DB — persists across restarts) ---
        if is_circuit_breaker_triggered(today):
            log.warning(f"[{symbol}] Circuit breaker active for today, skipping all orders")
            return

        # --- Get fresh positions from Alpaca ---
        try:
            positions = get_positions(api)
        except Exception as exc:
            log.error(f"[{symbol}] Failed to get positions from Alpaca: {exc}")
            return

        # --- Layer 1a: position rules ---
        ok, reason = check_position_rules(symbol, side, positions)
        if not ok:
            log.info(f"[{symbol}] Position rule blocked: {reason}")
            return

        # --- Layer 1b: position limit (buy only) ---
        if side == "buy":
            ok, reason = check_position_limit(positions)
            if not ok:
                log.info(f"[{symbol}] Position limit blocked: {reason}")
                return

        # --- Get current market price ---
        try:
            price = get_last_price(api, symbol)
        except Exception as exc:
            log.error(f"[{symbol}] Failed to get price: {exc}")
            return

        # --- Layer 2: position sizing ---
        if side == "buy":
            try:
                portfolio_value = get_portfolio_value(api)
            except Exception as exc:
                log.error(f"[{symbol}] Failed to get portfolio value: {exc}")
                return
            qty = calculate_qty(portfolio_value, price)
            if qty == 0:
                log.info(
                    f"[{symbol}] Qty=0 after 2% sizing "
                    f"(portfolio ${portfolio_value:.2f}, price ${price:.2f}) — skipping"
                )
                return
        else:  # sell — use the actual held qty
            qty = positions[symbol]

        # --- Layer 3: circuit breaker numeric check ---
        try:
            today_pnl = get_today_pnl(today)
        except Exception as exc:
            log.error(f"[{symbol}] Failed to read today P&L: {exc}")
            return

        ok, reason = check_circuit_breaker(today_pnl)
        if not ok:
            trigger_circuit_breaker(today)
            log.error(f"[{symbol}] Circuit breaker check: {reason}")
            return

        # --- Capture quote for slippage tracking ---
        try:
            ask, bid = get_quote(api, symbol)
            quoted_price = ask if side == "buy" else bid
        except Exception as exc:
            log.warning(
                f"[{symbol}] Failed to get quote for slippage tracking: {exc} "
                f"— order will proceed without quoted_price"
            )
            quoted_price = None

        # --- Place market order ---
        confidence = signal.get("confidence")
        try:
            trade = place_order(api, symbol, side, qty, price, confidence=confidence, quoted_price=quoted_price)
            log.info(
                f"[{symbol}] Order placed: {side} {qty} shares at ~${price:.2f} "
                f"(trade_id={trade['id']}, alpaca_order_id={trade['alpaca_order_id']})"
            )
        except Exception as exc:
            log.error(f"[{symbol}] Failed to place order: {exc}")
            return

        # --- Poll for actual fill price and update trade record ---
        try:
            fill_status, filled_price = poll_for_fill(api, trade["alpaca_order_id"])
            update_trade_fill(trade["id"], filled_price, fill_status)
            if fill_status == "filled" and filled_price is not None:
                log.info(f"[{symbol}] Filled at ${filled_price:.2f} (estimated ${price:.2f})")
                price = filled_price  # use real fill price for P&L below
            elif fill_status == "submitted":
                log.warning(f"[{symbol}] Fill poll timed out — trade recorded as submitted")
            else:
                log.warning(f"[{symbol}] Order ended with status '{fill_status}'")
        except Exception as exc:
            log.warning(f"[{symbol}] Fill poll failed: {exc} — P&L will use estimated price")

        # --- Update realized P&L on sell ---
        if side == "sell":
            try:
                buy_price = get_last_buy_price(symbol)
                if buy_price is not None:
                    realized = (price - buy_price) * qty
                    add_realized_pnl(realized, today)
                    new_pnl = get_today_pnl(today)
                    log.info(
                        f"[{symbol}] Realized P&L: ${realized:+.2f} | "
                        f"Daily total: ${new_pnl:+.2f}"
                    )
                    if new_pnl <= -_CIRCUIT_BREAKER_LIMIT:
                        trigger_circuit_breaker(today)
                        log.error(
                            f"Circuit breaker triggered after sell — "
                            f"daily loss ${abs(new_pnl):.2f} exceeds "
                            f"${_CIRCUIT_BREAKER_LIMIT:.0f} limit"
                        )
                else:
                    log.warning(
                        f"[{symbol}] No previous buy found in trades table — "
                        f"P&L not updated"
                    )
            except Exception as exc:
                log.error(f"[{symbol}] Failed to update P&L after sell: {exc}")

    except Exception as exc:
        log.error(f"[{symbol}] Unexpected error in _process_signal: {exc}", exc_info=True)
    finally:
        if msg_id:
            ack_signal(msg_id)


def main() -> None:
    log.info("Execution Service starting")

    alpaca_key = _get_env("ALPACA_API_KEY")
    alpaca_secret = _get_env("ALPACA_SECRET_KEY")
    alpaca_base_url = _get_env("ALPACA_BASE_URL")

    api = tradeapi.REST(alpaca_key, alpaca_secret, alpaca_base_url)

    # Startup reconciliation — log current positions so we know what state we're in
    try:
        positions = get_positions(api)
        log.info(
            f"Startup reconciliation: {len(positions)} open position(s): "
            f"{list(positions.keys()) or 'none'}"
        )
    except Exception as exc:
        log.error(f"Startup position reconciliation failed — proceeding anyway: {exc}")

    # Resolve any trades stuck in 'submitted' from a previous run
    try:
        n = reconcile_submitted_trades(api)
        if n:
            log.info(f"Startup reconciliation: resolved {n} submitted trade(s)")
        else:
            log.info("Startup reconciliation: no submitted trades to resolve")
    except Exception as exc:
        log.error(f"Startup trade reconciliation failed — proceeding anyway: {exc}")

    start_health_server()

    last_heartbeat = 0.0

    while True:
        now = time.time()

        # Heartbeat every 60 seconds
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            try:
                _publish_heartbeat()
                last_heartbeat = now
            except Exception as exc:
                log.error(f"Heartbeat failed: {exc}")

        # Read and process signals (blocks up to 5 seconds if none available)
        try:
            signals = read_next_signals(count=10, block_ms=5000)
        except Exception as exc:
            log.error(f"Failed to read from stream: {exc}")
            time.sleep(5)
            continue

        for signal in signals:
            _process_signal(signal, api)


if __name__ == "__main__":
    main()
