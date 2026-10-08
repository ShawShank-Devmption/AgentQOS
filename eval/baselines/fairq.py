"""Per-flow fair-queueing baseline command plan."""

from eval.baselines.base import BaselinePlan


def plan(link_mbps: int) -> BaselinePlan:
    """Return Linux flow-hash fair queueing at the bottleneck."""
    return BaselinePlan(
        name="fairq",
        setup_commands=(
            (
                "tc",
                "qdisc",
                "replace",
                "dev",
                "s1-eth7",
                "root",
                "handle",
                "1:",
                "htb",
                "default",
                "10",
            ),
            (
                "tc",
                "class",
                "replace",
                "dev",
                "s1-eth7",
                "parent",
                "1:",
                "classid",
                "1:10",
                "htb",
                "rate",
                f"{link_mbps}mbit",
                "ceil",
                f"{link_mbps}mbit",
            ),
            (
                "tc",
                "qdisc",
                "replace",
                "dev",
                "s1-eth7",
                "parent",
                "1:10",
                "handle",
                "10:",
                "sfq",
                "perturb",
                "10",
            ),
        ),
        teardown_commands=(("tc", "qdisc", "del", "dev", "s1-eth7", "root"),),
    )
