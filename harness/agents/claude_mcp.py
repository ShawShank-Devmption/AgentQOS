"""Claude plus MCP traffic adapter."""

from collections.abc import Sequence

from harness.agents.base import runner_main

FRAMEWORK_NAME = "claude-mcp"


def main(argv: Sequence[str] | None = None) -> int:
    """Run Claude+MCP-labeled deterministic tool tasks."""
    return runner_main(FRAMEWORK_NAME, argv)


if __name__ == "__main__":
    raise SystemExit(main())
