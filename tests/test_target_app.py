"""Protocol and live HTTP tests for the instrumented MCP target."""

import http.client
import json
import threading
from pathlib import Path

import pytest

from harness.mcp_target.server import MAX_BODY_BYTES, create_server, dispatch


def test_mcp_lifecycle_and_tools() -> None:
    status, payload = dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert status == 200
    assert payload["result"]["protocolVersion"] == "2025-03-26"
    _, tools = dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {tool["name"] for tool in tools["result"]["tools"]} == {"search", "fetch", "compute"}
    _, response = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "compute", "arguments": {"values": [2, 3, -1]}},
        }
    )
    assert json.loads(response["result"]["content"][0]["text"]) == {"sum": 4}
    assert dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)


@pytest.mark.parametrize("envelope", [[], {}, {"jsonrpc": "2.0", "id": True, "method": "x"}])
def test_invalid_envelopes(envelope: object) -> None:
    _, response = dispatch(envelope)
    assert response["error"]["code"] == -32600


@pytest.mark.parametrize(
    "arguments",
    [
        {"values": [True]},
        {"values": [float("inf")]},
        {"values": list(range(129))},
        {"values": [], "extra": 1},
    ],
)
def test_compute_limits(arguments: dict[str, object]) -> None:
    _, response = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "compute", "arguments": arguments},
        }
    )
    assert response["error"]["code"] == -32602


def test_http_target_and_append_only_request_log(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    server = create_server("127.0.0.1", 0, path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=2)
    try:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "search", "arguments": {"query": "network"}},
            }
        )
        connection.request("POST", "/mcp", body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["result"]["content"]
        connection.request("POST", "/mcp", b"{", {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert json.loads(response.read())["error"]["code"] == -32700
        connection.request(
            "POST", "/mcp", b"x" * (MAX_BODY_BYTES + 1), {"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        assert response.status == 413
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(events) == 3
    assert events[0]["tool"] == "search"
    assert events[0]["end_time_s"] >= events[0]["start_time_s"]
    with pytest.raises(FileExistsError):
        create_server("127.0.0.1", 0, path)
