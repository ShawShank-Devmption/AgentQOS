"""Application-layer nginx rate-limiter baseline command plan."""

from eval.baselines.base import BaselinePlan


def plan(link_mbps: int) -> BaselinePlan:
    """Return the pinned nginx Compose lifecycle."""
    del link_mbps
    compose = "eval/baselines/app_limiter.compose.yaml"
    return BaselinePlan(
        name="app_limiter",
        setup_commands=(("docker", "compose", "-f", compose, "up", "-d", "--wait"),),
        teardown_commands=(("docker", "compose", "-f", compose, "down", "--remove-orphans"),),
    )
