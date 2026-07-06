import json
from datetime import datetime, timezone
from typing import Optional

from shared.db import get_conn
from shared.redis_client import get_redis

CONFIDENCE_THRESHOLD = 0.65

_SIGNAL_STREAM_KEY = "stream:signals"
_SIGNAL_STREAM_MAXLEN = 1000


def write_decision(
    symbol: str,
    decision: str,
    confidence: float,
    reasoning: str,
    model: str,
    acted_on: bool,
    skip_reason: Optional[str],
) -> int:
    """
    Insert a Claude decision into the decisions table and return the new row ID.

    Called for every Claude AI decision — including hold decisions and
    decisions skipped due to low confidence. Returns the new row ID.

    Parameters:
        symbol: ticker symbol (e.g. "AAPL")
        decision: "buy", "sell", or "hold"
        confidence: Claude's stated confidence (0.0–1.0)
        reasoning: Claude's explanation
        model: Claude model ID used
        acted_on: True if a signal was published for this decision
        skip_reason: None if acted_on, otherwise a short explanation
    """
    sql = """
        INSERT INTO decisions
            (symbol, decision, confidence, reasoning, model, acted_on, skip_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, decision, confidence, reasoning, model, acted_on, skip_reason))
            return cur.fetchone()[0]


def write_signal(
    symbol: str,
    decision: str,
    confidence: float,
    decision_id: int,
) -> None:
    """
    Insert a trade signal into the signals table and publish to stream:signals.

    Only called when a decision is actionable (buy or sell, confidence >= 0.65).
    The Execution Service reads from stream:signals to place orders.
    """
    sql = """
        INSERT INTO signals (symbol, decision, confidence, decision_id)
        VALUES (%s, %s, %s, %s)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, decision, confidence, decision_id))

    payload = json.dumps({
        "symbol": symbol,
        "decision": decision,
        "confidence": confidence,
        "decision_id": decision_id,
        "published_at": datetime.now(timezone.utc).isoformat(),
    })
    r = get_redis()
    r.xadd(_SIGNAL_STREAM_KEY, {"data": payload}, maxlen=_SIGNAL_STREAM_MAXLEN)
