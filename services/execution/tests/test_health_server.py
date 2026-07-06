import time
import urllib.request
import urllib.error

from health_server import start_health_server


def test_health_endpoint_returns_200():
    port = 19081
    thread = start_health_server(port=port)
    assert thread.is_alive()
    time.sleep(0.1)
    with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2) as resp:
        assert resp.status == 200
        body = resp.read().decode()
        assert "ok" in body.lower()


def test_health_server_returns_daemon_thread():
    port = 19082
    thread = start_health_server(port=port)
    assert thread.daemon is True
    assert thread.is_alive()


def test_health_endpoint_returns_404_for_unknown_path():
    port = 19083
    start_health_server(port=port)
    time.sleep(0.1)
    try:
        urllib.request.urlopen(f"http://localhost:{port}/other", timeout=2)
        assert False, "Expected HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 404
