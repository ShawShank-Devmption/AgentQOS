"""D4: declare a HUMAN p99 target before runs, then validate measured QoS evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.contracts import TrafficClass
from eval.metrics import load_observations, summarize_observations, write_json


def declare_target(path: Path, target_ms: float) -> None:
    """Archive an explicit target before either measured run starts; never overwrite."""
    if isinstance(target_ms, bool) or not math.isfinite(target_ms) or target_ms <= 0:
        raise ValueError("HUMAN p99 target must be positive and finite")
    write_json(
        path, {"declared_at": datetime.now(UTC).isoformat(), "human_p99_target_ms": target_ms}
    )


def _run(path: Path, system: str, declared_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    started = json.loads((path / "started.json").read_text())
    completed = json.loads((path / "completed.json").read_text())
    if completed.get("status") != "success" or started["config"]["system"] != system:
        raise ValueError(f"expected a successful {system} run: {path}")
    if datetime.fromisoformat(started["started_at"]) < declared_at:
        raise ValueError("target must be declared before both runs start")
    digest = hashlib.sha256((path / "observations.json").read_bytes()).hexdigest()
    if digest != completed.get("observations_sha256"):
        raise ValueError("observations hash mismatch")
    return started, load_observations(path / "observations.json")


def qos_report(target: Path, ours: Path, fifo: Path, ptf_log: Path) -> dict[str, Any]:
    """Require comparable runs, checkpoint labels and PTF evidence; report absent data honestly."""
    declaration = json.loads(target.read_text())
    target_ms = declaration["human_p99_target_ms"]
    if isinstance(target_ms, bool) or not isinstance(target_ms, (int, float)):
        raise ValueError("invalid target declaration")
    if not math.isfinite(target_ms) or target_ms <= 0:
        raise ValueError("invalid target declaration")
    declared_at = datetime.fromisoformat(declaration["declared_at"])
    our_start, our_raw = _run(ours, "ours", declared_at)
    fifo_start, fifo_raw = _run(fifo, "fifo", declared_at)
    for name in ("host", "seed", "agent_share_pct", "burst_intensity"):
        if our_start[name] != fifo_start[name]:
            raise ValueError(f"paired runs differ in {name}")
    for name in ("topology", "link_mbps", "duration_s"):
        if our_start["config"][name] != fifo_start["config"][name]:
            raise ValueError(f"paired configs differ in {name}")
    # Compare full seeded traffic plans, not just matching experiment names.
    if (ours / "workload-plan.json").read_bytes() != (fifo / "workload-plan.json").read_bytes():
        raise ValueError("paired traffic plans differ")
    reports = [summarize_observations(raw) for raw in (our_raw, fifo_raw)]
    latencies = [
        report["classes"][TrafficClass.HUMAN_INTERACTIVE.name]["latency_ms"] for report in reports
    ]
    our_p99 = None if latencies[0] is None else latencies[0]["p99"]
    fifo_p99 = None if latencies[1] is None else latencies[1]["p99"]
    ratio = None if our_p99 is None or fifo_p99 is None or fifo_p99 <= 0 else our_p99 / fifo_p99
    classifications = [row for row in our_raw["classifications"] if row["checkpoint"] == 16]
    required = {int(TrafficClass.HUMAN_INTERACTIVE), int(TrafficClass.AGENT_BULK)}
    classification_ready = required <= {row["truth"] for row in classifications} and all(
        row["predicted"] == row["truth"] for row in classifications if row["truth"] in required
    )
    log = ptf_log.read_text()
    transitions = all(f"D4 real_queue congestion_flag={flag} " in log for flag in (0, 1))
    protective_selected = all(
        f"D4 isolated_selection preset={flag} forwarded={1 - flag}" in log for flag in (0, 1)
    )
    red_cases = re.findall(
        r"D4 isolated_red class=(\w+) ecn=(\d+) forwarded=(\d+) meter_drops=(\d+)", log
    )
    cases = {
        (label, int(ecn)): (int(forwarded), int(drops))
        for label, ecn, forwarded, drops in red_cases
    }
    expected = {
        ("UNKNOWN", 0): (1, 0),
        ("HUMAN_INTERACTIVE", 0): (1, 0),
        ("AGENT_INTERACTIVE", 0): (1, 0),
        ("AGENT_INTERACTIVE", 2): (1, 0),
        ("AGENT_BULK", 0): (0, 1),
        ("AGENT_BULK", 2): (1, 0),
    }
    red_behavior = all(cases.get(case) == result for case, result in expected.items())
    ptf_ok = bool(re.search(r"^OK(?:\s|$)", log, re.MULTILINE)) and "FAILED" not in log
    target_pass = None if our_p99 is None else our_p99 <= target_ms
    human_loss_free = reports[0]["classes"]["HUMAN_INTERACTIVE"]["lost"] == 0
    return {
        "human_p99_target_ms": target_ms,
        "ours_human_p99_ms": our_p99,
        "fifo_human_p99_ms": fifo_p99,
        "human_p99_ratio": ratio,
        "target_pass": target_pass,
        "classification_checkpoint_16_ready": classification_ready,
        "ptf_queue_transitions": transitions,
        "ptf_protective_selection": protective_selected,
        "ptf_red_behavior": red_behavior,
        "ptf_suite_ok": ptf_ok,
        "evidence_pass": target_pass is True
        and human_loss_free
        and ratio is not None
        and ratio < 1
        and classification_ready
        and transitions
        and protective_selected
        and red_behavior
        and ptf_ok,
        "human_lost_packets": [
            report["classes"]["HUMAN_INTERACTIVE"]["lost"] for report in reports
        ],
        "source_paths": {
            "target": str(target),
            "ours": str(ours),
            "fifo": str(fifo),
            "ptf_log": str(ptf_log),
        },
        "ptf_log_sha256": hashlib.sha256(ptf_log.read_bytes()).hexdigest(),
        "note": "PTF meter counts are isolated test evidence, not live workload drop counters. "
        "D2 shape provenance and C4 overhead readiness require separate review.",
    }


def main() -> None:
    """Declare a target or write a new evidence report without modifying measured inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    declare = commands.add_parser("declare-target")
    declare.add_argument("--target-ms", type=float, required=True)
    declare.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("report")
    for name in ("target", "ours", "fifo", "ptf-log", "output"):
        report.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "declare-target":
        declare_target(args.output, args.target_ms)
    else:
        write_json(args.output, qos_report(args.target, args.ours, args.fifo, args.ptf_log))


if __name__ == "__main__":
    main()
