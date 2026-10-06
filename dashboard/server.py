"""Read-only HTTP dashboard for live per-class throughput and latency snapshots."""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from common.contracts import TRAINING_LABELS

LOGGER = logging.getLogger(__name__)
METRIC_FIELDS = ("throughput_mbps", "p50_ms", "p95_ms", "p99_ms")


class SnapshotError(ValueError):
    """Raised when the live metrics snapshot is absent or malformed."""


def read_snapshot(path: Path) -> dict[str, object]:
    """Read and validate one immutable dashboard snapshot.

    Args:
        path: JSON file replaced atomically by the metrics producer.

    Returns:
        The validated JSON object.

    Raises:
        FileNotFoundError: If no snapshot has been produced yet.
        SnapshotError: If the JSON schema or metric values are invalid.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"could not parse dashboard snapshot: {path}") from exc
    if not isinstance(raw, Mapping):
        raise SnapshotError("dashboard snapshot must be a JSON object")
    generated_at = raw.get("generated_at")
    if not isinstance(generated_at, str):
        raise SnapshotError("dashboard snapshot requires generated_at")
    try:
        datetime.fromisoformat(generated_at)
    except ValueError as exc:
        raise SnapshotError("generated_at must be an ISO-8601 timestamp") from exc
    classes = raw.get("classes")
    if not isinstance(classes, Mapping):
        raise SnapshotError("dashboard snapshot requires classes")
    required_classes = {traffic_class.name for traffic_class in TRAINING_LABELS}
    if set(classes) != required_classes:
        raise SnapshotError(f"classes must be exactly {sorted(required_classes)}")
    for class_name, metrics in classes.items():
        if not isinstance(metrics, Mapping) or set(metrics) != set(METRIC_FIELDS):
            raise SnapshotError(f"invalid metric fields for {class_name}")
        for field in METRIC_FIELDS:
            value = metrics[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SnapshotError(f"{class_name}.{field} must be numeric")
            if not math.isfinite(float(value)) or float(value) < 0:
                raise SnapshotError(f"{class_name}.{field} must be finite and non-negative")
    return dict(raw)


def create_server(
    host: str,
    port: int,
    snapshot_path: Path,
    index_path: Path,
) -> ThreadingHTTPServer:
    """Create the read-only dashboard server without starting it.

    Args:
        host: Interface address to bind.
        port: TCP port, or zero for an ephemeral port.
        snapshot_path: Live JSON snapshot path.
        index_path: Static dashboard HTML path.

    Returns:
        A configured threaded HTTP server.
    """
    if port < 0 or port > 65_535:
        raise ValueError("port must be between 0 and 65535")
    if not index_path.is_file():
        raise FileNotFoundError(index_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", index_path.read_bytes())
                return
            if self.path == "/api/metrics":
                try:
                    snapshot = read_snapshot(snapshot_path)
                except (FileNotFoundError, SnapshotError) as exc:
                    body = json.dumps({"error": str(exc)}, sort_keys=True).encode("utf-8")
                    self._send(HTTPStatus.SERVICE_UNAVAILABLE, "application/json", body)
                    return
                body = json.dumps(snapshot, sort_keys=True).encode("utf-8")
                self._send(HTTPStatus.OK, "application/json", body)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, message_format: str, *args: object) -> None:
            LOGGER.info("dashboard HTTP: %s", message_format % args)

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: Sequence[str] | None = None) -> int:
    """Serve the live dashboard until interrupted.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero after a clean shutdown.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument(
        "--index",
        type=Path,
        default=Path(__file__).with_name("index.html"),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        server = create_server(args.host, args.port, args.snapshot, args.index)
    except (FileNotFoundError, ValueError) as exc:
        LOGGER.error("dashboard configuration failed: %s", exc)
        return 1
    LOGGER.info("dashboard listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("dashboard stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
