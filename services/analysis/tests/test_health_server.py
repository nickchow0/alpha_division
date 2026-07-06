import threading
import time
import urllib.request
import pytest

from health_server import start_health_server


def test_health_endpoint_returns_200():
    """Health server responds to GET /health with 200 OK."""
    port = 18081  # Use a non-standard port to avoid conflicts in tests
    thread = start_health_server(port=port)
    assert thread.is_alive()
    time.sleep(0.1)  # Give the server a moment to bind

    try:
        with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2) as resp:
            assert resp.status == 200
            body = resp.read().decode()
            assert "ok" in body.lower() or "status" in body.lower()
    finally:
        # Server runs as daemon thread — it stops when the test process exits
        pass


def test_health_server_returns_daemon_thread():
    """start_health_server returns a daemon thread."""
    port = 18082
    thread = start_health_server(port=port)
    assert thread.daemon is True
    assert thread.is_alive()


def test_health_endpoint_returns_404_for_unknown_path():
    """Only /health is handled — other paths return 404."""
    port = 18083
    start_health_server(port=port)
    time.sleep(0.1)

    try:
        urllib.request.urlopen(f"http://localhost:{port}/other", timeout=2)
        assert False, "Expected HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 404
