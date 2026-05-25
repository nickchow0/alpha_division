# services/research/tests/test_main.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from unittest.mock import patch, MagicMock


class TestHealthRoute(unittest.TestCase):
    def setUp(self):
        from main import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_health_returns_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "ok"})


import json
from unittest.mock import patch, MagicMock

MOCK_STRATEGY = {
    "id": 1, "name": "RSI Bounce", "description": "RSI dip strategy",
    "hypothesis": "Buy oversold RSI", "code": "def generate_signal(s): pass",
    "code_hash": "abc123", "status": "draft", "triggered_by": "manual",
    "created_at": "2026-05-17T00:00:00Z",
    "sharpe_ratio": None, "win_rate_pct": None, "max_drawdown_pct": None,
    "total_return_pct": None, "trade_count": None,
}

MOCK_RUN = {
    "id": 1, "strategy_id": 1, "symbol": "AAPL",
    "start_date": "2023-01-01", "end_date": "2023-12-31",
    "data_source": "alpaca", "initial_capital": "100000.00",
    "total_return_pct": "12.50", "sharpe_ratio": "1.20",
    "max_drawdown_pct": "8.00", "win_rate_pct": "55.00",
    "trade_count": 10, "avg_hold_bars": "5.50", "ran_at": "2026-05-17T00:00:00Z",
}

VALID_CODE = """
def generate_signal(snapshot):
    return {"decision": "hold", "confidence": 0.5, "reasoning": "hold"}
"""

INVALID_CODE = "import os\ndef generate_signal(s): pass"


class TestStrategyRoutes(unittest.TestCase):
    def setUp(self):
        from main import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    @patch("main.save_strategy", return_value=1)
    @patch("main.compute_code_hash", return_value="hash123")
    @patch("main.validate_strategy_code")
    def test_post_strategy_valid_returns_201(self, mock_val, mock_hash, mock_save):
        resp = self.client.post(
            "/api/strategies",
            json={"name": "Test", "description": "d", "hypothesis": "h",
                  "code": VALID_CODE, "triggered_by": "manual"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["id"], 1)

    @patch("main.validate_strategy_code", side_effect=ValueError("Import not allowed"))
    def test_post_strategy_invalid_code_returns_400(self, mock_val):
        resp = self.client.post(
            "/api/strategies",
            json={"name": "Bad", "description": "d", "hypothesis": "h",
                  "code": INVALID_CODE, "triggered_by": "manual"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    @patch("main.get_strategies", return_value=[MOCK_STRATEGY])
    def test_get_strategies_returns_list(self, mock_get):
        resp = self.client.get("/api/strategies")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)

    @patch("main._spy_annualized_return", return_value=10.5)
    @patch("main.get_strategy_runs", return_value=[MOCK_RUN])
    def test_get_strategy_runs(self, mock_runs, mock_spy):
        resp = self.client.get("/api/strategies/1/runs")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("spy_annualized_return_pct", data[0])

    @patch("main.update_strategy_status")
    def test_retire_strategy(self, mock_update):
        resp = self.client.post("/api/strategies/1/retire")
        self.assertEqual(resp.status_code, 200)
        mock_update.assert_called_once_with(strategy_id=1, status="retired")

    @patch("main.update_strategy_status")
    def test_approve_strategy(self, mock_update):
        resp = self.client.post("/api/strategies/1/approve")
        self.assertEqual(resp.status_code, 200)
        mock_update.assert_called_once_with(strategy_id=1, status="approved")

    @patch("main.get_run_trades", return_value=[])
    def test_get_run_trades(self, mock_trades):
        resp = self.client.get("/api/runs/7/trades")
        self.assertEqual(resp.status_code, 200)

    @patch("main.get_candidates", return_value=[])
    def test_get_candidates_page(self, mock_cands):
        resp = self.client.get("/candidates")
        self.assertEqual(resp.status_code, 200)

    @patch("main.get_strategies", return_value=[])
    def test_get_research_page(self, mock_strats):
        resp = self.client.get("/research")
        self.assertEqual(resp.status_code, 200)
