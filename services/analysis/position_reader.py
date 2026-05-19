from shared.db import get_conn


def get_open_position_symbols() -> set:
    """
    Return the set of symbols currently held (open buy positions).

    A symbol is held if its most recent filled trade is a buy with no
    subsequent filled sell. Matches the dashboard's open-position logic.

    Returns an empty set if no positions are open or on DB error.
    """
    sql = """
        SELECT symbol
        FROM (
            SELECT DISTINCT ON (symbol)
                symbol, side
            FROM trades
            WHERE status = 'filled'
            ORDER BY symbol, placed_at DESC
        ) latest
        WHERE side = 'buy'
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return {row[0] for row in cur.fetchall()}
    except Exception:
        return set()
