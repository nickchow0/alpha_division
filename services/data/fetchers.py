import requests
import alpaca_trade_api as tradeapi
from datetime import datetime, timezone, timedelta


def fetch_bars(symbol: str, api_key: str, secret_key: str, base_url: str) -> list[dict]:
    """
    Fetch 60 daily OHLCV bars for the given symbol from Alpaca.

    Passes an explicit start date (90 calendar days ago) so Alpaca returns
    a full window of daily bars rather than only today's incomplete bar.

    Returns a list of dicts with keys: t, o, h, l, c, v.
    Raises ValueError if Alpaca returns no bars.
    """
    api = tradeapi.REST(api_key, secret_key, base_url)
    start = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    bars_resp = api.get_bars(symbol, "1Day", start=start, limit=60)
    df = bars_resp.df

    if df.empty:
        raise ValueError(f"No bars returned for {symbol}")

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


def fetch_news(symbol: str, finnhub_token: str) -> list[dict]:
    """
    Fetch up to 5 news articles published within the last 24 hours for the given symbol
    from the Finnhub company-news endpoint.

    Returns a list of article dicts (raw Finnhub response fields).
    """
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)
    date_from = yesterday.strftime("%Y-%m-%d")
    date_to = now.strftime("%Y-%m-%d")

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": symbol,
        "from": date_from,
        "to": date_to,
        "token": finnhub_token,
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    articles = response.json()

    cutoff_ts = yesterday.timestamp()
    recent = [a for a in articles if a.get("datetime", 0) >= cutoff_ts]
    return recent[:5]


def fetch_macro(fred_api_key: str) -> dict:
    """
    Fetch the latest Fed funds rate (FEDFUNDS) and CPI (CPIAUCSL) from FRED.

    Returns a dict with keys: fed_funds_rate (float), cpi (float).
    Raises ValueError if either series returns no observations.
    """

    def _fetch_series(series_id: str) -> float:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": fred_api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        observations = data.get("observations", [])
        if not observations:
            raise ValueError(f"No observations returned for FRED series {series_id}")
        raw = observations[0]["value"]
        if raw == ".":
            raise ValueError(f"FRED series {series_id} returned a missing value ('.')")
        return float(raw)

    fed_funds_rate = _fetch_series("FEDFUNDS")
    cpi = _fetch_series("CPIAUCSL")

    return {"fed_funds_rate": fed_funds_rate, "cpi": cpi}
