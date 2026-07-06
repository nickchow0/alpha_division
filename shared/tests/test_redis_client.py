import pytest
from unittest.mock import patch, MagicMock
from shared.redis_client import get_redis


def test_get_redis_creates_client_from_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    mock_client = MagicMock()

    with patch("shared.redis_client.redis.Redis.from_url", return_value=mock_client) as mock_from_url:
        import shared.redis_client as rc_module
        rc_module._client = None  # reset singleton
        client = get_redis()
        mock_from_url.assert_called_once_with(
            "redis://localhost:6379",
            decode_responses=True,
        )
        assert client == mock_client
        rc_module._client = None  # cleanup


def test_get_redis_returns_existing_client(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    mock_client = MagicMock()

    with patch("shared.redis_client.redis.Redis.from_url") as mock_from_url:
        import shared.redis_client as rc_module
        rc_module._client = mock_client
        client = get_redis()
        mock_from_url.assert_not_called()
        assert client == mock_client
        rc_module._client = None  # cleanup


def test_get_redis_uses_default_url_when_env_not_set(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    mock_client = MagicMock()

    with patch("shared.redis_client.redis.Redis.from_url", return_value=mock_client) as mock_from_url:
        import shared.redis_client as rc_module
        rc_module._client = None
        get_redis()
        mock_from_url.assert_called_once_with(
            "redis://localhost:6379",
            decode_responses=True,
        )
        rc_module._client = None  # cleanup
