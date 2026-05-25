# services/research/data.py
from datetime import date as Date

import alpaca_trade_api as tradeapi


def fetch_bars_alpaca(
    symbol: str,
    start_date: Date,
    end_date: Date,
    api_key: str,
    secret_key: str,
    base_url: str,
) -> list[dict]:
    """
    Fetch daily OHLCV bars from Alpaca historical API.
    Returns list of dicts with keys: t, o, h, l, c, v.
    Raises ValueError if no bars are returned.
    """
    api = tradeapi.REST(api_key, secret_key, base_url)
    bars_resp = api.get_bars(
        symbol,
        "1Day",
        start=start_date.isoformat(),
        end=end_date.isoformat(),
        limit=10000,
    )
    df = bars_resp.df

    if df.empty:
        raise ValueError(f"No bars returned for {symbol} from Alpaca")

    result = []
    for ts, row in df.iterrows():
        result.append({
            "t": str(ts),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "v": int(row["volume"]),
        })
    return result
