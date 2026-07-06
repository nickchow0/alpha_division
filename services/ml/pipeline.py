"""services/ml/pipeline.py — ML strategy discovery pipeline entrypoint.

Runs a nightly batch job at 2am. Exposes GET /health on port 8082 for the
existing watchdog. The five phases are orchestrated in _run_phases():
  1. collect_bars     — fetch + cache OHLCV bars via Alpaca
  2. compute_features — 26-indicator vectors per bar
  3. discover_patterns — DT + k-means pattern discovery
  4. codegen           — Claude/Gemini API → generate_signal() code
  5. backtest+promote  — call Research API to backtest and auto-promote
"""
import logging
import os
import threading
import time
from datetime import date, timedelta
from typing import Optional

import requests
import schedule
from flask import Flask, jsonify

import anthropic

from shared.config import load_config
from shared.enums import AIProvider, ClaudeModel, GeminiModel
from shared.redis_client import get_redis
from collector import collect_bars
from features import compute_features
from discoverer import discover_patterns, CandidatePattern
from codegen import generate_strategy_code, code_hash
from validator import validate_against_pattern
from queries import (
    save_ml_strategy, save_ml_run, ensure_ml_tables,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("ml")

app = Flask(__name__)

_pipeline_lock = threading.Lock()
_pipeline_running = False


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/run", methods=["POST"])
def trigger_run():
    global _pipeline_running
    with _pipeline_lock:
        if _pipeline_running:
            return jsonify({"status": "already_running"}), 409
        _pipeline_running = True

    def _run_and_clear():
        global _pipeline_running
        try:
            run_pipeline()
        finally:
            with _pipeline_lock:
                _pipeline_running = False

    threading.Thread(target=_run_and_clear, daemon=True, name="manual-pipeline").start()
    log.info("Manual pipeline run triggered via API")
    return jsonify({"status": "started"}), 202


@app.route("/status", methods=["GET"])
def pipeline_status():
    return jsonify({"running": _pipeline_running})


# ── Phase helpers ─────────────────────────────────────────────────────────────

def _call_backtest_api(
    research_url: str,
    strategy_id: int,
    symbol: str,
    start_date: str,
    end_date: str,
    data_source: str,
) -> Optional[dict]:
    """POST to Research backtest API. Returns metrics dict or None on failure."""
    endpoint = f"{research_url}/api/strategies/{strategy_id}/backtest"
    payload = {
        "symbol": symbol,
        "start_date": start_date,
        "end_date": end_date,
        "data_source": data_source,
    }
    try:
        resp = requests.post(endpoint, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("metrics", {})
    except Exception as exc:  # noqa: BLE001
        log.error(
            "Backtest API call failed for strategy %d (%s): %s",
            strategy_id, data_source, exc,
        )
        return None


def _backtest_strategy(strategy_id: int, symbol: Optional[str], research_url: str) -> bool:
    """Run Alpaca backtest via Research API. Returns True if promoted to candidate.

    If Alpaca credentials are not configured, returns False.
    """
    today = date.today()
    start_date = (today - timedelta(days=730)).isoformat()  # 2yr daily bars
    end_date = today.isoformat()
    sym = symbol or "SPY"

    if not os.environ.get("ALPACA_API_KEY"):
        log.info("Strategy %d: Alpaca creds not configured — skipping backtest", strategy_id)
        return False

    metrics = _call_backtest_api(research_url, strategy_id, sym, start_date, end_date, "alpaca")
    if metrics is None:
        return False

    # Mirror the Research service candidate thresholds
    promoted = (
        (metrics.get("trade_count") or 0) > 0
        and (metrics.get("sharpe_ratio") or 0) >= 0.5
        and (metrics.get("win_rate_pct") or 0) >= 45.0
        and (metrics.get("max_drawdown_pct") or 100.0) <= 20.0
    )
    log.info(
        "Strategy %d Alpaca backtest: promoted=%s, Sharpe=%.2f, win_rate=%.1f%%",
        strategy_id, promoted,
        metrics.get("sharpe_ratio") or 0,
        metrics.get("win_rate_pct") or 0,
    )
    return promoted


def _run_phases() -> None:
    """Execute all 5 pipeline phases and record results in ml_runs."""
    cfg       = load_config()
    ml_cfg    = cfg.get("ml", {})
    symbols   = ml_cfg.get("symbols", [])
    research_url = os.environ.get("RESEARCH_URL", "http://research:8081")

    start = time.time()
    patterns_found         = 0
    strategies_generated   = 0
    candidates_promoted    = 0
    bars_by_symbol         = {}
    run_error: Optional[str] = None

    try:
        # Phase 1: Data collection
        log.info("Phase 1: Collecting bars for %d symbols", len(symbols))
        bars_by_symbol = collect_bars(
            symbols,
            lookback_days=ml_cfg.get("lookback_days_regime", 1825),
        )
        log.info("Phase 1 complete: %d symbols with data", len(bars_by_symbol))

        # Phase 2: Feature engineering
        log.info("Phase 2: Computing features")
        features_by_symbol: dict = {}
        for sym, bars in bars_by_symbol.items():
            rows = compute_features(bars)
            if rows:
                features_by_symbol[sym] = rows
        log.info("Phase 2 complete: %d symbols with features", len(features_by_symbol))

        # Phase 3: Pattern discovery
        log.info("Phase 3: Discovering patterns")
        patterns = discover_patterns(features_by_symbol, ml_cfg)
        patterns_found = len(patterns)
        log.info("Phase 3 complete: %d candidate patterns", patterns_found)

        if patterns_found == 0:
            log.warning("No patterns found — pipeline run produced 0 strategies")
            _send_discord_alert("ML pipeline: 0 patterns found after full run")

        # Phase 4: Strategy codegen
        log.info("Phase 4: Generating strategy code for %d patterns", patterns_found)

        r = get_redis()
        _raw_provider = r.get("config:ml_codegen_provider") or ml_cfg.get("codegen_provider", AIProvider.GEMINI)
        if isinstance(_raw_provider, bytes):
            _raw_provider = _raw_provider.decode()
        _codegen_provider = AIProvider(_raw_provider)

        if _codegen_provider == AIProvider.GEMINI:
            _model_key = "config:ml_codegen_gemini_model"
            _default_model = ml_cfg.get("codegen_gemini_model", GeminiModel.FLASH_20)
        else:
            _model_key = "config:ml_codegen_claude_model"
            _default_model = ml_cfg.get("codegen_model", ClaudeModel.SONNET)

        _raw_model = r.get(_model_key) or _default_model
        if isinstance(_raw_model, bytes):
            _raw_model = _raw_model.decode()
        _codegen_model = _raw_model

        log.info("Phase 4 codegen: provider=%s model=%s", _codegen_provider, _codegen_model)

        if _codegen_provider == AIProvider.GEMINI:
            _codegen_client = None
        else:
            _codegen_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        saved_strategies: list[tuple[int, Optional[str]]] = []

        for pattern in patterns:
            code = generate_strategy_code(
                pattern,
                client=_codegen_client,
                provider=_codegen_provider,
                model=_codegen_model,
            )
            if code is None:
                log.warning("Codegen failed for pattern: %.60s", pattern.rule_description)
                continue

            if not validate_against_pattern(code, pattern.rows, ml_cfg):
                log.warning(
                    "Replay gate rejected strategy for pattern: %.60s",
                    pattern.rule_description,
                )
                continue

            h = code_hash(code)
            strategy_name = (
                f"ML-{pattern.pattern_type[:2].upper()}-{pattern.symbol or 'XSYM'}-{h[:6]}"
            )
            description = (
                f"ML-discovered {pattern.pattern_type} pattern. "
                f"{pattern.example_count} historical examples, "
                f"avg 10-bar return {pattern.avg_forward_return_pct:.2f}%, "
                f"win rate {pattern.win_rate_pct:.1f}%, Sharpe {pattern.sharpe:.2f}."
            )
            hypothesis = pattern.rule_description

            try:
                strategy_id = save_ml_strategy(
                    name=strategy_name,
                    description=description,
                    hypothesis=hypothesis,
                    code=code,
                    code_hash=h,
                )
                saved_strategies.append((strategy_id, pattern.symbol))
                strategies_generated += 1
                log.info("Saved strategy %d: %s", strategy_id, strategy_name)
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to save strategy: %s", exc)

        log.info("Phase 4 complete: %d strategies generated", strategies_generated)

        if strategies_generated == 0 and patterns_found > 0:
            _send_discord_alert(
                f"ML pipeline: {patterns_found} patterns found but 0 strategies generated"
            )

        # Phase 5: Backtest + promote via Research API
        log.info("Phase 5: Backtesting %d strategies", len(saved_strategies))
        for strategy_id, symbol in saved_strategies:
            promoted = _backtest_strategy(strategy_id, symbol, research_url)
            if promoted:
                candidates_promoted += 1

        log.info(
            "Phase 5 complete: %d/%d strategies promoted to candidate",
            candidates_promoted, strategies_generated,
        )

    except Exception as exc:  # noqa: BLE001
        run_error = str(exc)
        log.error("Pipeline phase failed: %s", exc, exc_info=True)
        _send_discord_alert(f"ML pipeline error: {exc}")

    duration = time.time() - start
    try:
        run_id = save_ml_run(
            symbols_processed=len(bars_by_symbol),
            patterns_found=patterns_found,
            strategies_generated=strategies_generated,
            candidates_promoted=candidates_promoted,
            duration_seconds=duration,
            error=run_error,
        )
        log.info("Run record saved: id=%d, duration=%.1fs", run_id, duration)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to save ml_run record: %s", exc)

    # Alert if > 15 minutes
    if duration > 900:
        _send_discord_alert(f"ML pipeline took {duration:.0f}s (> 15 min threshold)")


def run_pipeline() -> None:
    """Top-level pipeline entry. Catches all unhandled exceptions."""
    log.info("=== ML pipeline run starting ===")
    try:
        _run_phases()
    except Exception as exc:  # noqa: BLE001
        log.error("Unhandled pipeline exception: %s", exc, exc_info=True)
        _send_discord_alert(f"ML pipeline unhandled exception: {exc}")
    log.info("=== ML pipeline run complete ===")


def _send_discord_alert(message: str) -> None:
    """Send a Discord alert via webhook if DISCORD_WEBHOOK_URL is configured."""
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    try:
        requests.post(url, json={"content": f"[ml] {message}"}, timeout=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("Discord alert failed: %s", exc)


def _start_health_server() -> None:
    """Run Flask health server in a background daemon thread on port 8082."""
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=8082, use_reloader=False),
        daemon=True,
        name="health",
    ).start()
    log.info("Health server started on :8082")


def main() -> None:
    cfg = load_config().get("ml", {})
    cron = cfg.get("cron_schedule", "0 2 * * *")

    # Ensure ML tables exist before first run
    ensure_ml_tables()

    _start_health_server()

    # Parse cron schedule: "0 2 * * *" → 02:00 UTC
    parts  = cron.split()
    minute = int(parts[0])
    hour   = int(parts[1])
    schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(run_pipeline)
    log.info("Pipeline scheduled at %02d:%02d UTC nightly", hour, minute)

    # Run once immediately on startup
    run_pipeline()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
