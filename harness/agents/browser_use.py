"""Browser Use traffic adapter."""

from collections.abc import Sequence

from harness.agents.base import runner_main

FRAMEWORK_NAME = "browser-use"


def main(argv: Sequence[str] | None = None) -> int:
    """Run Browser Use-labeled deterministic MCP tasks."""
    return runner_main(FRAMEWORK_NAME, argv)


if __name__ == "__main__":
    raise SystemExit(main())
