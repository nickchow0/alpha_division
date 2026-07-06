import pytest
from unittest.mock import patch, MagicMock
from shared.db import get_pool, get_conn


def test_get_pool_creates_pool_with_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    mock_pool = MagicMock()

    with patch("shared.db.SimpleConnectionPool", return_value=mock_pool) as mock_cls:
        import shared.db as db_module
        db_module._pool = None  # reset singleton
        pool = get_pool()
        mock_cls.assert_called_once_with(
            minconn=1,
            maxconn=5,
            dsn="postgresql://user:pass@localhost/testdb",
        )
        assert pool == mock_pool
        db_module._pool = None  # cleanup


def test_get_pool_returns_existing_pool(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    mock_pool = MagicMock()

    with patch("shared.db.SimpleConnectionPool", return_value=mock_pool) as mock_cls:
        import shared.db as db_module
        db_module._pool = mock_pool
        pool = get_pool()
        mock_cls.assert_not_called()
        assert pool == mock_pool
        db_module._pool = None  # cleanup


def test_get_conn_commits_on_success(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_conn

    with patch("shared.db.get_pool", return_value=mock_pool):
        with get_conn() as conn:
            assert conn == mock_conn

    mock_conn.commit.assert_called_once()
    mock_pool.putconn.assert_called_once_with(mock_conn)


def test_get_conn_rolls_back_on_exception(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_conn

    with patch("shared.db.get_pool", return_value=mock_pool):
        with pytest.raises(ValueError):
            with get_conn() as conn:
                raise ValueError("db error")

    mock_conn.rollback.assert_called_once()
    mock_conn.commit.assert_not_called()
    mock_pool.putconn.assert_called_once_with(mock_conn)


def test_get_pool_raises_if_database_url_not_set(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import shared.db as db_module
    db_module._pool = None  # reset singleton
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        get_pool()
    db_module._pool = None  # cleanup
