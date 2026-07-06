import requests
import alpaca_trade_api as tradeapi
from datetime import datetime, timezone, timedelta


def fetch_account_equity(api_key: str, secret_key: str, base_url: str) -> float:
    """
    Fetch the current paper-trading account equity from Alpaca.

    Returns the float equity value (cash + market value of open positions at
    real-time prices) as reported by Alpaca.  This is the authoritative number
    — use it instead of reconstructing from snapshot prices.
    """
    api = tradeapi.REST(api_key, secret_key, base_url)
    account = api.get_account()
    return float(account.equity)


def fetch_latest_price(symbol: str, api_key: str, secret_key: str, base_url: str) -> float:
    """
    Fetch the live last-trade price for a symbol from Alpaca.

    Uses get_latest_trade() which reflects the most recent transaction price
    during market hours — more accurate than the previous daily bar close.

    Raises ValueError if no trade data is returned.
    """
    api = tradeapi.REST(api_key, secret_key, base_url)
    trade = api.get_latest_trade(symbol)
    if trade is None:
        raise ValueError(f"No latest trade returned for {symbol}")
    return float(trade.price)


def fetch_bars(symbol: str, api_key: str, secret_key: str, base_url: str) -> list[dict]:
    """
    Fetch 250 daily OHLCV bars for the given symbol from Alpaca.

    Passes an explicit start date (400 calendar days ago) so Alpaca returns
    enough bars for SMA200 and 52-week range indicators.

    Returns a list of dicts with keys: t, o, h, l, c, v.
    Raises ValueError if Alpaca returns no bars.
    """
    api = tradeapi.REST(api_key, secret_key, base_url)
    start = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    bars_resp = api.get_bars(symbol, "1Day", start=start, limit=250)
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
