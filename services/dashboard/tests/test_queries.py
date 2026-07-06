import unittest
from unittest.mock import patch, MagicMock
from contextlib import contextmanager
from datetime import date, datetime, timezone

import psycopg2.extras

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
    get_ml_codegen_settings,
    set_ml_codegen_provider,
    get_available_models,
    _fetch_claude_models,
    _fetch_gemini_models,
    CLAUDE_MODELS,
    GEMINI_MODELS,
)


def _make_mock_conn(rows, fetchone_row=None):
    """Helper: mock psycopg2 connection whose cursor returns rows."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = rows
    mock_cur.fetchone.return_value = fetchone_row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


@contextmanager
def _make_mock_cm(mock_conn):
    yield mock_conn


class TestGetOpenPositions(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_empty_when_no_open_positions(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_open_positions()
        self.assertEqual(result, [])

    @patch("queries.get_conn")
    def test_returns_positions_list(self, mock_get_conn):
        rows = [
            {"symbol": "AAPL", "qty": 10, "price": "150.0000", "placed_at": datetime(2026, 5, 15, 9, 30, tzinfo=timezone.utc)},
            {"symbol": "MSFT", "qty": 5,  "price": "300.0000", "placed_at": datetime(2026, 5, 15, 9, 31, tzinfo=timezone.utc)},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_open_positions()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["symbol"], "AAPL")

    @patch("queries.get_conn")
    def test_uses_realdict_cursor(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        get_open_positions()
        call_kwargs = mock_conn.cursor.call_args[1]
        self.assertEqual(call_kwargs["cursor_factory"], psycopg2.extras.RealDictCursor)


class TestGetUnrealizedPnl(unittest.TestCase):
    def _make_redis(self, snapshot_map: dict):
        """Build a mock Redis that returns JSON snapshots keyed by symbol."""
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda key: (
            __import__("json").dumps(snapshot_map[key.replace("snapshot:", "")])
            if key.replace("snapshot:", "") in snapshot_map else None
        )
        return mock_redis

    @patch("queries.get_redis")
    @patch("queries.get_conn")
    def test_returns_zero_when_no_open_positions(self, mock_get_conn, mock_get_redis):
        mock_conn, _ = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        mock_get_redis.return_value = self._make_redis({})
        self.assertEqual(get_unrealized_pnl(), 0.0)

    @patch("queries.get_redis")
    @patch("queries.get_conn")
    def test_computes_unrealized_pnl_from_snapshot(self, mock_get_conn, mock_get_redis):
        positions = [{"symbol": "AAPL", "qty": 10, "price": 150.0, "placed_at": None}]
        mock_conn, _ = _make_mock_conn(positions)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        mock_get_redis.return_value = self._make_redis({"AAPL": {"price": 160.0}})
        # unrealized = (160 - 150) * 10 = 100
        self.assertAlmostEqual(get_unrealized_pnl(), 100.0)

    @patch("queries.get_redis")
    @patch("queries.get_conn")
    def test_skips_symbols_with_no_snapshot(self, mock_get_conn, mock_get_redis):
        positions = [
            {"symbol": "AAPL", "qty": 10, "price": 150.0, "placed_at": None},
            {"symbol": "TSLA", "qty": 5,  "price": 200.0, "placed_at": None},
        ]
        mock_conn, _ = _make_mock_conn(positions)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        # Only AAPL has a snapshot; TSLA is skipped
        mock_get_redis.return_value = self._make_redis({"AAPL": {"price": 160.0}})
        self.assertAlmostEqual(get_unrealized_pnl(), 100.0)

    @patch("queries.get_redis")
    @patch("queries.get_conn")
    def test_negative_unrealized_pnl(self, mock_get_conn, mock_get_redis):
        positions = [{"symbol": "AAPL", "qty": 10, "price": 150.0, "placed_at": None}]
        mock_conn, _ = _make_mock_conn(positions)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        mock_get_redis.return_value = self._make_redis({"AAPL": {"price": 140.0}})
        # unrealized = (140 - 150) * 10 = -100
        self.assertAlmostEqual(get_unrealized_pnl(), -100.0)


class TestGetAccountEquity(unittest.TestCase):
    @patch("queries.get_redis")
    def test_returns_none_when_key_missing(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis
        self.assertIsNone(get_account_equity())

    @patch("queries.get_redis")
    def test_returns_float_when_key_present(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.get.return_value = "99989.25"
        mock_get_redis.return_value = mock_redis
        self.assertAlmostEqual(get_account_equity(), 99989.25)

    @patch("queries.get_redis")
    def test_returns_none_on_invalid_value(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.get.return_value = "not-a-number"
        mock_get_redis.return_value = mock_redis
        self.assertIsNone(get_account_equity())


class TestGetTotalPnl(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_zero_when_no_rows(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={"total": None})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_total_pnl()
        self.assertEqual(result, 0.0)

    @patch("queries.get_conn")
    def test_returns_sum(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={"total": "1234.56"})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_total_pnl()
        self.assertAlmostEqual(result, 1234.56)


class TestGetDailyPnlToday(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_zero_when_no_row(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row=None)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_daily_pnl_today(date(2026, 5, 15))
        self.assertEqual(result, 0.0)

    @patch("queries.get_conn")
    def test_returns_todays_pnl(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(
            [], fetchone_row={"realized_pnl": "250.00", "circuit_breaker_triggered": False}
        )
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_daily_pnl_today(date(2026, 5, 15))
        self.assertAlmostEqual(result, 250.0)


class TestGetRecentTrades(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_list_of_dicts(self, mock_get_conn):
        rows = [
            {"id": 1, "symbol": "AAPL", "side": "buy", "qty": 10, "price": "150.0000",
             "status": "filled", "placed_at": datetime(2026, 5, 15, 9, 30, tzinfo=timezone.utc),
             "filled_at": datetime(2026, 5, 15, 9, 30, 5, tzinfo=timezone.utc)},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_recent_trades()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "AAPL")

    @patch("queries.get_conn")
    def test_passes_limit_to_query(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        get_recent_trades(limit=50)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(50, params)


class TestGetRecentDecisions(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_list_of_dicts(self, mock_get_conn):
        rows = [
            {"id": 1, "symbol": "AAPL", "decision": "buy", "confidence": "0.820",
             "reasoning": "Strong uptrend", "model": "claude-haiku",
             "acted_on": True, "skip_reason": None,
             "decided_at": datetime(2026, 5, 15, 9, 29, tzinfo=timezone.utc)},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_recent_decisions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "AAPL")

    @patch("queries.get_conn")
    def test_passes_limit_to_query(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        get_recent_decisions(limit=25)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(25, params)


class TestGetApiHealth(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_list_of_health_rows(self, mock_get_conn):
        rows = [
            {"api_name": "alpaca", "status": "ok", "latency_ms": 45,
             "checked_at": datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc), "error_message": None},
            {"api_name": "finnhub", "status": "warning", "latency_ms": 800,
             "checked_at": datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc), "error_message": "slow"},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_api_health()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["api_name"], "alpaca")

    @patch("queries.get_conn")
    def test_returns_empty_list_when_no_rows(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_api_health()
        self.assertEqual(result, [])


class TestGetWatchlist(unittest.TestCase):
    def _mock_redis(self, snapshots: dict):
        """Build a mock Redis that returns JSON snapshots for snapshot:<symbol> keys."""
        import json
        mock_r = MagicMock()
        mock_r.get.side_effect = lambda key: (
            json.dumps(snapshots[key.removeprefix("snapshot:")])
            if key.removeprefix("snapshot:") in snapshots else None
        )
        return mock_r

    @patch("queries.get_redis")
    @patch("queries.load_config")
    @patch("queries.get_conn")
    def test_returns_one_row_per_configured_symbol(self, mock_get_conn, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {"watchlist": ["AAPL", "MSFT"]}
        mock_get_redis.return_value = self._mock_redis({})
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_watchlist()
        self.assertEqual(len(result), 2)
        self.assertEqual([r["symbol"] for r in result], ["AAPL", "MSFT"])

    @patch("queries.get_redis")
    @patch("queries.load_config")
    @patch("queries.get_conn")
    def test_merges_snapshot_data(self, mock_get_conn, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {"watchlist": ["AAPL"]}
        mock_get_redis.return_value = self._mock_redis({
            "AAPL": {"symbol": "AAPL", "price": 175.5, "rsi": 52.3,
                     "sma20": 172.1, "sma50": 168.5, "timestamp": "2026-05-18T14:00:00Z"}
        })
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_watchlist()
        self.assertEqual(result[0]["price"], 175.5)
        self.assertEqual(result[0]["rsi"], 52.3)

    @patch("queries.get_redis")
    @patch("queries.load_config")
    @patch("queries.get_conn")
    def test_merges_decision_data(self, mock_get_conn, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {"watchlist": ["AAPL"]}
        mock_get_redis.return_value = self._mock_redis({})
        rows = [{"symbol": "AAPL", "decision": "buy", "confidence": "0.82",
                 "decided_at": datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc),
                 "acted_on": True, "skip_reason": None}]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_watchlist()
        self.assertEqual(result[0]["decision"], "buy")
        self.assertEqual(result[0]["acted_on"], True)

    @patch("queries.get_redis")
    @patch("queries.load_config")
    @patch("queries.get_conn")
    def test_symbol_with_no_data_has_none_fields(self, mock_get_conn, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {"watchlist": ["GOOGL"]}
        mock_get_redis.return_value = self._mock_redis({})
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_watchlist()
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["price"])
        self.assertIsNone(result[0]["decision"])

    @patch("queries.get_redis")
    @patch("queries.load_config")
    @patch("queries.get_conn")
    def test_empty_watchlist_returns_empty_list(self, mock_get_conn, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {"watchlist": []}
        mock_get_redis.return_value = self._mock_redis({})
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_watchlist()
        self.assertEqual(result, [])


class TestGetCircuitBreakerStatus(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_false_when_no_row(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row=None)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_circuit_breaker_status(date(2026, 5, 15))
        self.assertFalse(result)

    @patch("queries.get_conn")
    def test_returns_true_when_triggered(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={"circuit_breaker_triggered": True})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_circuit_breaker_status(date(2026, 5, 15))
        self.assertTrue(result)

    @patch("queries.get_conn")
    def test_returns_false_when_not_triggered(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={"circuit_breaker_triggered": False})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_circuit_breaker_status(date(2026, 5, 15))
        self.assertFalse(result)


class TestGetPnlHistory(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_empty_list_when_no_rows(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_pnl_history(30)
        self.assertEqual(result, [])

    @patch("queries.get_conn")
    def test_returns_rows_with_correct_keys(self, mock_get_conn):
        rows = [
            {"date": date(2026, 5, 14), "realized_pnl": "123.45"},
            {"date": date(2026, 5, 15), "realized_pnl": "-50.00"},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_pnl_history(30)
        self.assertEqual(len(result), 2)
        self.assertIn("date", result[0])
        self.assertIn("realized_pnl", result[0])
        self.assertEqual(result[0]["date"], date(2026, 5, 14))

    @patch("queries.get_conn")
    def test_passes_days_parameter_to_query(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        get_pnl_history(days=7)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(7, params)


class TestGetTradeActivity(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_empty_list_when_no_rows(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_trade_activity(30)
        self.assertEqual(result, [])

    @patch("queries.get_conn")
    def test_returns_rows_with_correct_keys(self, mock_get_conn):
        rows = [
            {"date": date(2026, 5, 14), "count": 5},
            {"date": date(2026, 5, 15), "count": 12},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_trade_activity(30)
        self.assertEqual(len(result), 2)
        self.assertIn("date", result[0])
        self.assertIn("count", result[0])
        self.assertEqual(result[1]["count"], 12)

    @patch("queries.get_conn")
    def test_passes_days_parameter_to_query(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        get_trade_activity(days=14)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(14, params)


class TestGetTradeStats(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_zeros_when_no_closed_trades(self, mock_get_conn):
        row = {
            "total_closed": 0, "wins": 0, "losses": 0,
            "win_rate_pct": None,
            "avg_pnl": "0.00", "best_trade": "0.00",
            "worst_trade": "0.00", "avg_holding_hours": "0.0",
        }
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row=row)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_trade_stats()
        self.assertEqual(result["total_closed"], 0)
        self.assertEqual(result["wins"], 0)
        self.assertAlmostEqual(result["win_rate_pct"], 0.0)
        self.assertAlmostEqual(result["avg_pnl"], 0.0)

    @patch("queries.get_conn")
    def test_win_rate_pct_is_float(self, mock_get_conn):
        row = {
            "total_closed": 4, "wins": 3, "losses": 1,
            "win_rate_pct": "75.0",
            "avg_pnl": "42.50", "best_trade": "120.00",
            "worst_trade": "-30.00", "avg_holding_hours": "18.5",
        }
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row=row)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_trade_stats()
        self.assertAlmostEqual(result["win_rate_pct"], 75.0)
        self.assertIsInstance(result["win_rate_pct"], float)

    @patch("queries.get_conn")
    def test_all_keys_present(self, mock_get_conn):
        row = {
            "total_closed": 1, "wins": 1, "losses": 0,
            "win_rate_pct": "100.0",
            "avg_pnl": "55.00", "best_trade": "55.00",
            "worst_trade": "55.00", "avg_holding_hours": "24.0",
        }
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row=row)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_trade_stats()
        expected_keys = {
            "total_closed", "wins", "losses", "win_rate_pct",
            "avg_pnl", "best_trade", "worst_trade", "avg_holding_hours",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    @patch("queries.get_conn")
    def test_negative_values_preserved(self, mock_get_conn):
        row = {
            "total_closed": 2, "wins": 0, "losses": 2,
            "win_rate_pct": "0.0",
            "avg_pnl": "-75.00", "best_trade": "-50.00",
            "worst_trade": "-100.00", "avg_holding_hours": "6.0",
        }
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row=row)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_trade_stats()
        self.assertAlmostEqual(result["worst_trade"], -100.0)
        self.assertAlmostEqual(result["avg_pnl"], -75.0)
        self.assertAlmostEqual(result["win_rate_pct"], 0.0)

    @patch("queries.get_conn")
    def test_uses_realdict_cursor(self, mock_get_conn):
        row = {
            "total_closed": 0, "wins": 0, "losses": 0,
            "win_rate_pct": None,
            "avg_pnl": "0.00", "best_trade": "0.00",
            "worst_trade": "0.00", "avg_holding_hours": "0.0",
        }
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row=row)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        get_trade_stats()
        call_kwargs = mock_conn.cursor.call_args[1]
        self.assertEqual(call_kwargs["cursor_factory"], psycopg2.extras.RealDictCursor)

    @patch("queries.get_conn")
    def test_break_even_trade_not_counted_as_loss(self, mock_get_conn):
        # A trade where sell_price == buy_price has pnl == 0
        # It must not be counted as a loss (pnl < 0 is the loss filter)
        row = {
            "total_closed": 1, "wins": 0, "losses": 0,
            "win_rate_pct": "0.0",
            "avg_pnl": "0.00", "best_trade": "0.00",
            "worst_trade": "0.00", "avg_holding_hours": "8.0",
        }
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row=row)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_trade_stats()
        self.assertEqual(result["total_closed"], 1)
        self.assertEqual(result["wins"], 0)
        self.assertEqual(result["losses"], 0)   # break-even ≠ loss
        self.assertAlmostEqual(result["win_rate_pct"], 0.0)


class TestGetAnalysisStats(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_zeros_when_no_decisions(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 0,
            "median_confidence": None,
            "pct_above_threshold": None,
            "pct_acted_on": None,
            "haiku_count": 0,
            "sonnet_count": 0,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_analysis_stats()
        self.assertEqual(result["total_decisions"], 0)
        self.assertEqual(result["median_confidence"], 0.0)
        self.assertEqual(result["pct_above_threshold"], 0.0)
        self.assertEqual(result["pct_acted_on"], 0.0)

    @patch("queries.get_conn")
    def test_all_keys_present(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 50,
            "median_confidence": "0.72",
            "pct_above_threshold": "68.0",
            "pct_acted_on": "45.0",
            "haiku_count": 40,
            "sonnet_count": 10,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_analysis_stats()
        self.assertEqual(
            set(result.keys()),
            {"total_decisions", "median_confidence", "pct_above_threshold",
             "pct_acted_on", "haiku_count", "sonnet_count"}
        )

    @patch("queries.get_conn")
    def test_median_confidence_is_float(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 10,
            "median_confidence": "0.71",
            "pct_above_threshold": "70.0",
            "pct_acted_on": "50.0",
            "haiku_count": 8,
            "sonnet_count": 2,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_analysis_stats()
        self.assertIsInstance(result["median_confidence"], float)

    @patch("queries.get_conn")
    def test_passes_days_param_when_provided(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 0, "median_confidence": None,
            "pct_above_threshold": None, "pct_acted_on": None,
            "haiku_count": 0, "sonnet_count": 0,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_analysis_stats(days=30)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(30, params)

    @patch("queries.get_conn")
    def test_no_params_when_days_is_none(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 0, "median_confidence": None,
            "pct_above_threshold": None, "pct_acted_on": None,
            "haiku_count": 0, "sonnet_count": 0,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_analysis_stats(days=None)
        params = mock_cur.execute.call_args[0][1]
        self.assertEqual(params, ())

    @patch("queries.get_conn")
    def test_haiku_and_sonnet_counts_are_ints(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 10,
            "median_confidence": "0.71",
            "pct_above_threshold": "70.0",
            "pct_acted_on": "50.0",
            "haiku_count": 8,
            "sonnet_count": 2,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_analysis_stats()
        self.assertIsInstance(result["haiku_count"], int)
        self.assertIsInstance(result["sonnet_count"], int)


class TestGetConfidenceHistogram(unittest.TestCase):
    def _make_20_rows(self):
        return [
            {"bucket": i, "label": f"{(i-1)*5}-{i*5}%", "count": 0}
            for i in range(1, 21)
        ]

    @patch("queries.get_conn")
    def test_always_returns_20_rows(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_confidence_histogram()
        self.assertEqual(len(result), 20)

    @patch("queries.get_conn")
    def test_bucket_13_label_is_60_65(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_confidence_histogram()
        # bucket 13 (index 12) covers 60-65%
        self.assertEqual(result[12]["label"], "60-65%")

    @patch("queries.get_conn")
    def test_bucket_14_label_is_65_70(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_confidence_histogram()
        self.assertEqual(result[13]["label"], "65-70%")

    @patch("queries.get_conn")
    def test_passes_days_param_when_provided(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_confidence_histogram(days=30)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(30, params)

    @patch("queries.get_conn")
    def test_no_params_when_days_is_none(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_confidence_histogram(days=None)
        params = mock_cur.execute.call_args[0][1]
        self.assertEqual(params, ())


class TestGetActedOnRateByBand(unittest.TestCase):
    def _make_20_rows(self):
        return [
            {"bucket": i, "label": f"{(i-1)*5}-{i*5}%", "total": 0, "acted": 0, "acted_pct": 0}
            for i in range(1, 21)
        ]

    @patch("queries.get_conn")
    def test_returns_20_rows(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_acted_on_rate_by_band()
        self.assertEqual(len(result), 20)

    @patch("queries.get_conn")
    def test_acted_pct_is_float(self, mock_get_conn):
        rows = [{"bucket": 14, "label": "65-70%", "total": 10, "acted": 7, "acted_pct": "70.0"}]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_acted_on_rate_by_band()
        self.assertIsInstance(result[0]["acted_pct"], float)

    @patch("queries.get_conn")
    def test_zero_total_gives_zero_pct(self, mock_get_conn):
        rows = [{"bucket": 5, "label": "20-25%", "total": 0, "acted": 0, "acted_pct": 0}]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_acted_on_rate_by_band()
        self.assertEqual(result[0]["acted_pct"], 0.0)

    @patch("queries.get_conn")
    def test_passes_days_param_when_provided(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_acted_on_rate_by_band(days=90)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(90, params)


class TestGetWinRateByBand(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_empty_list_when_no_closed_trades(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_win_rate_by_band()
        self.assertEqual(result, [])

    @patch("queries.get_conn")
    def test_returns_only_buckets_with_sample_data(self, mock_get_conn):
        rows = [
            {"bucket": 14, "label": "65-70%", "sample_size": 5, "wins": 3, "win_rate_pct": "60.0"},
            {"bucket": 15, "label": "70-75%", "sample_size": 3, "wins": 2, "win_rate_pct": "66.7"},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_win_rate_by_band()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["bucket"], 14)

    @patch("queries.get_conn")
    def test_win_rate_pct_is_float(self, mock_get_conn):
        rows = [{"bucket": 14, "label": "65-70%", "sample_size": 5, "wins": 3, "win_rate_pct": "60.0"}]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_win_rate_by_band()
        self.assertIsInstance(result[0]["win_rate_pct"], float)

    @patch("queries.get_conn")
    def test_passes_days_param_when_provided(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_win_rate_by_band(days=90)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(90, params)


class TestGetMlCodegenSettings(unittest.TestCase):
    @patch("queries.get_redis")
    @patch("queries.load_config")
    def test_returns_config_defaults_when_redis_empty(self, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {
            "ml": {"codegen_provider": "claude", "codegen_model": "claude-sonnet-4-5"}
        }
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        result = get_ml_codegen_settings()

        self.assertEqual(result["codegen_provider"], "claude")
        self.assertEqual(result["codegen_claude_model"], "claude-sonnet-4-5")
        self.assertIn("claude_models", result)
        self.assertIn("gemini_models", result)

    @patch("queries.get_redis")
    @patch("queries.load_config")
    def test_redis_values_override_config(self, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {"ml": {}}
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda key: {
            "config:ml_codegen_provider": b"gemini",
            "config:ml_codegen_claude_model": None,
            "config:ml_codegen_gemini_model": b"gemini-1.5-pro",
        }.get(key)
        mock_get_redis.return_value = mock_redis

        result = get_ml_codegen_settings()

        self.assertEqual(result["codegen_provider"], "gemini")
        self.assertEqual(result["codegen_gemini_model"], "gemini-1.5-pro")

    @patch("queries.get_redis")
    @patch("queries.load_config")
    def test_decodes_bytes_from_redis(self, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {"ml": {}}
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda key: {
            "config:ml_codegen_provider": b"claude",
            "config:ml_codegen_claude_model": b"claude-sonnet-4-5",
            "config:ml_codegen_gemini_model": None,
        }.get(key)
        mock_get_redis.return_value = mock_redis

        result = get_ml_codegen_settings()

        self.assertIsInstance(result["codegen_provider"], str)
        self.assertIsInstance(result["codegen_claude_model"], str)


_MOCK_AVAILABLE = {
    "claude": ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash-001"],
}


class TestSetMlCodegenProvider(unittest.TestCase):
    @patch("queries.get_available_models", return_value=_MOCK_AVAILABLE)
    @patch("queries.get_redis")
    def test_sets_claude_provider_and_model(self, mock_get_redis, _):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        set_ml_codegen_provider("claude", "claude-sonnet-4-6")

        mock_redis.set.assert_any_call("config:ml_codegen_provider", "claude")
        mock_redis.set.assert_any_call("config:ml_codegen_claude_model", "claude-sonnet-4-6")

    @patch("queries.get_available_models", return_value=_MOCK_AVAILABLE)
    @patch("queries.get_redis")
    def test_sets_gemini_provider_and_model(self, mock_get_redis, _):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        set_ml_codegen_provider("gemini", "gemini-2.5-flash")

        mock_redis.set.assert_any_call("config:ml_codegen_provider", "gemini")
        mock_redis.set.assert_any_call("config:ml_codegen_gemini_model", "gemini-2.5-flash")

    @patch("queries.get_available_models", return_value=_MOCK_AVAILABLE)
    @patch("queries.get_redis")
    def test_raises_on_unknown_provider(self, mock_get_redis, _):
        with self.assertRaises(ValueError):
            set_ml_codegen_provider("openai", "gpt-4")

    @patch("queries.get_available_models", return_value=_MOCK_AVAILABLE)
    @patch("queries.get_redis")
    def test_raises_on_unknown_claude_model(self, mock_get_redis, _):
        with self.assertRaises(ValueError):
            set_ml_codegen_provider("claude", "claude-opus-99")

    @patch("queries.get_available_models", return_value=_MOCK_AVAILABLE)
    @patch("queries.get_redis")
    def test_raises_on_unknown_gemini_model(self, mock_get_redis, _):
        with self.assertRaises(ValueError):
            set_ml_codegen_provider("gemini", "gemini-ultra-99")


class TestGetAvailableModels(unittest.TestCase):
    def _make_redis(self, cached_value=None):
        mock_r = MagicMock()
        mock_r.get.return_value = cached_value
        return mock_r

    @patch("queries.get_redis")
    def test_returns_cached_result_when_present(self, mock_get_redis):
        import json
        cached = {"claude": ["claude-haiku-4-5"], "gemini": ["gemini-2.5-flash"]}
        mock_get_redis.return_value = self._make_redis(json.dumps(cached).encode())

        result = get_available_models()

        self.assertEqual(result["claude"], ["claude-haiku-4-5"])
        self.assertEqual(result["gemini"], ["gemini-2.5-flash"])

    @patch("queries._fetch_gemini_models", return_value=["gemini-2.5-flash"])
    @patch("queries._fetch_claude_models", return_value=["claude-haiku-4-5", "claude-sonnet-4-6"])
    @patch("queries.get_redis")
    def test_fetches_from_apis_on_cache_miss(self, mock_get_redis, mock_claude, mock_gemini):
        mock_get_redis.return_value = self._make_redis(None)

        result = get_available_models()

        self.assertEqual(result["claude"], ["claude-haiku-4-5", "claude-sonnet-4-6"])
        self.assertEqual(result["gemini"], ["gemini-2.5-flash"])

    @patch("queries._fetch_gemini_models", return_value=["gemini-2.5-flash"])
    @patch("queries._fetch_claude_models", return_value=["claude-haiku-4-5"])
    @patch("queries.get_redis")
    def test_stores_result_in_redis_on_cache_miss(self, mock_get_redis, mock_claude, mock_gemini):
        import json
        mock_r = self._make_redis(None)
        mock_get_redis.return_value = mock_r

        result = get_available_models()

        mock_r.setex.assert_called_once()
        args = mock_r.setex.call_args[0]
        self.assertEqual(args[0], "cache:available_models")
        stored = json.loads(args[2])
        self.assertIn("claude", stored)
        self.assertIn("gemini", stored)

    @patch("queries.get_redis")
    def test_falls_back_to_hardcoded_on_corrupt_cache(self, mock_get_redis):
        mock_get_redis.return_value = self._make_redis(b"not-valid-json")

        with patch("queries._fetch_claude_models", return_value=CLAUDE_MODELS), \
             patch("queries._fetch_gemini_models", return_value=GEMINI_MODELS):
            result = get_available_models()

        self.assertEqual(result["claude"], CLAUDE_MODELS)


class TestFetchClaudeModels(unittest.TestCase):
    def test_returns_fallback_when_no_api_key(self):
        result = _fetch_claude_models("")
        self.assertEqual(result, CLAUDE_MODELS)

    def test_returns_api_models_sorted_newest_first(self):
        mock_model_a = MagicMock()
        mock_model_a.id = "claude-haiku-4-5"
        mock_model_b = MagicMock()
        mock_model_b.id = "claude-sonnet-4-6"
        mock_model_c = MagicMock()
        mock_model_c.id = "claude-opus-4-7"
        mock_client = MagicMock()
        mock_client.models.list.return_value = [mock_model_a, mock_model_b, mock_model_c]

        # anthropic is imported locally inside the function; patch at the package level
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = _fetch_claude_models("test-key")

        self.assertEqual(result[0], "claude-sonnet-4-6")  # sorted reverse alpha

    def test_filters_out_non_claude_models(self):
        mock_model_a = MagicMock()
        mock_model_a.id = "claude-haiku-4-5"
        mock_model_b = MagicMock()
        mock_model_b.id = "other-model-1"
        mock_client = MagicMock()
        mock_client.models.list.return_value = [mock_model_a, mock_model_b]

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = _fetch_claude_models("test-key")

        self.assertNotIn("other-model-1", result)

    def test_returns_fallback_on_api_exception(self):
        with patch("anthropic.Anthropic", side_effect=Exception("auth failed")):
            result = _fetch_claude_models("bad-key")
        self.assertEqual(result, CLAUDE_MODELS)


class TestFetchGeminiModels(unittest.TestCase):
    def test_returns_fallback_when_no_api_key(self):
        result = _fetch_gemini_models("")
        self.assertEqual(result, GEMINI_MODELS)

    def _make_gemini_model(self, name, methods=None):
        m = MagicMock()
        m.name = name
        m.supported_generation_methods = methods or ["generateContent"]
        return m

    def test_includes_stable_flash_and_pro_models(self):
        models = [
            self._make_gemini_model("models/gemini-2.5-flash"),
            self._make_gemini_model("models/gemini-2.5-pro"),
        ]
        # genai is imported as `import google.generativeai as genai` locally
        with patch("google.generativeai.configure"), \
             patch("google.generativeai.list_models", return_value=models):
            result = _fetch_gemini_models("test-key")
        self.assertIn("gemini-2.5-flash", result)
        self.assertIn("gemini-2.5-pro", result)

    def test_excludes_non_generate_content_models(self):
        # One valid model + one that only supports embedContent (not generateContent)
        models = [
            self._make_gemini_model("models/gemini-2.5-pro"),                           # valid
            self._make_gemini_model("models/gemini-2.5-flash", methods=["embedContent"]),  # excluded
        ]
        with patch("google.generativeai.configure"), \
             patch("google.generativeai.list_models", return_value=models):
            result = _fetch_gemini_models("test-key")
        self.assertIn("gemini-2.5-pro", result)
        self.assertNotIn("gemini-2.5-flash", result)

    def test_excludes_tts_and_image_models(self):
        models = [
            self._make_gemini_model("models/gemini-2.5-flash"),
            self._make_gemini_model("models/gemini-tts-1"),
            self._make_gemini_model("models/gemini-image-gen"),
        ]
        with patch("google.generativeai.configure"), \
             patch("google.generativeai.list_models", return_value=models):
            result = _fetch_gemini_models("test-key")
        self.assertIn("gemini-2.5-flash", result)
        self.assertNotIn("gemini-tts-1", result)
        self.assertNotIn("gemini-image-gen", result)

    def test_excludes_experimental_and_preview_models(self):
        models = [
            self._make_gemini_model("models/gemini-2.5-flash"),
            self._make_gemini_model("models/gemini-2.5-flash-preview-05-20"),
            self._make_gemini_model("models/gemini-2.5-flash-exp-1"),
        ]
        with patch("google.generativeai.configure"), \
             patch("google.generativeai.list_models", return_value=models):
            result = _fetch_gemini_models("test-key")
        self.assertIn("gemini-2.5-flash", result)
        self.assertNotIn("gemini-2.5-flash-preview-05-20", result)
        self.assertNotIn("gemini-2.5-flash-exp-1", result)

    def test_strips_models_prefix(self):
        models = [self._make_gemini_model("models/gemini-2.5-flash")]
        with patch("google.generativeai.configure"), \
             patch("google.generativeai.list_models", return_value=models):
            result = _fetch_gemini_models("test-key")
        self.assertTrue(all(not m.startswith("models/") for m in result))

    def test_returns_fallback_on_api_exception(self):
        with patch("google.generativeai.configure", side_effect=Exception("auth failed")):
            result = _fetch_gemini_models("bad-key")
        self.assertEqual(result, GEMINI_MODELS)

    def test_returns_fallback_when_no_models_pass_filter(self):
        models = [self._make_gemini_model("models/gemini-tts-pro")]
        with patch("google.generativeai.configure"), \
             patch("google.generativeai.list_models", return_value=models):
            result = _fetch_gemini_models("test-key")
        self.assertEqual(result, GEMINI_MODELS)


if __name__ == "__main__":
    unittest.main()
