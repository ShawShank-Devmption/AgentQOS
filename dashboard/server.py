"""Serve the local measurement dashboard: python -m dashboard.server SOURCE.json."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from common.contracts import TrafficClass
from eval.metrics import MeasurementError, load_plot_summary

LOGGER = logging.getLogger(__name__)
ASSETS = Path(__file__).parent
STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


@dataclass(frozen=True)
class Response:
    """A complete allowlisted dashboard response."""

    status: int
    content_type: str
    body: bytes


def _timestamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _metrics_response(source: Path) -> Response:
    payload: dict[str, Any] = {
        "status": "unavailable",
        "message": "Measurements are missing, invalid, or contain no samples.",
        "checked_at": datetime.now(UTC).isoformat(),
        "source_updated_at": None,
        "class_order": [label.name for label in TrafficClass],
        "summary": None,
    }
    try:
        before = source.stat()
        payload["source_updated_at"] = _timestamp(before.st_mtime)
        summary = load_plot_summary(source)
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise MeasurementError("measurement source changed during validation")
    except (MeasurementError, OSError) as exc:
        LOGGER.warning("Dashboard measurement unavailable: %s", exc)
        status = 503
    else:
        payload.update(
            status="ready",
            message="Validated measurement snapshot; source modification time is shown below.",
            summary=summary,
        )
        status = 200
    return Response(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))


def response_for(path: str, source: Path) -> Response:
    """Return only fixed dashboard assets or the current validated measurement snapshot.

    The request never selects a source file. Query strings, encoded paths and traversal paths
    are absent from the exact route allowlist and receive a generic response.
    """
    if path == "/api/metrics":
        return _metrics_response(source)
    if path in STATIC_ROUTES:
        filename, content_type = STATIC_ROUTES[path]
        try:
            return Response(200, content_type, (ASSETS / filename).read_bytes())
        except OSError as exc:
            LOGGER.error("Dashboard asset unavailable: %s", exc)
            return Response(503, "text/plain; charset=utf-8", b"Dashboard asset unavailable.\n")
    return Response(404, "text/plain; charset=utf-8", b"Not found.\n")


class DashboardServer(ThreadingHTTPServer):
    """Local server with a source fixed by the host operator, never by the browser."""

    def __init__(self, address: tuple[str, int], source: Path) -> None:
        self.source = source
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP boundary for fixed assets and read-only metrics."""

    server: DashboardServer

    def do_GET(self) -> None:  # noqa: N802 -- stdlib HTTP handler interface
        """Serve a single allowlisted GET request without caching."""
        result = response_for(self.path, self.server.source)
        self.send_response(result.status)
        self.send_header("Content-Type", result.content_type)
        self.send_header("Content-Length", str(len(result.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(result.body)

    def log_message(self, format: str, *args: object) -> None:
        """Route stdlib request diagnostics to the project logger."""
        LOGGER.info("HTTP %s", format % args)


def main(argv: Sequence[str] | None = None) -> int:
    """Run a read-only dashboard bound to loopback until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="observations.json or validated per-run summary")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    logging.basicConfig(level=logging.INFO)
    try:
        with DashboardServer(("127.0.0.1", args.port), args.source) as httpd:
            LOGGER.info("Dashboard at http://127.0.0.1:%d", args.port)
            httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Dashboard stopped")
    except OSError as exc:
        LOGGER.error("Dashboard could not start: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
