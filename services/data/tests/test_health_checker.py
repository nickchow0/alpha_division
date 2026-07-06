import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from health_checker import write_health_result, check_all, check_ai_api


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_conn():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


def _make_mock_cm(mock_conn):
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


def _sample_bars_df():
    index = pd.date_range("2026-01-01", periods=1, freq="D", tz="UTC")
    return pd.DataFrame(
        {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [1_000_000]},
        index=index,
    )


def _make_alpaca_api(df):
    mock_bars_resp = MagicMock()
    mock_bars_resp.df = df
    mock_api = MagicMock()
    mock_api.get_bars.return_value = mock_bars_resp
    return mock_api


def _make_requests_ok_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = [{"headline": "test", "datetime": 1715000000}]
    return mock_resp


def _make_fred_ok_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"observations": [{"date": "2026-01-01", "value": "5.33"}]}
    return mock_resp


# ---------------------------------------------------------------------------
# write_health_result tests
# ---------------------------------------------------------------------------

def test_write_health_result_executes_insert():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cm = _make_mock_cm(mock_conn)

    with patch("health_checker.get_conn", return_value=mock_cm):
        write_health_result("alpaca", "ok", 150, None)

    mock_cursor.execute.assert_called_once()
    sql, params = mock_cursor.execute.call_args[0]
    assert "INSERT INTO api_health" in sql
    assert params == ("alpaca", "ok", 150, None)


def test_write_health_result_includes_latency():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cm = _make_mock_cm(mock_conn)

    with patch("health_checker.get_conn", return_value=mock_cm):
        write_health_result("alpaca", "ok", 200, None)

    _, params = mock_cursor.execute.call_args[0]
    assert params == ("alpaca", "ok", 200, None)


def test_write_health_result_includes_error_message():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cm = _make_mock_cm(mock_conn)

    with patch("health_checker.get_conn", return_value=mock_cm):
        write_health_result("alpaca", "error", 0, "Connection refused")

    _, params = mock_cursor.execute.call_args[0]
    assert params == ("alpaca", "error", 0, "Connection refused")


# ---------------------------------------------------------------------------
# check_all tests
# ---------------------------------------------------------------------------

def _make_redis(provider="claude"):
    mock_r = MagicMock()
    mock_r.get.return_value = provider.encode()
    return mock_r


def test_check_all_returns_ok_when_all_succeed():
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.get_redis", return_value=_make_redis()), \
         patch("health_checker.check_ai_api", return_value="ok"), \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets",
                           "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey")

    assert result["alpaca"] == "ok"
    assert result["finnhub"] == "ok"
    assert result["fred"] == "ok"
    assert result["claude"] == "ok"


def test_check_all_checks_gemini_when_active():
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    mock_r = MagicMock()
    mock_r.get.side_effect = lambda key: {
        "config:ai_provider": b"gemini",
        "config:gemini_model": b"gemini-2.5-flash",
    }.get(key)

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.get_redis", return_value=mock_r), \
         patch("health_checker.check_ai_api", return_value="ok") as mock_ai, \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets",
                           "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey")

    assert "gemini" in result
    mock_ai.assert_called_once_with("gemini", "gkey", model="gemini-2.5-flash")


def test_check_all_passes_model_from_redis_to_check_ai_api():
    """check_all reads the model from Redis and forwards it to check_ai_api."""
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    mock_r = MagicMock()
    mock_r.get.side_effect = lambda key: {
        "config:ai_provider": b"claude",
        "config:claude_model": b"claude-opus-4-7",
    }.get(key)

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.get_redis", return_value=mock_r), \
         patch("health_checker.check_ai_api", return_value="ok") as mock_ai, \
         patch("health_checker.write_health_result"):
        check_all("key", "secret", "https://paper-api.alpaca.markets",
                  "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey")

    mock_ai.assert_called_once_with("claude", "akey", model="claude-opus-4-7")


def test_check_all_uses_empty_model_when_redis_has_no_model_key():
    """Falls back to empty string model when model key absent from Redis."""
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    mock_r = MagicMock()
    mock_r.get.side_effect = lambda key: {
        "config:ai_provider": b"claude",
    }.get(key)  # model key absent → returns None

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.get_redis", return_value=mock_r), \
         patch("health_checker.check_ai_api", return_value="ok") as mock_ai, \
         patch("health_checker.write_health_result"):
        check_all("key", "secret", "https://paper-api.alpaca.markets",
                  "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey")

    mock_ai.assert_called_once_with("claude", "akey", model="")


def test_check_all_returns_error_for_alpaca_on_exception():
    mock_api = MagicMock()
    mock_api.get_bars.side_effect = Exception("down")
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.get_redis", return_value=_make_redis()), \
         patch("health_checker.check_ai_api", return_value="ok"), \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets",
                           "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey")

    assert result["alpaca"] == "error"


def test_check_all_returns_warning_for_finnhub_on_exception():
    mock_api = _make_alpaca_api(_sample_bars_df())
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[Exception("finnhub down"), fred_resp]), \
         patch("health_checker.get_redis", return_value=_make_redis()), \
         patch("health_checker.check_ai_api", return_value="ok"), \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets",
                           "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey")

    assert result["finnhub"] == "warning"


def test_check_all_returns_warning_for_fred_on_exception():
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, Exception("fred down")]), \
         patch("health_checker.get_redis", return_value=_make_redis()), \
         patch("health_checker.check_ai_api", return_value="ok"), \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets",
                           "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey")

    assert result["fred"] == "warning"


def test_check_all_returns_warning_for_ai_on_exception():
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.get_redis", return_value=_make_redis()), \
         patch("health_checker.check_ai_api", side_effect=Exception("auth failed")), \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets",
                           "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey")

    assert result["claude"] == "warning"


def test_check_all_calls_write_health_result_four_times():
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.get_redis", return_value=_make_redis()), \
         patch("health_checker.check_ai_api", return_value="ok"), \
         patch("health_checker.write_health_result") as mock_write:
        check_all("key", "secret", "https://paper-api.alpaca.markets",
                  "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey",
                  check_ai=True)

    assert mock_write.call_count == 4


def test_check_all_skips_ai_when_check_ai_false():
    """check_ai=False skips the AI check — only alpaca, finnhub, fred are written."""
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.get_redis", return_value=_make_redis()), \
         patch("health_checker.check_ai_api") as mock_ai, \
         patch("health_checker.write_health_result") as mock_write:
        result = check_all("key", "secret", "https://paper-api.alpaca.markets",
                           "ftoken", "fredkey", anthropic_api_key="akey", gemini_api_key="gkey",
                           check_ai=False)

    mock_ai.assert_not_called()
    assert mock_write.call_count == 3
    assert "claude" not in result
    assert "gemini" not in result


# ---------------------------------------------------------------------------
# check_ai_api tests
# ---------------------------------------------------------------------------

def test_check_ai_api_claude_calls_anthropic():
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock()
    with patch("health_checker.anthropic.Anthropic", return_value=mock_client):
        result = check_ai_api("claude", "test-key")
    assert result == "ok"
    mock_client.messages.create.assert_called_once()


def test_check_ai_api_gemini_calls_genai():
    mock_model = MagicMock()
    mock_model.generate_content.return_value = MagicMock()
    with patch("health_checker.genai.configure"), \
         patch("health_checker.genai.GenerativeModel", return_value=mock_model):
        result = check_ai_api("gemini", "test-key")
    assert result == "ok"
    mock_model.generate_content.assert_called_once()


def test_check_ai_api_raises_on_unknown_provider():
    with pytest.raises(ValueError, match="Unknown AI provider"):
        check_ai_api("gpt4", "test-key")


def test_check_ai_api_claude_forwards_model_to_create():
    mock_client = MagicMock()
    with patch("health_checker.anthropic.Anthropic", return_value=mock_client):
        check_ai_api("claude", "test-key", model="claude-opus-4-7")
    _, kwargs = mock_client.messages.create.call_args
    assert kwargs["model"] == "claude-opus-4-7"


def test_check_ai_api_claude_uses_fallback_when_model_empty():
    mock_client = MagicMock()
    with patch("health_checker.anthropic.Anthropic", return_value=mock_client):
        check_ai_api("claude", "test-key", model="")
    _, kwargs = mock_client.messages.create.call_args
    assert kwargs["model"] == "claude-haiku-4-5"


def test_check_ai_api_gemini_forwards_model_to_generative_model():
    mock_model = MagicMock()
    with patch("health_checker.genai.configure"), \
         patch("health_checker.genai.GenerativeModel", return_value=mock_model) as mock_gm:
        check_ai_api("gemini", "test-key", model="gemini-2.5-pro")
    mock_gm.assert_called_once_with("gemini-2.5-pro")


def test_check_ai_api_gemini_uses_fallback_when_model_empty():
    mock_model = MagicMock()
    with patch("health_checker.genai.configure"), \
         patch("health_checker.genai.GenerativeModel", return_value=mock_model) as mock_gm:
        check_ai_api("gemini", "test-key", model="")
    mock_gm.assert_called_once_with("gemini-2.5-flash")
