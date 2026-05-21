import sys
import os
import json
from datetime import datetime, date as Date
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app")

import jinja2
from flask import Flask, render_template, jsonify, request
from shared.config import load_config
from shared.logger import get_logger

from queries import (
    get_open_positions,
    get_total_pnl,
    get_daily_pnl_today,
    get_recent_trades,
    get_recent_decisions,
    get_api_health,
    get_watchlist,
    get_circuit_breaker_status,
    get_pnl_history,
    get_trade_activity,
    get_trade_stats,
    get_analysis_stats,
    get_confidence_histogram,
    get_acted_on_rate_by_band,
    get_win_rate_by_band,
    get_unrealized_pnl,
    get_account_equity,
    get_ai_settings,
    set_ai_provider,
    get_ml_codegen_settings,
    set_ml_codegen_provider,
)
from service_status import get_service_statuses

log = get_logger("dashboard")

_TZ = ZoneInfo("America/Los_Angeles")

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
        "overview":  "overview",
        "trades":    "trades",
        "decisions": "decisions",
        "analysis":  "analysis",
        "watchlist": "watchlist",
        "charts":    "charts",
        "settings":  "settings",
    }
    return dict(
        dashboard_url=_DASHBOARD_URL,
        research_url=_RESEARCH_URL,
        active_page=_page_map.get(endpoint, ""),
    )


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def _chart_data(days: int = 30) -> dict:
    """Build JSON-encoded chart data shared by overview and charts routes."""
    pnl_history = get_pnl_history(days)
    trade_activity = get_trade_activity(days)
    paper_balance = float(load_config().get("paper_balance", 100_000.0))

    # Cumulative realized P&L from daily_pnl table
    cumulative, running = [], 0.0
    for row in pnl_history:
        running += float(row["realized_pnl"])
        cumulative.append(round(running, 2))

    # Portfolio value: historical days from daily_pnl, plus today always appended
    # so the chart shows current value even before any trade is closed.
    portfolio_dates = [str(r["date"]) for r in pnl_history]
    portfolio_values = [round(paper_balance + v, 2) for v in cumulative]

    today_str = str(Date.today())
    # Prefer Alpaca's authoritative equity; fall back to reconstructed value
    # if the data service hasn't published it yet.
    alpaca_equity = get_account_equity()
    if alpaca_equity is not None:
        today_portfolio = round(alpaca_equity, 2)
    else:
        unrealized = get_unrealized_pnl()
        today_portfolio = round(paper_balance + running + unrealized, 2)

    if portfolio_dates and portfolio_dates[-1] == today_str:
        # Replace today's entry (daily_pnl row exists for today) with live value
        portfolio_values[-1] = today_portfolio
    else:
        # Append today as a live snapshot (no closed-trade row yet)
        portfolio_dates.append(today_str)
        portfolio_values.append(today_portfolio)

    return dict(
        pnl_dates=json.dumps([str(r["date"]) for r in pnl_history]),
        pnl_values=json.dumps([float(r["realized_pnl"]) for r in pnl_history]),
        cumulative_values=json.dumps(cumulative),
        portfolio_dates=json.dumps(portfolio_dates),
        portfolio_values=json.dumps(portfolio_values),
        trade_dates=json.dumps([str(r["date"]) for r in trade_activity]),
        trade_counts=json.dumps([int(r["count"]) for r in trade_activity]),
    )


@app.route("/")
def overview():
    today = datetime.now(_TZ).date()
    positions = get_open_positions()
    total_pnl = get_total_pnl()
    daily_pnl = get_daily_pnl_today(today)
    circuit_breaker = get_circuit_breaker_status(today)
    api_health = get_api_health()
    services = get_service_statuses()
    trade_stats = get_trade_stats()
    return render_template(
        "overview.html",
        positions=positions,
        total_pnl=total_pnl,
        daily_pnl=daily_pnl,
        circuit_breaker=circuit_breaker,
        api_health=api_health,
        services=services,
        trade_stats=trade_stats,
        **_chart_data(),
    )


@app.route("/trades")
def trades():
    trade_list = get_recent_trades()
    return render_template("trades.html", trades=trade_list)


@app.route("/decisions")
def decisions():
    decision_list = get_recent_decisions()
    return render_template("decisions.html", decisions=decision_list)


@app.route("/watchlist")
def watchlist():
    symbols = get_watchlist()
    return render_template("watchlist.html", symbols=symbols)


@app.route("/charts")
def charts():
    return render_template("charts.html", **_chart_data())


# ── JSON API (used by auto-refresh JS) ────────────────────────────────────────

@app.route("/api/overview")
def api_overview():
    today = datetime.now(_TZ).date()
    positions = get_open_positions()
    services = get_service_statuses()
    api_health = get_api_health()
    trade_stats = get_trade_stats()
    return jsonify(
        total_pnl=get_total_pnl(),
        daily_pnl=get_daily_pnl_today(today),
        position_count=len(positions),
        circuit_breaker=get_circuit_breaker_status(today),
        services=services,
        api_health=[
            {
                "api_name": h["api_name"],
                "status": h["status"],
                "latency_ms": h["latency_ms"],
                "error_message": h["error_message"],
            }
            for h in api_health
        ],
        trade_stats=trade_stats,
    )


@app.route("/api/charts")
def api_charts():
    raw = _chart_data()
    return jsonify(
        pnl_dates=json.loads(raw["pnl_dates"]),
        pnl_values=json.loads(raw["pnl_values"]),
        cumulative_values=json.loads(raw["cumulative_values"]),
        portfolio_dates=json.loads(raw["portfolio_dates"]),
        portfolio_values=json.loads(raw["portfolio_values"]),
        trade_dates=json.loads(raw["trade_dates"]),
        trade_counts=json.loads(raw["trade_counts"]),
    )


def _analysis_data(days) -> dict:
    """Build template variables for the /analysis page."""
    stats = get_analysis_stats(days)
    histogram = get_confidence_histogram(days)
    acted_on_rate = get_acted_on_rate_by_band(days)
    win_rate = get_win_rate_by_band(days)
    return dict(
        stats=stats,
        hist_labels=json.dumps([r["label"] for r in histogram]),
        hist_counts=json.dumps([int(r["count"]) for r in histogram]),
        acted_pcts=json.dumps([float(r["acted_pct"]) for r in acted_on_rate]),
        win_labels=json.dumps([r["label"] for r in win_rate]),
        win_rates=json.dumps([float(r["win_rate_pct"]) for r in win_rate]),
    )


@app.route("/analysis")
def analysis():
    days = 30
    return render_template("analysis.html", active_days=days, **_analysis_data(days))


@app.route("/api/analysis")
def api_analysis():
    raw = request.args.get("days", "30")
    if raw == "all":
        days = None
    elif raw in ("30", "90"):
        days = int(raw)
    else:
        days = 30
    stats = get_analysis_stats(days)
    histogram = get_confidence_histogram(days)
    acted_on_rate = get_acted_on_rate_by_band(days)
    win_rate = get_win_rate_by_band(days)
    return jsonify(
        stats=stats,
        hist_labels=[r["label"] for r in histogram],
        hist_counts=[int(r["count"]) for r in histogram],
        acted_pcts=[float(r["acted_pct"]) for r in acted_on_rate],
        win_labels=[r["label"] for r in win_rate],
        win_rates=[float(r["win_rate_pct"]) for r in win_rate],
    )


@app.route("/settings")
def settings():
    return render_template("settings.html", **{**get_ai_settings(), **get_ml_codegen_settings()})


@app.route("/api/settings/ai-provider", methods=["POST"])
def api_set_ai_provider():
    data = request.get_json(force=True) or {}
    provider = data.get("provider", "")
    model    = data.get("model", "")
    try:
        set_ai_provider(provider, model)
        return jsonify(ok=True, provider=provider, model=model)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.route("/api/settings/ml-codegen", methods=["POST"])
def api_settings_ml_codegen():
    data     = request.get_json() or {}
    provider = data.get("provider", "")
    model    = data.get("model", "")
    try:
        set_ml_codegen_provider(provider, model)
        return jsonify(ok=True, provider=provider, model=model)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


if __name__ == "__main__":
    log.info("Dashboard Service starting")
    app.run(host="0.0.0.0", port=8080)
