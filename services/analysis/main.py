import sys
import os
import time

sys.path.insert(0, "/app")

from shared.logger import get_logger
from shared.redis_client import get_redis
from stream_reader import read_next_snapshots, ack_snapshot
from filters import passes_technical_filter, passes_sell_filter
from claude_client import call_claude, MODEL_HAIKU
from signal_writer import write_decision, write_signal, CONFIDENCE_THRESHOLD
from position_reader import get_open_position_symbols
from health_server import start_health_server
from alerter import send_alert

log = get_logger("analysis")

_HEARTBEAT_KEY = "heartbeat:analysis"
_HEARTBEAT_TTL = 90        # seconds — refreshed every 60s so TTL never expires during normal operation
_HEARTBEAT_INTERVAL = 60   # seconds


def _get_env(key: str) -> str:
    """Retrieve a required environment variable, raising clearly if missing."""
    value = os.getenv(key, "")
    if not value:
        raise RuntimeError(f"Required environment variable '{key}' is not set")
    return value


def _publish_heartbeat() -> None:
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


def _process_snapshot(snapshot: dict, anthropic_api_key: str, held_symbols: set) -> None:
    """
    Run one market snapshot through the two-stage analysis pipeline.

    Stage 1 (free, fast): technical filter.
    - Held symbols: passes_sell_filter — looks for weakness/overbought signals.
      Claude is asked for a sell recommendation; hold is also valid.
    - Non-held symbols: passes_technical_filter — looks for buy setups.

    Stage 2 (Claude AI): build prompt → call Claude → parse decision JSON.
        Every Claude decision is written to the decisions table.
        Actionable decisions (buy/sell, confidence >= 0.65) are additionally
        written to signals and published to stream:signals.

    Any exception is caught and logged — the main loop always continues.
    """
    symbol = snapshot.get("symbol", "UNKNOWN")
    msg_id = snapshot.pop("_msg_id", None)
    is_held = symbol in held_symbols

    try:
        # --- Stage 1: Technical filter ---
        if is_held:
            passed, reason = passes_sell_filter(snapshot)
        else:
            passed, reason = passes_technical_filter(snapshot)

        if not passed:
            log.info(f"[{symbol}] Stage 1 filter failed ({'sell' if is_held else 'buy'}): {reason}")
            return

        log.info(f"[{symbol}] Stage 1 passed ({'sell check' if is_held else 'buy check'}) — calling Claude ({MODEL_HAIKU})")

        # --- Stage 2: Claude AI ---
        try:
            result = call_claude(snapshot, anthropic_api_key, model=MODEL_HAIKU)
        except Exception as exc:
            log.error(f"[{symbol}] Claude call failed: {exc}")
            send_alert(f"[analysis] [{symbol}] Claude call failed: {exc}")
            return

        decision = result["decision"]
        confidence = result["confidence"]
        reasoning = result["reasoning"]
        model = result["model"]

        # Determine whether to act on this decision
        skip_reason = None
        acted_on = False

        if decision == "hold":
            skip_reason = "Claude decision is hold"
        elif confidence < CONFIDENCE_THRESHOLD:
            skip_reason = f"Confidence {confidence:.2f} below threshold {CONFIDENCE_THRESHOLD}"
        else:
            acted_on = True

        # Write decision record (always — for dashboard visibility)
        try:
            decision_id = write_decision(
                symbol, decision, confidence, reasoning, model, acted_on, skip_reason
            )
        except Exception as exc:
            log.error(f"[{symbol}] Failed to write decision to DB: {exc}")
            send_alert(f"[analysis] [{symbol}] DB write failed: {exc}")
            return

        log.info(
            f"[{symbol}] Decision: {decision} confidence={confidence:.2f} "
            f"acted_on={acted_on}"
            + (f" skip='{skip_reason}'" if skip_reason else "")
        )

        # Publish signal (only for actionable decisions)
        if acted_on:
            try:
                write_signal(symbol, decision, confidence, decision_id)
                log.info(f"[{symbol}] Signal published: {decision} ({confidence:.2f})")
            except Exception as exc:
                log.error(f"[{symbol}] Failed to publish signal: {exc}")
                send_alert(f"[analysis] [{symbol}] Signal publish failed: {exc}")

    except Exception as exc:
        log.error(f"[{symbol}] Unexpected error in _process_snapshot: {exc}")
        send_alert(f"[analysis] [{symbol}] Unexpected error: {exc}")
    finally:
        if msg_id:
            ack_snapshot(msg_id)


def main() -> None:
    log.info("Analysis Service starting")

    anthropic_api_key = _get_env("ANTHROPIC_API_KEY")

    start_health_server()
    last_heartbeat = 0.0

    while True:
        now = time.time()

        # Heartbeat (every 60 seconds)
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            try:
                _publish_heartbeat()
            except Exception as exc:
                log.error(f"Heartbeat failed: {exc}")
                send_alert(f"[analysis] Heartbeat publish failed: {exc}")
            last_heartbeat = now

        # Read and process snapshots (blocks up to 5 seconds if none available)
        try:
            snapshots = read_next_snapshots(count=10, block_ms=5000)
        except Exception as exc:
            log.error(f"Failed to read from stream: {exc}")
            send_alert(f"[analysis] Stream read failed: {exc}")
            time.sleep(5)
            continue

        # Fetch held symbols once per batch — cheap DB read, consistent within a cycle
        try:
            held_symbols = get_open_position_symbols()
        except Exception as exc:
            log.error(f"Failed to read open positions: {exc}")
            held_symbols = set()

        for snapshot in snapshots:
            _process_snapshot(snapshot, anthropic_api_key, held_symbols)


if __name__ == "__main__":
    main()
