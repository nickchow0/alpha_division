import unittest
from unittest.mock import patch
from datetime import date, datetime, timezone


MOCK_POSITIONS = [
    {"symbol": "AAPL", "qty": 10, "price": "150.0000",
     "placed_at": datetime(2026, 5, 15, 9, 30, tzinfo=timezone.utc)},
]
MOCK_TRADES = [
    {"id": 1, "symbol": "AAPL", "side": "buy", "qty": 10, "price": "150.0000",
     "status": "filled",
     "placed_at": datetime(2026, 5, 15, 9, 30, tzinfo=timezone.utc),
     "filled_at": datetime(2026, 5, 15, 9, 30, 5, tzinfo=timezone.utc)},
]
MOCK_DECISIONS = [
    {"id": 1, "symbol": "AAPL", "decision": "buy", "confidence": "0.820",
     "reasoning": "Uptrend", "model": "claude-haiku",
     "acted_on": True, "skip_reason": None,
     "decided_at": datetime(2026, 5, 15, 9, 29, tzinfo=timezone.utc)},
]
MOCK_WATCHLIST = [
    {"symbol": "AAPL", "decision": "buy", "confidence": "0.820",
     "decided_at": datetime(2026, 5, 15, 9, 29, tzinfo=timezone.utc), "acted_on": True},
]
MOCK_API_HEALTH = [
    {"api_name": "alpaca", "status": "ok", "latency_ms": 45,
     "checked_at": datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc), "error_message": None},
]
MOCK_SERVICES = [
    {"name": "data", "alive": True, "ttl": 60},
    {"name": "analysis", "alive": True, "ttl": 55},
    {"name": "execution", "alive": False, "ttl": -2},
    {"name": "alerts", "alive": True, "ttl": 70},
]
MOCK_TRADE_STATS = {
    "total_closed": 0,
    "wins": 0,
    "losses": 0,
    "win_rate_pct": 0.0,
    "avg_pnl": 0.0,
    "best_trade": 0.0,
    "worst_trade": 0.0,
    "avg_holding_hours": 0.0,
}


class TestFlaskRoutes(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch("queries.get_open_positions", return_value=MOCK_POSITIONS),
            patch("queries.get_total_pnl", return_value=250.0),
            patch("queries.get_daily_pnl_today", return_value=50.0),
            patch("queries.get_circuit_breaker_status", return_value=False),
            patch("queries.get_recent_trades", return_value=MOCK_TRADES),
            patch("queries.get_recent_decisions", return_value=MOCK_DECISIONS),
            patch("queries.get_api_health", return_value=MOCK_API_HEALTH),
            patch("queries.get_watchlist", return_value=MOCK_WATCHLIST),
            patch("service_status.get_service_statuses", return_value=MOCK_SERVICES),
            patch("queries.get_trade_stats", return_value=MOCK_TRADE_STATS),
        ]
        for p in self.patches:
            p.start()

        import main
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_health_returns_200(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_overview_returns_200(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_overview_contains_pnl(self):
        resp = self.client.get("/")
        self.assertIn(b"250.00", resp.data)

    def test_overview_contains_position(self):
        resp = self.client.get("/")
        self.assertIn(b"AAPL", resp.data)

    def test_trades_returns_200(self):
        resp = self.client.get("/trades")
        self.assertEqual(resp.status_code, 200)

    def test_trades_contains_trade_data(self):
        resp = self.client.get("/trades")
        self.assertIn(b"AAPL", resp.data)
        self.assertIn(b"BUY", resp.data)

    def test_decisions_returns_200(self):
        resp = self.client.get("/decisions")
        self.assertEqual(resp.status_code, 200)

    def test_decisions_contains_decision_data(self):
        resp = self.client.get("/decisions")
        self.assertIn(b"AAPL", resp.data)
        self.assertIn(b"BUY", resp.data)

    def test_watchlist_returns_200(self):
        resp = self.client.get("/watchlist")
        self.assertEqual(resp.status_code, 200)

    def test_watchlist_contains_symbol(self):
        resp = self.client.get("/watchlist")
        self.assertIn(b"AAPL", resp.data)


if __name__ == "__main__":
    unittest.main()
