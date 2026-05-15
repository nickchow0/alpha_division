import sys
import os
import time

sys.path.insert(0, "/app")

from shared.logger import get_logger
from shared.redis_client import get_redis
from stream_reader import read_next_snapshots, ack_snapshot
from filters import passes_technical_filter
from claude_client import call_claude, MODEL_HAIKU
from signal_writer import write_decision, write_signal, CONFIDENCE_THRESHOLD

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


def _process_snapshot(snapshot: dict, anthropic_api_key: str) -> None:
    """
    Run one market snapshot through the two-stage analysis pipeline.

    Stage 1 (free, fast): technical filter — RSI range, price above SMA50,
        SMA20 momentum. Failures are logged and the snapshot is skipped.

    Stage 2 (Claude AI): build prompt → call Claude → parse decision JSON.
        Every Claude decision is written to the decisions table.
        Actionable decisions (buy/sell, confidence >= 0.65) are additionally
        written to signals and published to stream:signals.

    Any exception is caught and logged — the main loop always continues.
    """
    symbol = snapshot.get("symbol", "UNKNOWN")
    msg_id = snapshot.pop("_msg_id", None)

    try:
        # --- Stage 1: Technical filter ---
        passed, reason = passes_technical_filter(snapshot)
        if not passed:
            log.info(f"[{symbol}] Stage 1 filter failed: {reason}")
            if msg_id:
                ack_snapshot(msg_id)
            return

        log.info(f"[{symbol}] Stage 1 passed — calling Claude ({MODEL_HAIKU})")

        # --- Stage 2: Claude AI ---
        try:
            result = call_claude(snapshot, anthropic_api_key, model=MODEL_HAIKU)
        except Exception as exc:
            log.error(f"[{symbol}] Claude call failed: {exc}")
            if msg_id:
                ack_snapshot(msg_id)
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
            if msg_id:
                ack_snapshot(msg_id)
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

    except Exception as exc:
        log.error(f"[{symbol}] Unexpected error in _process_snapshot: {exc}")
    finally:
        if msg_id:
            ack_snapshot(msg_id)


def main() -> None:
    log.info("Analysis Service starting")

    anthropic_api_key = _get_env("ANTHROPIC_API_KEY")

    last_heartbeat = 0.0

    while True:
        now = time.time()

        # Heartbeat (every 60 seconds)
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            try:
                _publish_heartbeat()
            except Exception as exc:
                log.error(f"Heartbeat failed: {exc}")
            last_heartbeat = now

        # Read and process snapshots (blocks up to 5 seconds if none available)
        try:
            snapshots = read_next_snapshots(count=10, block_ms=5000)
        except Exception as exc:
            log.error(f"Failed to read from stream: {exc}")
            time.sleep(5)
            continue

        for snapshot in snapshots:
            _process_snapshot(snapshot, anthropic_api_key)


if __name__ == "__main__":
    main()
