"""Stateless MCP Streamable HTTP target with deterministic search/fetch/compute tools."""

from __future__ import annotations

import argparse
import json
import logging
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TextIO

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 65_536
PROTOCOL_VERSION = "2025-03-26"
_DOCUMENTS = {
    "networking": "Agent-aware networking measures traffic timing and applies transport QoS.",
    "mcp": "MCP exposes search, fetch and compute as structured tools over JSON-RPC.",
    "evaluation": "Reproducible experiments use explicit seeds and preserve measured raw data.",
}
_TOOLS = [
    {
        "name": "search",
        "description": "Search the local reference documents.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "maxLength": 256}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fetch",
        "description": "Fetch one local reference document by its identifier.",
        "inputSchema": {
            "type": "object",
            "properties": {"document_id": {"type": "string", "enum": list(_DOCUMENTS)}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compute",
        "description": "Sum up to 128 finite bounded numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "maxItems": 128,
                    "items": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
                }
            },
            "required": ["values"],
            "additionalProperties": False,
        },
    },
]


def _error(code: int, message: str, request_id: Any = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "search" and set(arguments) == {"query"}:
        query = arguments["query"]
        if isinstance(query, str) and 0 < len(query) <= 256:
            return [
                {"document_id": key, "text": text}
                for key, text in _DOCUMENTS.items()
                if query.casefold() in f"{key} {text}".casefold()
            ]
    elif name == "fetch" and set(arguments) == {"document_id"}:
        document_id = arguments["document_id"]
        if isinstance(document_id, str) and document_id in _DOCUMENTS:
            return {"document_id": document_id, "text": _DOCUMENTS[document_id]}
    elif name == "compute" and set(arguments) == {"values"}:
        values = arguments["values"]
        if (
            isinstance(values, list)
            and len(values) <= 128
            and all(
                type(value) in (int, float) and abs(value) <= 1_000_000 and math.isfinite(value)
                for value in values
            )
        ):
            return {"sum": sum(values)}
    raise ValueError("unknown tool or invalid arguments")


def dispatch(request: object) -> tuple[int, dict[str, Any] | None]:
    """Validate one bounded JSON-RPC request and implement MCP lifecycle and tool methods."""
    if not isinstance(request, dict):
        return 400, _error(-32600, "expected one JSON-RPC request object")
    request_id = request.get("id")
    if (
        request.get("jsonrpc") != "2.0"
        or not isinstance(request.get("method"), str)
        or ("id" in request and (type(request_id) not in (int, str) or len(str(request_id)) > 128))
    ):
        return 400, _error(-32600, "invalid JSON-RPC envelope")
    method = request["method"]
    params = request.get("params", {})
    if not isinstance(params, dict):
        return 400, _error(-32602, "params must be an object", request_id)
    if "id" not in request:
        if method == "notifications/initialized":
            return 202, None
        return 400, _error(-32600, "only initialized notifications are supported")
    if method == "initialize":
        result: Any = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agent-aware-mcp-target", "version": "1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": _TOOLS}
    elif method == "tools/call":
        if set(params) != {"name", "arguments"} or not isinstance(params.get("arguments"), dict):
            return 400, _error(-32602, "expected tool name and argument object", request_id)
        try:
            value = _tool(params["name"], params["arguments"])
        except ValueError as exc:
            return 400, _error(-32602, str(exc), request_id)
        result = {
            "content": [{"type": "text", "text": json.dumps(value, allow_nan=False)}],
            "isError": False,
        }
    else:
        return 400, _error(-32601, "method not found", request_id)
    return 200, {"jsonrpc": "2.0", "id": request_id, "result": result}


class McpServer(ThreadingHTTPServer):
    """Thread-bounded server owning an append-only request timing log."""

    daemon_threads = False
    block_on_close = True

    def __init__(self, address: tuple[str, int], log_file: TextIO) -> None:
        self.log_file = log_file
        self.log_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, McpHandler)

    def process_request(self, request: Any, client_address: Any) -> None:
        """Reject excess connections before creating another handler thread."""
        request.settimeout(5.0)
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            except OSError:
                logger.debug("overloaded client disconnected", exc_info=True)
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        """Return a concurrency slot after the complete connection has finished."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def record(self, event: dict[str, Any]) -> None:
        """Append one complete event without interleaving concurrent tool-call records."""
        with self.log_lock:
            self.log_file.write(json.dumps(event, allow_nan=False) + "\n")
            self.log_file.flush()

    def server_close(self) -> None:
        """Close the listening socket and owned timing log."""
        super().server_close()
        with self.log_lock:
            self.log_file.close()


class McpHandler(BaseHTTPRequestHandler):
    """HTTP transport for the target's stateless MCP endpoint."""

    server: McpServer

    def log_message(self, format: str, *args: Any) -> None:
        """Send access diagnostics through logging instead of stderr writes."""
        logger.debug(format, *args)

    def _send(self, status: int, payload: object | None) -> None:
        body = b"" if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        """Expose a health probe; SSE streams are deliberately unnecessary for these tools."""
        self._send(
            200 if self.path == "/health" else 405,
            {"status": "ok"} if self.path == "/health" else {"error": "use POST /mcp"},
        )

    def do_POST(self) -> None:  # noqa: N802
        """Validate body framing and record timing for each completed POST request."""
        started = time.time()
        status = 400
        payload: Any = _error(-32600, "invalid HTTP framing")
        request: Any = None
        try:
            if self.path != "/mcp":
                status, payload = 404, {"error": "unknown endpoint"}
            elif self.headers.get("Transfer-Encoding"):
                status, payload = 400, _error(-32600, "transfer encoding is unsupported")
            elif self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                status, payload = 415, _error(-32600, "Content-Type must be application/json")
            elif self.headers.get("MCP-Protocol-Version", PROTOCOL_VERSION) != PROTOCOL_VERSION:
                status, payload = 400, _error(-32600, "unsupported MCP protocol version")
            else:
                status, payload, request = self._read_request()
            self._send(status, payload)
        except (OSError, TimeoutError):
            logger.warning("MCP client disconnected or timed out", exc_info=True)
        finally:
            params = request.get("params", {}) if isinstance(request, dict) else {}
            self.server.record(
                {
                    "source_ip": self.client_address[0],
                    "start_time_s": started,
                    "end_time_s": time.time(),
                    "status": status,
                    "method": request.get("method") if isinstance(request, dict) else None,
                    "tool": params.get("name") if isinstance(params, dict) else None,
                }
            )

    def _read_request(self) -> tuple[int, Any, Any]:
        try:
            raw_length = self.headers.get_all("Content-Length", [])
            if len(raw_length) != 1:
                return 411, _error(-32600, "one Content-Length is required"), None
            length = int(raw_length[0])
            if length < 0:
                raise ValueError("negative body size")
        except ValueError:
            return 400, _error(-32600, "invalid Content-Length"), None
        if length > MAX_BODY_BYTES:
            return 413, _error(-32600, "request body exceeds limit"), None
        body = self.rfile.read(length)
        if len(body) != length:
            return 400, _error(-32700, "incomplete request body"), None
        try:
            request = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            return 400, _error(-32700, "invalid JSON"), None
        status, payload = dispatch(request)
        return status, payload, request


def create_server(host: str, port: int, log_path: Path) -> McpServer:
    """Bind an HTTP target while refusing to overwrite a previous request log."""
    log_file = log_path.open("x", encoding="utf-8")
    try:
        return McpServer((host, port), log_file)
    except BaseException:
        log_file.close()
        raise


def main() -> None:
    """Serve the MCP target until interrupted, preserving request logs on shutdown."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    with create_server(args.host, args.port, args.log) as server:
        logger.info("MCP target listening on %s:%s", args.host, args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("MCP target interrupted")


if __name__ == "__main__":
    main()
