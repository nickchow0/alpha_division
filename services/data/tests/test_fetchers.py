import pytest
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from fetchers import fetch_bars, fetch_news, fetch_macro, fetch_latest_price


# ---------------------------------------------------------------------------
# fetch_bars tests
# ---------------------------------------------------------------------------

def _make_mock_api(df: pd.DataFrame):
    """Build a mock tradeapi.REST whose get_bars().df returns the given DataFrame."""
    mock_bars_resp = MagicMock()
    mock_bars_resp.df = df
    mock_api = MagicMock()
    mock_api.get_bars.return_value = mock_bars_resp
    return mock_api


def _sample_bars_df() -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=250, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(250)],
            "high": [101.0 + i for i in range(250)],
            "low": [99.0 + i for i in range(250)],
            "close": [100.5 + i for i in range(250)],
            "volume": [1_000_000] * 250,
        },
        index=index,
    )


def test_fetch_bars_returns_list_of_dicts():
    mock_api = _make_mock_api(_sample_bars_df())
    with patch("fetchers.tradeapi.REST", return_value=mock_api):
        result = fetch_bars("AAPL", "key", "secret", "https://paper-api.alpaca.markets")
    assert isinstance(result, list)
    assert len(result) == 250


def test_fetch_bars_dicts_have_required_keys():
    mock_api = _make_mock_api(_sample_bars_df())
    with patch("fetchers.tradeapi.REST", return_value=mock_api):
        result = fetch_bars("AAPL", "key", "secret", "https://paper-api.alpaca.markets")
    for bar in result:
        for key in ("t", "o", "h", "l", "c", "v"):
            assert key in bar, f"Missing key '{key}' in bar: {bar}"


def test_fetch_bars_raises_on_empty_dataframe():
    empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    mock_api = _make_mock_api(empty_df)
    with patch("fetchers.tradeapi.REST", return_value=mock_api):
        with pytest.raises(ValueError, match="No bars returned"):
            fetch_bars("AAPL", "key", "secret", "https://paper-api.alpaca.markets")


def test_fetch_bars_passes_correct_symbol_and_start_date():
    mock_api = _make_mock_api(_sample_bars_df())
    with patch("fetchers.tradeapi.REST", return_value=mock_api):
        fetch_bars("TSLA", "key", "secret", "https://paper-api.alpaca.markets")
    call_kwargs = mock_api.get_bars.call_args
    assert call_kwargs[0][0] == "TSLA"
    assert call_kwargs[0][1] == "1Day"
    assert "start" in call_kwargs[1], "start date must be passed to get_bars"
    assert call_kwargs[1]["limit"] == 250


# ---------------------------------------------------------------------------
# fetch_news tests
# ---------------------------------------------------------------------------

def _make_news_response(articles: list[dict]):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = articles
    return mock_resp


def _article(hours_ago: int, headline: str = "Test headline") -> dict:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "headline": headline,
        "datetime": int(dt.timestamp()),
        "source": "TestSource",
        "url": "https://example.com/news",
        "summary": "A test article.",
    }


def test_fetch_news_returns_list():
    articles = [_article(1), _article(2), _article(3)]
    with patch("fetchers.requests.get", return_value=_make_news_response(articles)):
        result = fetch_news("AAPL", "token123")
    assert isinstance(result, list)


def test_fetch_news_returns_at_most_5_items():
    articles = [_article(i + 1) for i in range(10)]
    with patch("fetchers.requests.get", return_value=_make_news_response(articles)):
        result = fetch_news("AAPL", "token123")
    assert len(result) <= 5


def test_fetch_news_filters_older_than_24h():
    articles = [
        _article(1, "Recent"),
        _article(25, "Too old"),
        _article(23, "Just within"),
    ]
    with patch("fetchers.requests.get", return_value=_make_news_response(articles)):
        result = fetch_news("AAPL", "token123")
    headlines = [a["headline"] for a in result]
    assert "Too old" not in headlines
    assert "Recent" in headlines
    assert "Just within" in headlines


def test_fetch_news_returns_empty_list_when_no_recent_articles():
    articles = [_article(30), _article(48)]
    with patch("fetchers.requests.get", return_value=_make_news_response(articles)):
        result = fetch_news("AAPL", "token123")
    assert result == []


def test_fetch_news_includes_expected_fields():
    articles = [_article(1, "Breaking news")]
    with patch("fetchers.requests.get", return_value=_make_news_response(articles)):
        result = fetch_news("AAPL", "token123")
    assert len(result) == 1
    assert result[0]["headline"] == "Breaking news"
    assert "datetime" in result[0]


# ---------------------------------------------------------------------------
# fetch_macro tests
# ---------------------------------------------------------------------------

def _make_fred_response(value: str):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "observations": [{"date": "2026-04-01", "value": value}]
    }
    return mock_resp


def _make_fred_empty_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"observations": []}
    return mock_resp


def test_fetch_macro_returns_dict_with_required_keys():
    fed_resp = _make_fred_response("5.33")
    cpi_resp = _make_fred_response("314.5")
    with patch("fetchers.requests.get", side_effect=[fed_resp, cpi_resp]):
        result = fetch_macro("fredkey")
    assert "fed_funds_rate" in result
    assert "cpi" in result


def test_fetch_macro_parses_float_values():
    fed_resp = _make_fred_response("5.33")
    cpi_resp = _make_fred_response("314.5")
    with patch("fetchers.requests.get", side_effect=[fed_resp, cpi_resp]):
        result = fetch_macro("fredkey")
    assert result["fed_funds_rate"] == pytest.approx(5.33)
    assert result["cpi"] == pytest.approx(314.5)


def test_fetch_macro_raises_on_empty_observations():
    fed_resp = _make_fred_empty_response()
    cpi_resp = _make_fred_response("314.5")
    with patch("fetchers.requests.get", side_effect=[fed_resp, cpi_resp]):
        with pytest.raises(ValueError, match="No observations"):
            fetch_macro("fredkey")


def test_fetch_macro_raises_on_empty_cpi_observations():
    fed_resp = _make_fred_response("5.33")
    cpi_empty = _make_fred_empty_response()
    with patch("fetchers.requests.get", side_effect=[fed_resp, cpi_empty]):
        with pytest.raises(ValueError, match="No observations"):
            fetch_macro("fredkey")


def test_fetch_macro_raises_on_fred_dot_sentinel():
    fed_dot = _make_fred_response(".")
    cpi_resp = _make_fred_response("314.5")
    with patch("fetchers.requests.get", side_effect=[fed_dot, cpi_resp]):
        with pytest.raises(ValueError, match="missing value"):
            fetch_macro("fredkey")


# ---------------------------------------------------------------------------
# fetch_latest_price tests
# ---------------------------------------------------------------------------

def _make_mock_api_with_trade(price: float):
    mock_trade = MagicMock()
    mock_trade.price = price
    mock_api = MagicMock()
    mock_api.get_latest_trade.return_value = mock_trade
    return mock_api


def test_fetch_latest_price_returns_float():
    mock_api = _make_mock_api_with_trade(175.42)
    with patch("fetchers.tradeapi.REST", return_value=mock_api):
        result = fetch_latest_price("AAPL", "key", "secret", "https://paper-api.alpaca.markets")
    assert result == pytest.approx(175.42)


def test_fetch_latest_price_calls_correct_symbol():
    mock_api = _make_mock_api_with_trade(50.0)
    with patch("fetchers.tradeapi.REST", return_value=mock_api):
        fetch_latest_price("TSLA", "key", "secret", "https://paper-api.alpaca.markets")
    mock_api.get_latest_trade.assert_called_once_with("TSLA")


def test_fetch_latest_price_raises_when_no_trade():
    mock_api = MagicMock()
    mock_api.get_latest_trade.return_value = None
    with patch("fetchers.tradeapi.REST", return_value=mock_api):
        with pytest.raises(ValueError, match="No latest trade"):
            fetch_latest_price("AAPL", "key", "secret", "https://paper-api.alpaca.markets")
