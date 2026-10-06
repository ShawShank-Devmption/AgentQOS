"""Instrumented HTTP MCP target with deterministic search, fetch, and compute tools."""

from __future__ import annotations

import argparse
import ast
import json
import logging
import operator
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final

LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES: Final = 1_048_576
PROTOCOL_VERSION: Final = "2025-06-18"

_DOCUMENTS: Final = {
    "agent-qos": {
        "title": "Agent-aware QoS",
        "body": "Agent-aware networking provides human tail-latency protection during bursts.",
    },
    "mcp-overview": {
        "title": "Model Context Protocol",
        "body": "MCP uses JSON-RPC messages to expose tools and resources to agent clients.",
    },
    "diffserv": {
        "title": "Differentiated Services",
        "body": "DiffServ assigns packets to forwarding classes using DSCP markings.",
    },
}


class RpcError(ValueError):
    """A JSON-RPC error safe to return to an MCP client."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ToolDefinition:
    """One MCP tool exposed by the target."""

    name: str
    description: str
    input_schema: Mapping[str, object]

    def as_dict(self) -> dict[str, object]:
        """Return the MCP wire representation of this tool.

        Returns:
            JSON-compatible tool metadata.
        """
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }


TOOLS: Final = (
    ToolDefinition(
        "search",
        "Search the target's deterministic document corpus.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        "fetch",
        "Fetch one document by resource identifier.",
        {
            "type": "object",
            "properties": {"resource": {"type": "string"}},
            "required": ["resource"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        "compute",
        "Evaluate a bounded arithmetic expression.",
        {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    ),
)


class McpService:
    """Thread-safe MCP request dispatcher with JSONL ground-truth logs."""

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path
        self._log_lock = threading.Lock()

    def handle(self, request: object, client_ip: str) -> dict[str, object]:
        """Dispatch one JSON-RPC request and append its timing record.

        Args:
            request: Decoded JSON request value.
            client_ip: Orchestration-visible source address.

        Returns:
            A JSON-RPC success or error response.
        """
        start_time_ns = time.time_ns()
        request_id: object = None
        method = ""
        tool = ""
        status = "ok"
        try:
            if not isinstance(request, Mapping):
                raise RpcError(-32600, "Invalid Request")
            request_id = request.get("id")
            if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                raise RpcError(-32600, "Invalid Request")
            method = str(request["method"])
            result, tool = self._dispatch(method, request.get("params", {}))
            response: dict[str, object] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except RpcError as exc:
            status = "error"
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": exc.code, "message": exc.message},
            }
        end_time_ns = time.time_ns()
        self._append_log(
            {
                "request_id": request_id,
                "client_ip": client_ip,
                "method": method,
                "tool": tool,
                "start_time_ns": start_time_ns,
                "end_time_ns": end_time_ns,
                "duration_ms": (end_time_ns - start_time_ns) / 1_000_000,
                "status": status,
            }
        )
        return response

    def _dispatch(self, method: str, raw_params: object) -> tuple[dict[str, object], str]:
        if method == "initialize":
            return (
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "agent-qos-target", "version": "1.0.0"},
                },
                "",
            )
        if method == "tools/list":
            return {"tools": [tool.as_dict() for tool in TOOLS]}, ""
        if method != "tools/call":
            raise RpcError(-32601, "Method not found")
        if not isinstance(raw_params, Mapping):
            raise RpcError(-32602, "Invalid params")
        name = raw_params.get("name")
        arguments = raw_params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            raise RpcError(-32602, "Invalid params")
        payload = _call_tool(name, arguments)
        return (
            {
                "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
                "isError": False,
            },
            name,
        )

    def _append_log(self, entry: Mapping[str, object]) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_lock, self._log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(entry, sort_keys=True))
            log_file.write("\n")


def create_server(host: str, port: int, service: McpService) -> ThreadingHTTPServer:
    """Create the threaded MCP HTTP server without starting its event loop.

    Args:
        host: Address to bind.
        port: TCP port to bind.
        service: Request dispatcher and logger.

    Returns:
        A configured `ThreadingHTTPServer`.
    """
    if port < 0 or port > 65_535:
        raise ValueError("port must be between 0 and 65535")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/mcp":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            try:
                request = json.loads(self.rfile.read(content_length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                response: dict[str, object] = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            else:
                response = service.handle(request, self.client_address[0])
            body = json.dumps(response, sort_keys=True).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, message_format: str, *args: object) -> None:
            LOGGER.info("MCP HTTP: %s", message_format % args)

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the instrumented MCP target until interrupted.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero after a clean shutdown.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log-path", type=Path, default=Path("results/mcp_requests.jsonl"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = create_server(args.host, args.port, McpService(args.log_path))
    LOGGER.info("MCP target listening on %s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("MCP target stopping")
    finally:
        server.server_close()
    return 0


def _call_tool(name: str, arguments: Mapping[str, object]) -> object:
    if name == "search":
        query = _required_string(arguments, "query").casefold()
        terms = query.split()
        return [
            {"resource": resource, "title": str(document["title"])}
            for resource, document in sorted(_DOCUMENTS.items())
            if all(term in f"{document['title']} {document['body']}".casefold() for term in terms)
        ]
    if name == "fetch":
        resource = _required_string(arguments, "resource")
        document = _DOCUMENTS.get(resource)
        if document is None:
            raise RpcError(-32602, "unknown resource")
        return {"resource": resource, **document}
    if name == "compute":
        expression = _required_string(arguments, "expression")
        return {"expression": expression, "value": _evaluate_expression(expression)}
    raise RpcError(-32602, "unknown tool")


def _required_string(arguments: Mapping[str, object], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RpcError(-32602, f"{field} must be a non-empty string")
    return value.strip()


def _evaluate_expression(expression: str) -> int | float:
    if len(expression) > 200:
        raise RpcError(-32602, "expression is too long")
    try:
        parsed = ast.parse(expression, mode="eval")
        value = _evaluate_node(parsed.body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise RpcError(-32602, "invalid arithmetic expression") from exc
    if abs(float(value)) > 1e12:
        raise RpcError(-32602, "arithmetic result is out of range")
    return value


def _evaluate_node(node: ast.AST) -> int | float:
    binary_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    unary_operators = {ast.UAdd: operator.pos, ast.USub: operator.neg}
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in binary_operators:
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > 12:
            raise ValueError("exponent out of range")
        return binary_operators[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in unary_operators:
        return unary_operators[type(node.op)](_evaluate_node(node.operand))
    raise ValueError("unsupported expression")


if __name__ == "__main__":
    raise SystemExit(main())
