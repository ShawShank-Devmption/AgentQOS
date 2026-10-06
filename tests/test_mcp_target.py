"""Tests for the instrumented MCP target service."""

import json
from pathlib import Path

from harness.mcp_target.server import McpService


def _tool_result(response: dict[str, object]) -> object:
    result = response["result"]
    assert isinstance(result, dict)
    content = result["content"]
    assert isinstance(content, list)
    first = content[0]
    assert isinstance(first, dict)
    return json.loads(str(first["text"]))


def test_search_fetch_and_compute_tools_are_deterministic(tmp_path: Path) -> None:
    service = McpService(tmp_path / "requests.jsonl")

    search = service.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "latency protection"}},
        },
        client_ip="10.0.0.1",
    )
    fetch = service.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "fetch", "arguments": {"resource": "mcp-overview"}},
        },
        client_ip="10.0.0.1",
    )
    compute = service.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "compute", "arguments": {"expression": "(7 + 5) * 3"}},
        },
        client_ip="10.0.0.1",
    )

    assert _tool_result(search) == [
        {
            "resource": "agent-qos",
            "title": "Agent-aware QoS",
        }
    ]
    assert _tool_result(fetch) == {
        "body": "MCP uses JSON-RPC messages to expose tools and resources to agent clients.",
        "resource": "mcp-overview",
        "title": "Model Context Protocol",
    }
    assert _tool_result(compute) == {"expression": "(7 + 5) * 3", "value": 36}


def test_invalid_json_rpc_returns_protocol_error_and_is_logged(tmp_path: Path) -> None:
    log_path = tmp_path / "requests.jsonl"
    response = McpService(log_path).handle({}, client_ip="10.0.0.9")

    assert response == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "Invalid Request"},
    }
    entry = json.loads(log_path.read_text(encoding="utf-8"))
    assert entry["client_ip"] == "10.0.0.9"
    assert entry["status"] == "error"
    assert entry["end_time_ns"] >= entry["start_time_ns"]
    assert entry["duration_ms"] >= 0
