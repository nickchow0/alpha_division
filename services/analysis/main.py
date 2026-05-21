import sys
import os
import time

sys.path.insert(0, "/app")

from shared.logger import get_logger
from shared.redis_client import get_redis
from stream_reader import read_next_snapshots, ack_snapshot
from filters import passes_technical_filter, passes_sell_filter
from ai_client import call_ai
from signal_writer import write_decision, write_signal, CONFIDENCE_THRESHOLD
from shared.config import load_config
from position_reader import get_open_position_symbols
from health_server import start_health_server
from alerter import send_alert

log = get_logger("analysis")

_HEARTBEAT_KEY = "heartbeat:analysis"
_HEARTBEAT_TTL = 90        # seconds — refreshed every 60s so TTL never expires during normal operation
_HEARTBEAT_INTERVAL = 60   # seconds

# Redis keys written by the dashboard settings page
_REDIS_AI_PROVIDER_KEY  = "config:ai_provider"
_REDIS_CLAUDE_MODEL_KEY = "config:claude_model"
_REDIS_GEMINI_MODEL_KEY = "config:gemini_model"


def _load_effective_config(base_config: dict) -> dict:
    """
    Merge Redis-persisted AI provider settings over the base config.

    The dashboard settings page writes to Redis; this lets live changes
    take effect within seconds without restarting the analysis service.
    """
    r = get_redis()
    provider     = r.get(_REDIS_AI_PROVIDER_KEY)
    claude_model = r.get(_REDIS_CLAUDE_MODEL_KEY)
    gemini_model = r.get(_REDIS_GEMINI_MODEL_KEY)

    overrides = {}
    if provider:
        overrides["ai_provider"] = provider.decode() if isinstance(provider, bytes) else provider
    if claude_model:
        overrides["claude_model"] = claude_model.decode() if isinstance(claude_model, bytes) else claude_model
    if gemini_model:
        overrides["gemini_model"] = gemini_model.decode() if isinstance(gemini_model, bytes) else gemini_model

    if not overrides:
        return base_config

    merged = dict(base_config)
    merged["analysis"] = {**base_config.get("analysis", {}), **overrides}
    return merged


def _get_env(key: str) -> str:
    """Retrieve a required environment variable, raising clearly if missing."""
    value = os.getenv(key, "")
    if not value:
        raise RuntimeError(f"Required environment variable '{key}' is not set")
    return value


def _publish_heartbeat() -> None:
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


def _process_snapshot(snapshot: dict, anthropic_api_key: str, gemini_api_key: str,
                      config: dict, held_symbols: set) -> None:
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

        provider = config.get("analysis", {}).get("ai_provider", "claude")
        log.info(f"[{symbol}] Stage 1 passed ({'sell check' if is_held else 'buy check'}) — calling {provider}")

        # --- Stage 2: AI decision ---
        try:
            result = call_ai(snapshot, config,
                             anthropic_api_key=anthropic_api_key,
                             gemini_api_key=gemini_api_key)
        except Exception as exc:
            log.error(f"[{symbol}] AI call failed: {exc}")
            send_alert(f"[analysis] [{symbol}] AI call failed: {exc}")
            return

        decision = result["decision"]
        confidence = result["confidence"]
        reasoning = result["reasoning"]
        model = result["model"]

        # Determine whether to act on this decision
        skip_reason = None
        acted_on = False

        if decision == "hold":
            skip_reason = "AI decision is hold"
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

    config = load_config()
    anthropic_api_key = _get_env("ANTHROPIC_API_KEY")
    gemini_api_key = os.getenv("GEMINI_API_KEY", "")

    provider = config.get("analysis", {}).get("ai_provider", "claude")
    log.info(f"AI provider: {provider}")

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

        # Merge Redis overrides (written by dashboard settings) over base config
        effective_config = _load_effective_config(config)

        # Fetch held symbols once per batch — cheap DB read, consistent within a cycle
        try:
            held_symbols = get_open_position_symbols()
        except Exception as exc:
            log.error(f"Failed to read open positions: {exc}")
            held_symbols = set()

        for snapshot in snapshots:
            _process_snapshot(snapshot, anthropic_api_key, gemini_api_key, effective_config, held_symbols)


if __name__ == "__main__":
    main()
