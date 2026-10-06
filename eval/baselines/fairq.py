"""Per-flow fair-queueing baseline command plan."""

from eval.baselines.base import BaselinePlan


def plan(link_mbps: int) -> BaselinePlan:
    """Return Linux flow-hash fair queueing at the bottleneck."""
    del link_mbps
    return BaselinePlan(
        name="fairq",
        setup_commands=(
            (
                "tc",
                "qdisc",
                "replace",
                "dev",
                "s1-eth4",
                "root",
                "fq",
                "buckets",
                "65536",
            ),
        ),
        teardown_commands=(("tc", "qdisc", "del", "dev", "s1-eth4", "root"),),
    )
