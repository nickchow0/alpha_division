# services/research/main.py
import sys
import os
sys.path.insert(0, "/app")

import jinja2
from flask import Flask, render_template, jsonify, request
from shared.config import load_config
from shared.logger import get_logger

from strategy import validate_strategy_code, compute_code_hash
from backtester import run_backtest
from data import fetch_bars_alpaca
from queries import (
    save_strategy,
    get_strategies,
    save_backtest_run,
    save_backtest_trades,
    get_strategy_runs,
    update_strategy_status,
    get_candidates,
    get_run_trades,
    get_ml_runs,
)

log = get_logger("research")

_DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8080")
_RESEARCH_URL  = os.environ.get("RESEARCH_URL",  "http://localhost:8081")

app = Flask(__name__)

# Load templates from both this service and shared/templates/
app.jinja_loader = jinja2.ChoiceLoader([
    app.jinja_loader,
    jinja2.FileSystemLoader("/app/shared/templates"),
])

@app.context_processor
def _inject_nav():
    """Inject nav URLs and active_page into every template."""
    endpoint = request.endpoint or ""
    _page_map = {
        "research_page":   "research",
        "candidates_page": "research",
    }
    return dict(
        dashboard_url=_DASHBOARD_URL,
        research_url=_RESEARCH_URL,
        active_page=_page_map.get(endpoint, "research"),
    )

# Candidate promotion thresholds (Alpaca run must pass all)
_SHARPE_MIN = 0.5
_WIN_RATE_MIN = 45.0
_MAX_DRAWDOWN_MAX = 20.0


_ML_URL = os.environ.get("ML_URL", "http://ml:8082")


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/api/ml/run", methods=["POST"])
def ml_run():
    try:
        resp = requests.post(f"{_ML_URL}/run", timeout=5)
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "ML service unreachable"}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/ml/status", methods=["GET"])
def ml_status():
    try:
        resp = requests.get(f"{_ML_URL}/status", timeout=5)
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.ConnectionError:
        return jsonify({"running": False}), 200


# ── HTML pages ────────────────────────────────────────────────────────────────

@app.route("/research")
def research_page():
    strategies = get_strategies()
    ml_runs = get_ml_runs()
    return render_template("research.html", strategies=strategies, ml_runs=ml_runs)


@app.route("/candidates")
def candidates_page():
    candidates = get_candidates()
    return render_template("candidates.html", candidates=candidates)


# ── Strategy API ──────────────────────────────────────────────────────────────

@app.route("/api/strategies", methods=["POST"])
def create_strategy():
    body = request.get_json(silent=True) or {}
    code = body.get("code", "")
    try:
        validate_strategy_code(code)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    strategy_id = save_strategy(
        name=body.get("name", "Unnamed"),
        description=body.get("description", ""),
        hypothesis=body.get("hypothesis", ""),
        code=code,
        code_hash=compute_code_hash(code),
        triggered_by=body.get("triggered_by", "manual"),
    )
    log.info("Strategy saved: id=%s name=%s", strategy_id, body.get("name"))
    return jsonify({"id": strategy_id, "status": "draft"}), 201


@app.route("/api/strategies", methods=["GET"])
def list_strategies():
    strategies = get_strategies()
    return jsonify([dict(s) for s in strategies])


@app.route("/api/strategies/<int:strategy_id>/backtest", methods=["POST"])
def trigger_backtest(strategy_id: int):
    import datetime

    body = request.get_json(silent=True) or {}
    symbol = body.get("symbol")
    start_date = body.get("start_date")
    end_date = body.get("end_date")

    if not symbol or not start_date or not end_date:
        return jsonify({"error": "symbol, start_date, end_date are required"}), 400

    # Fetch strategy code
    strategies = get_strategies()
    strat = next((s for s in strategies if s["id"] == strategy_id), None)
    if strat is None:
        return jsonify({"error": "Strategy not found"}), 404

    params = {
        "initial_capital": float(body.get("initial_capital", 100_000)),
        "max_position_pct": float(body.get("max_position_pct", 0.15)),
        "stop_loss_pct": float(body.get("stop_loss_pct", 0.05)),
        "max_hold_bars": int(body.get("max_hold_bars", 20)),
    }

    try:
        start = datetime.date.fromisoformat(start_date)
        end = datetime.date.fromisoformat(end_date)

        bars = fetch_bars_alpaca(
            symbol=symbol, start_date=start, end_date=end,
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
            base_url=os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.error("Data fetch failed: %s", e)
        return jsonify({"error": "Data fetch failed"}), 500

    try:
        metrics, trades = run_backtest(strat["code"], bars, params)
    except Exception as e:
        log.error("Backtest failed for strategy %s: %s", strategy_id, e)
        return jsonify({"error": str(e)}), 500

    # Persist results
    update_strategy_status(strategy_id=strategy_id, status="testing")
    run_id = save_backtest_run(
        strategy_id=strategy_id, symbol=symbol,
        start_date=start, end_date=end,
        data_source="alpaca", params=params, metrics=metrics,
    )
    if trades:
        save_backtest_trades(run_id=run_id, symbol=symbol, trades=trades)

    # Auto-promote to candidate if run passes thresholds
    if _passes_candidate_thresholds(metrics):
        update_strategy_status(strategy_id=strategy_id, status="candidate")
        log.info(
            "Strategy %s promoted to candidate (Sharpe=%.2f)",
            strategy_id, metrics.get("sharpe_ratio") or 0,
        )

    log.info(
        "Backtest complete: strategy=%s run=%s trades=%s",
        strategy_id, run_id, metrics["trade_count"],
    )
    return jsonify({"run_id": run_id, "metrics": metrics}), 200


def _spy_annualized_return(start_date, end_date) -> float | None:
    """Annualised SPY return for the period. Returns None on any failure."""
    try:
        api_key  = os.environ.get("ALPACA_API_KEY", "")
        secret   = os.environ.get("ALPACA_SECRET_KEY", "")
        base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        bars = fetch_bars_alpaca("SPY", start_date, end_date, api_key, secret, base_url)
        if len(bars) < 2:
            return None
        years = (end_date - start_date).days / 365.25
        if years <= 0:
            return None
        return round(((bars[-1]["c"] / bars[0]["c"]) ** (1 / years) - 1) * 100, 4)
    except Exception:
        return None


@app.route("/api/strategies/<int:strategy_id>/runs", methods=["GET"])
def strategy_runs(strategy_id: int):
    runs = get_strategy_runs(strategy_id)

    # Fetch SPY benchmark once per unique (start_date, end_date) pair
    spy_cache: dict = {}
    for r in runs:
        key = (r["start_date"], r["end_date"])
        if key not in spy_cache:
            spy_cache[key] = _spy_annualized_return(r["start_date"], r["end_date"])

    result = []
    for r in runs:
        row = dict(r)
        row["spy_annualized_return_pct"] = spy_cache[(r["start_date"], r["end_date"])]
        result.append(row)
    return jsonify(result)


@app.route("/api/strategies/<int:strategy_id>/approve", methods=["POST"])
def approve_strategy(strategy_id: int):
    update_strategy_status(strategy_id=strategy_id, status="approved")
    log.info("Strategy %s approved", strategy_id)
    return jsonify({"status": "approved"}), 200


@app.route("/api/strategies/<int:strategy_id>/retire", methods=["POST"])
def retire_strategy(strategy_id: int):
    update_strategy_status(strategy_id=strategy_id, status="retired")
    log.info("Strategy %s retired", strategy_id)
    return jsonify({"status": "retired"}), 200


@app.route("/api/runs/<int:run_id>/trades", methods=["GET"])
def run_trades(run_id: int):
    trades = get_run_trades(run_id)
    return jsonify([dict(t) for t in trades])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _passes_candidate_thresholds(metrics: dict) -> bool:
    """Return True if Alpaca run metrics meet candidate promotion thresholds."""
    sharpe = metrics.get("sharpe_ratio")
    win_rate = metrics.get("win_rate_pct")
    max_dd = metrics.get("max_drawdown_pct")
    trade_count = metrics.get("trade_count", 0)
    if not all([sharpe is not None, win_rate is not None, max_dd is not None]):
        return False
    return (
        trade_count > 0
        and sharpe >= _SHARPE_MIN
        and win_rate >= _WIN_RATE_MIN
        and max_dd <= _MAX_DRAWDOWN_MAX
    )


if __name__ == "__main__":
    log.info("Research Service starting")
    app.run(host="0.0.0.0", port=8081)
