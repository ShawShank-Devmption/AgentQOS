"""Calculate collision and accuracy summaries for the P4.4 state-scaling sweep."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StateScalingPoint:
    """One completed state-scaling measurement point."""

    concurrent_flows: int
    collision_count: int
    flow_observations: int
    accuracy: float
    register_bytes: int


@dataclass(frozen=True)
class StateScalingSummary:
    """Derived results for one state-scaling measurement point."""

    concurrent_flows: int
    collision_rate: float
    accuracy: float
    register_bytes: int


def _require_int(value: object, field: str, minimum: int) -> int:
    if not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer greater than or equal to {minimum}")
    return value


def _require_accuracy(value: object) -> float:
    if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError("accuracy must be between zero and one")
    return float(value)


def parse_points(raw_points: Sequence[object]) -> list[StateScalingPoint]:
    """Validate collected scaling points from an append-only experiment output.

    Args:
        raw_points: JSON array of state-scaling records.

    Raises:
        ValueError: If records are malformed, unordered, or duplicate flow counts.
    """
    points: list[StateScalingPoint] = []
    previous_flow_count = 0
    for index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, Mapping):
            raise ValueError(f"point {index} must be a JSON object")
        point = StateScalingPoint(
            concurrent_flows=_require_int(raw_point.get("concurrent_flows"), "concurrent_flows", 1),
            collision_count=_require_int(raw_point.get("collision_count"), "collision_count", 0),
            flow_observations=_require_int(
                raw_point.get("flow_observations"), "flow_observations", 1
            ),
            accuracy=_require_accuracy(raw_point.get("accuracy")),
            register_bytes=_require_int(raw_point.get("register_bytes"), "register_bytes", 0),
        )
        if point.collision_count > point.flow_observations:
            raise ValueError("collision_count cannot exceed flow_observations")
        if point.concurrent_flows <= previous_flow_count:
            raise ValueError("concurrent_flows must be strictly increasing")
        previous_flow_count = point.concurrent_flows
        points.append(point)
    if not points:
        raise ValueError("state-scaling input must contain at least one point")
    return points


def summarize_points(points: Sequence[StateScalingPoint]) -> list[StateScalingSummary]:
    """Calculate collision rate without changing any recorded experiment values."""
    return [
        StateScalingSummary(
            concurrent_flows=point.concurrent_flows,
            collision_rate=point.collision_count / point.flow_observations,
            accuracy=point.accuracy,
            register_bytes=point.register_bytes,
        )
        for point in points
    ]


def write_summary(input_path: Path, output_path: Path) -> list[StateScalingSummary]:
    """Read collected sweep data and write the deterministic analysis summary."""
    try:
        raw_points: Any = json.loads(input_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"could not read state-scaling data: {input_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"state-scaling data is invalid JSON: {input_path}") from error
    if not isinstance(raw_points, Sequence) or isinstance(raw_points, (str, bytes)):
        raise ValueError("state-scaling data must be a JSON array")
    summaries = summarize_points(parse_points(raw_points))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps([asdict(summary) for summary in summaries], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("wrote %d state-scaling summaries to %s", len(summaries), output_path)
    return summaries


def main() -> None:
    """Write collision-rate summaries from a state-scaling JSON dataset."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input sweep JSON path")
    parser.add_argument("output", type=Path, help="output summary JSON path")
    arguments = parser.parse_args()
    write_summary(arguments.input, arguments.output)


if __name__ == "__main__":
    main()
