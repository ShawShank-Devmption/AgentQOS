"""FIFO/best-effort baseline command plan."""

from eval.baselines.base import BaselinePlan


def plan(link_mbps: int) -> BaselinePlan:
    """Return a single FIFO queue at the configured bottleneck rate."""
    return BaselinePlan(
        name="fifo",
        setup_commands=(
            (
                "tc",
                "qdisc",
                "replace",
                "dev",
                "s1-eth7",
                "root",
                "tbf",
                "rate",
                f"{link_mbps}mbit",
                "burst",
                "32kbit",
                "latency",
                "400ms",
            ),
        ),
        teardown_commands=(("tc", "qdisc", "del", "dev", "s1-eth7", "root"),),
    )
