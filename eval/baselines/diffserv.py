"""Static port-based DiffServ baseline command plan."""

from eval.baselines.base import BaselinePlan


def plan(link_mbps: int) -> BaselinePlan:
    """Return static MCP-port marking plus three priority bands."""
    del link_mbps
    mark_command = (
        "iptables",
        "-t",
        "mangle",
        "-A",
        "POSTROUTING",
        "-p",
        "tcp",
        "--dport",
        "8080",
        "-j",
        "DSCP",
        "--set-dscp",
        "18",
    )
    return BaselinePlan(
        name="diffserv",
        setup_commands=(
            ("tc", "qdisc", "replace", "dev", "s1-eth4", "root", "handle", "1:", "prio"),
            mark_command,
        ),
        teardown_commands=(
            ("iptables", "-t", "mangle", "-D", *mark_command[4:]),
            ("tc", "qdisc", "del", "dev", "s1-eth4", "root"),
        ),
    )
