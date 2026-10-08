"""Shared immutable command plans for the four evaluation baselines."""

from dataclasses import dataclass
from pathlib import Path

from common.contracts import EXPERIMENT_SYSTEMS


@dataclass(frozen=True)
class BaselinePlan:
    """External setup and teardown commands for one system variant."""

    name: str
    setup_commands: tuple[tuple[str, ...], ...]
    teardown_commands: tuple[tuple[str, ...], ...]


def baseline_plan(
    system: str,
    link_mbps: int,
    output_dir: Path = Path("/tmp/agentqos"),
) -> BaselinePlan:
    """Build the lifecycle command plan for one frozen system name.

    Args:
        system: One value from `common.contracts.EXPERIMENT_SYSTEMS`.
        link_mbps: Configured bottleneck rate used by applicable baselines.
        output_dir: Run-owned directory for baseline process state.

    Returns:
        Deterministic setup and teardown commands.

    Raises:
        ValueError: If the system name is not frozen by design section 4.6.
    """
    if system not in EXPERIMENT_SYSTEMS:
        raise ValueError(f"unknown experiment system: {system}")
    if system == "ours":
        return BaselinePlan("ours", (), ())
    if system == "fifo":
        from eval.baselines.fifo import plan
    elif system == "diffserv":
        from eval.baselines.diffserv import plan
    elif system == "fairq":
        from eval.baselines.fairq import plan
    else:
        from eval.baselines.app_limiter import plan

        return plan(link_mbps, output_dir)
    return plan(link_mbps)
