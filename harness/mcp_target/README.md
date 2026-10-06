# Instrumented MCP target

This container exposes an HTTP MCP endpoint at `/mcp` with deterministic `search`, `fetch`, and
`compute` tools. Every handled request is appended to `/data/requests.jsonl` with source IP,
wall-clock start/end timestamps, duration, method, tool, and status. These logs provide tool-call
completion ground truth; they are not classification labels.

Build and run from the repository root:

```bash
docker build -f harness/mcp_target/Dockerfile -t agent-qos-mcp-target .
docker run --rm -p 8080:8080 -v "$PWD/results/mcp-target:/data" agent-qos-mcp-target
```

The server uses only Python's standard library. Corpus runners must supply their own explicit seed,
parallelism, think time, and task script through `harness.agents.base.RunnerConfig`.
