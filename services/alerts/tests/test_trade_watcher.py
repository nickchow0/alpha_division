import unittest
from unittest.mock import patch, MagicMock, call
from contextlib import contextmanager

from trade_watcher import (
    get_last_seen_trade_id,
    set_last_seen_trade_id,
    get_new_trades,
    check_new_trades,
)


def _make_mock_conn(rows):
    """Helper: returns a mock psycopg2 connection whose cursor fetchall returns rows."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


@contextmanager
def _make_mock_cm(mock_conn):
    """Helper: context manager that yields the mock connection."""
    yield mock_conn


class TestGetLastSeenTradeId(unittest.TestCase):
    @patch("trade_watcher.get_redis")
    def test_returns_none_when_key_missing(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        result = get_last_seen_trade_id()
        self.assertIsNone(result)
        mock_redis.get.assert_called_once_with("alert:last_trade_id")

    @patch("trade_watcher.get_redis")
    def test_returns_int_when_key_exists(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.get.return_value = "42"
        mock_get_redis.return_value = mock_redis

        result = get_last_seen_trade_id()
        self.assertEqual(result, 42)


class TestSetLastSeenTradeId(unittest.TestCase):
    @patch("trade_watcher.get_redis")
    def test_sets_key(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        set_last_seen_trade_id(99)
        mock_redis.set.assert_called_once_with("alert:last_trade_id", 99)


class TestGetNewTrades(unittest.TestCase):
    @patch("trade_watcher.get_conn")
    def test_queries_all_when_no_last_id(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_new_trades(None)

        sql = mock_cur.execute.call_args[0][0]
        self.assertIn("ORDER BY id ASC", sql)
        self.assertNotIn(">", sql)
        self.assertEqual(result, [])

    @patch("trade_watcher.get_conn")
    def test_queries_since_last_id(self, mock_get_conn):
        rows = [
            {"id": 5, "symbol": "AAPL", "side": "buy", "qty": 10, "price": 150.0},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_new_trades(4)

        sql = mock_cur.execute.call_args[0][0]
        params = mock_cur.execute.call_args[0][1]
        self.assertIn("id >", sql)
        self.assertEqual(params, (4,))
        self.assertEqual(result, rows)

    @patch("trade_watcher.get_conn")
    def test_returns_list_of_dicts(self, mock_get_conn):
        rows = [
            {"id": 1, "symbol": "TSLA", "side": "sell", "qty": 5, "price": 200.0},
            {"id": 2, "symbol": "MSFT", "side": "buy", "qty": 3, "price": 300.0},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_new_trades(0)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["symbol"], "TSLA")


class TestCheckNewTrades(unittest.TestCase):
    @patch("trade_watcher.set_last_seen_trade_id")
    @patch("trade_watcher.get_new_trades")
    @patch("trade_watcher.get_last_seen_trade_id")
    def test_no_new_trades_does_nothing(self, mock_last, mock_new, mock_set):
        mock_last.return_value = 10
        mock_new.return_value = []
        mock_discord = MagicMock()

        check_new_trades("https://hook", mock_discord)

        mock_discord.assert_not_called()
        mock_set.assert_not_called()

    @patch("trade_watcher.set_last_seen_trade_id")
    @patch("trade_watcher.get_new_trades")
    @patch("trade_watcher.get_last_seen_trade_id")
    def test_sends_discord_for_each_trade(self, mock_last, mock_new, mock_set):
        mock_last.return_value = 5
        mock_new.return_value = [
            {"id": 6, "symbol": "AAPL", "side": "buy", "qty": 10, "price": 150.0},
            {"id": 7, "symbol": "TSLA", "side": "sell", "qty": 5, "price": 200.0},
        ]
        mock_discord = MagicMock()

        check_new_trades("https://hook", mock_discord)

        self.assertEqual(mock_discord.call_count, 2)
        mock_set.assert_has_calls([call(6), call(7)])

    @patch("trade_watcher.set_last_seen_trade_id")
    @patch("trade_watcher.get_new_trades")
    @patch("trade_watcher.get_last_seen_trade_id")
    def test_advances_last_id_even_on_discord_failure(self, mock_last, mock_new, mock_set):
        mock_last.return_value = 0
        mock_new.return_value = [
            {"id": 1, "symbol": "AAPL", "side": "buy", "qty": 10, "price": 150.0},
        ]
        mock_discord = MagicMock(side_effect=Exception("Discord down"))

        # Should not raise
        check_new_trades("https://hook", mock_discord)

        # ID still advanced
        mock_set.assert_called_once_with(1)

    @patch("trade_watcher.set_last_seen_trade_id")
    @patch("trade_watcher.get_new_trades")
    @patch("trade_watcher.get_last_seen_trade_id")
    def test_message_contains_trade_details(self, mock_last, mock_new, mock_set):
        mock_last.return_value = 0
        mock_new.return_value = [
            {"id": 1, "symbol": "AAPL", "side": "buy", "qty": 10, "price": 150.0},
        ]
        mock_discord = MagicMock()

        check_new_trades("https://hook", mock_discord)

        msg = mock_discord.call_args[0][1]
        self.assertIn("AAPL", msg)
        self.assertIn("BUY", msg)
        self.assertIn("10", msg)
        self.assertIn("150", msg)


if __name__ == "__main__":
    unittest.main()
