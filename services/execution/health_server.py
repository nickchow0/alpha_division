import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from shared.logger import get_logger

log = get_logger("execution")

_DEFAULT_PORT = 8080


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default HTTP server access log — we use structured logging
        pass


def start_health_server(port: int = _DEFAULT_PORT) -> threading.Thread:
    """
    Start the HTTP health server on a background daemon thread.

    Responds to GET /health with {"status": "ok"} and HTTP 200.
    Returns 404 for all other paths.
    Runs as a daemon thread — exits automatically when the main process exits.
    """
    server = HTTPServer(("", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Health server listening on :{port}")
    return thread
