"""Application-layer nginx rate-limiter baseline command plan."""

from pathlib import Path

from eval.baselines.base import BaselinePlan


def plan(link_mbps: int, output_dir: Path) -> BaselinePlan:
    """Return the target-namespace nginx lifecycle."""
    del link_mbps
    prefix = output_dir / "nginx"
    config = Path(__file__).with_name("nginx-app-limiter.conf").resolve()
    return BaselinePlan(
        name="app_limiter",
        setup_commands=(
            (
                "nginx",
                "-p",
                str(prefix),
                "-c",
                str(config),
                "-g",
                "daemon off;",
            ),
        ),
        teardown_commands=(
            (
                "nginx",
                "-p",
                str(prefix),
                "-c",
                str(config),
                "-s",
                "quit",
            ),
        ),
    )
