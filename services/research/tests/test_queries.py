# services/research/tests/test_queries.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch, MagicMock, call

from queries import (
    save_strategy,
    get_strategies,
    save_backtest_run,
    save_backtest_trades,
    get_strategy_runs,
    update_strategy_status,
    get_candidates,
    get_run_trades,
)


def _make_mock_conn(rows=None, fetchone_row=None):
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = rows or []
    mock_cur.fetchone.return_value = fetchone_row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


@contextmanager
def _make_mock_cm(mock_conn):
    yield mock_conn


class TestSaveStrategy(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_new_id(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(fetchone_row={"id": 42})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = save_strategy(
            name="Test", description="desc", hypothesis="hyp",
            code="def generate_signal(s): pass",
            code_hash="abc123", triggered_by="manual"
        )
        self.assertEqual(result, 42)

    @patch("queries.get_conn")
    def test_executes_insert_with_draft_status(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(fetchone_row={"id": 1})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        save_strategy("n", "d", "h", "code", "hash", "manual")
        sql_called = mock_cur.execute.call_args[0][0]
        self.assertIn("INSERT INTO strategies", sql_called)
        self.assertIn("draft", sql_called)


class TestGetStrategies(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_list(self, mock_get_conn):
        rows = [{"id": 1, "name": "S1", "status": "draft", "sharpe_ratio": None}]
        mock_conn, mock_cur = _make_mock_conn(rows=rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_strategies()
        self.assertEqual(result, rows)

    @patch("queries.get_conn")
    def test_returns_empty_list_when_no_strategies(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(rows=[])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_strategies()
        self.assertEqual(result, [])


class TestSaveBacktestRun(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_run_id(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(fetchone_row={"id": 7})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        metrics = {
            "total_return_pct": 12.5, "sharpe_ratio": 1.2,
            "max_drawdown_pct": 8.0, "win_rate_pct": 55.0,
            "trade_count": 10, "avg_hold_bars": 5.5,
        }
        params = {
            "initial_capital": 100000, "max_position_pct": 0.15,
            "stop_loss_pct": 0.05, "max_hold_bars": 20,
        }
        result = save_backtest_run(
            strategy_id=1, symbol="AAPL",
            start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
            data_source="yfinance", params=params, metrics=metrics,
        )
        self.assertEqual(result, 7)


class TestSaveBacktestTrades(unittest.TestCase):
    @patch("queries.execute_values")
    @patch("queries.get_conn")
    def test_calls_execute_values_with_trades(self, mock_get_conn, mock_exec_values):
        mock_conn, mock_cur = _make_mock_conn()
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        trades = [
            {"side": "long", "entry_bar": 50, "exit_bar": 55,
             "entry_price": 100.0, "exit_price": 105.0,
             "position_size": 12000.0, "pnl": 600.0, "exit_reason": "signal"},
        ]
        save_backtest_trades(run_id=7, symbol="AAPL", trades=trades)
        self.assertTrue(mock_exec_values.called)

    @patch("queries.get_conn")
    def test_no_op_when_trades_empty(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn()
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        # Should not raise
        save_backtest_trades(run_id=7, symbol="AAPL", trades=[])


class TestGetStrategyRuns(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_runs_for_strategy(self, mock_get_conn):
        rows = [{"id": 1, "strategy_id": 5, "symbol": "AAPL", "sharpe_ratio": 1.2}]
        mock_conn, mock_cur = _make_mock_conn(rows=rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_strategy_runs(5)
        self.assertEqual(result, rows)


class TestUpdateStrategyStatus(unittest.TestCase):
    @patch("queries.get_conn")
    def test_executes_update(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn()
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        update_strategy_status(strategy_id=3, status="candidate")
        sql = mock_cur.execute.call_args[0][0]
        self.assertIn("UPDATE strategies", sql)

    @patch("queries.get_conn")
    def test_passes_correct_params(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn()
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        update_strategy_status(strategy_id=3, status="retired")
        params = mock_cur.execute.call_args[0][1]
        self.assertIn("retired", params)
        self.assertIn(3, params)


class TestGetCandidates(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_candidate_list(self, mock_get_conn):
        rows = [{"id": 2, "name": "S2", "status": "candidate", "sharpe_ratio": 1.5}]
        mock_conn, mock_cur = _make_mock_conn(rows=rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_candidates()
        self.assertEqual(result, rows)


class TestGetRunTrades(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_trades_for_run(self, mock_get_conn):
        rows = [{"id": 1, "run_id": 7, "symbol": "AAPL", "pnl": 500.0}]
        mock_conn, mock_cur = _make_mock_conn(rows=rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_run_trades(7)
        self.assertEqual(result, rows)
