"""Static application-port DiffServ on the owned BMv2 choke point."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.contracts import (
    CLASS_ACTION_TABLE,
    CLASS_TREATMENTS,
    CONGESTION_FLAG_REGISTER,
    METER_AGENT_BULK,
    METER_AGENT_INTERACTIVE,
    TREE_TABLE_NAMES,
    ClassTreatment,
    TrafficClass,
)
from controller.policy import class_policy_commands
from controller.switch_api import SwitchApi
from eval.baselines.fifo import running_owned_network, table_names
from harness.runtime import RuntimeConfig, RuntimePrerequisiteError

_PORT_TABLE = "tbl_diffserv_port"  # DESIGN GAP: baseline-only table, outside frozen system names.
_PORT_ACTION = "set_diffserv_class"


@dataclass(frozen=True)
class PortRule:
    """Map one TCP/UDP destination port to an existing class treatment."""

    protocol: str
    destination_port: int
    traffic_class: TrafficClass

    def __post_init__(self) -> None:
        if self.protocol not in ("tcp", "udp"):
            raise ValueError("protocol must be tcp or udp")
        if type(self.destination_port) is not int or not 1 <= self.destination_port <= 65_535:
            raise ValueError("destination_port must be an integer between 1 and 65535")
        if not isinstance(self.traffic_class, TrafficClass):
            raise ValueError("traffic_class must be a frozen TrafficClass")


@dataclass(frozen=True)
class DiffServConfig:
    """Explicit nonoverlapping port map; unmatched traffic retains UNKNOWN treatment."""

    rules: tuple[PortRule, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.rules, tuple) or not all(
            isinstance(rule, PortRule) for rule in self.rules
        ):
            raise ValueError("rules must be a tuple of PortRule values")
        keys = [(rule.protocol, rule.destination_port) for rule in self.rules]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate protocol/destination-port rule")

    def treatment(self, protocol: str, destination_port: int) -> ClassTreatment:
        """Resolve the frozen DSCP/queue treatment for a packet's transport port."""
        for rule in self.rules:
            if (rule.protocol, rule.destination_port) == (protocol, destination_port):
                return CLASS_TREATMENTS[rule.traffic_class]
        return CLASS_TREATMENTS[TrafficClass.UNKNOWN]

    def validate_pipeline(self, pipeline_json: Path) -> None:
        """Require the baseline's static port and frozen class-action tables."""
        tables = table_names(pipeline_json)
        if not {"tbl_l2_forward", _PORT_TABLE, CLASS_ACTION_TABLE} <= tables:
            raise RuntimePrerequisiteError(
                f"DiffServ requires compiled {_PORT_TABLE} and {CLASS_ACTION_TABLE}; "
                "the minimal l2fwd artifact cannot classify by application port"
            )
        if set(TREE_TABLE_NAMES) & tables:
            raise RuntimePrerequisiteError("DiffServ baseline must not contain learned tree tables")
        try:
            artifact = json.loads(pipeline_json.read_text(encoding="utf-8"))
            meters = {entry["name"].rsplit(".", 1)[-1] for entry in artifact["meter_arrays"]}
            registers = {entry["name"].rsplit(".", 1)[-1] for entry in artifact["register_arrays"]}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RuntimePrerequisiteError(
                "DiffServ requires compiled meter/register metadata"
            ) from exc
        if not {METER_AGENT_INTERACTIVE, METER_AGENT_BULK} <= meters:
            raise RuntimePrerequisiteError("DiffServ artifact lacks frozen class meters")
        if CONGESTION_FLAG_REGISTER not in registers:
            raise RuntimePrerequisiteError("DiffServ artifact lacks congestion protection register")

    def port_rule_commands(self) -> list[str]:
        """Build switch CLI entries for exact protocol and application-port matches."""
        commands = [
            f"table_set_default {_PORT_TABLE} {_PORT_ACTION} {int(TrafficClass.UNKNOWN)}",
            f"table_clear {_PORT_TABLE}",
        ]
        commands.extend(
            f"table_add {_PORT_TABLE} {_PORT_ACTION} "
            f"{socket.IPPROTO_TCP if rule.protocol == 'tcp' else socket.IPPROTO_UDP} "
            f"{rule.destination_port} => {int(rule.traffic_class)}"
            for rule in self.rules
        )
        return commands


@contextmanager
def running_diffserv(runtime: RuntimeConfig, config: DiffServConfig) -> Iterator[Any]:
    """Install static port rules and frozen class treatment on an owned switch."""

    def install(api: SwitchApi) -> None:
        api.execute(class_policy_commands(runtime.link_mbps))
        api.execute(config.port_rule_commands())

    with running_owned_network(
        runtime,
        "diffserv",
        "BMv2 static protocol/application-port classification and shared class QoS",
        config.validate_pipeline,
        install,
    ) as network:
        (runtime.output_dir / "diffserv.json").write_text(
            json.dumps(
                {
                    "rules": [
                        {
                            "protocol": rule.protocol,
                            "destination_port": rule.destination_port,
                            "traffic_class": int(rule.traffic_class),
                        }
                        for rule in config.rules
                    ],
                    "port_commands": config.port_rule_commands(),
                    "class_commands": class_policy_commands(runtime.link_mbps),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        yield network
