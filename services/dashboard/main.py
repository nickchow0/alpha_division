import sys
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app")

from flask import Flask, render_template, jsonify
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
)
from service_status import get_service_statuses

log = get_logger("dashboard")

_ET = ZoneInfo("America/New_York")

app = Flask(__name__)


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def _chart_data(days: int = 30) -> dict:
    """Build JSON-encoded chart data shared by overview and charts routes."""
    pnl_history = get_pnl_history(days)
    trade_activity = get_trade_activity(days)
    cumulative, running = [], 0.0
    for row in pnl_history:
        running += float(row["realized_pnl"])
        cumulative.append(round(running, 2))
    return dict(
        pnl_dates=json.dumps([str(r["date"]) for r in pnl_history]),
        pnl_values=json.dumps([float(r["realized_pnl"]) for r in pnl_history]),
        cumulative_values=json.dumps(cumulative),
        trade_dates=json.dumps([str(r["date"]) for r in trade_activity]),
        trade_counts=json.dumps([int(r["count"]) for r in trade_activity]),
    )


@app.route("/")
def overview():
    today = datetime.now(_ET).date()
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
    today = datetime.now(_ET).date()
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
        trade_dates=json.loads(raw["trade_dates"]),
        trade_counts=json.loads(raw["trade_counts"]),
    )


if __name__ == "__main__":
    log.info("Dashboard Service starting")
    app.run(host="0.0.0.0", port=8080)
