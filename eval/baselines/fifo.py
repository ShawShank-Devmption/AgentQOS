"""Owned minimal-l2fwd FIFO network for design section 10 (P4.1)."""

from __future__ import annotations

import fcntl
import json
import logging
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from controller.switch_api import SwitchApi
from harness.runtime import (
    RuntimeConfig,
    RuntimePrerequisiteError,
    configure_switch_queues,
    create_network,
    install_l2_forwarding,
    validate_prerequisites,
    wait_for_switch,
)
from harness.topology import HOSTS, topology_manifest

logger = logging.getLogger(__name__)


def table_names(pipeline_json: Path) -> set[str]:
    """Read compiled BMv2 table names, failing before any network allocation."""
    try:
        artifact = json.loads(pipeline_json.read_text(encoding="utf-8"))
        return {
            table["name"].rsplit(".", 1)[-1]
            for pipeline in artifact["pipelines"]
            for table in pipeline["tables"]
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimePrerequisiteError("expected readable compiled BMv2 pipeline tables") from exc


def validate_pipeline(pipeline_json: Path) -> None:
    """Require the existing minimal forwarding table and no classification pipeline."""
    tables = table_names(pipeline_json)
    if tables != {"tbl_l2_forward"}:
        raise RuntimePrerequisiteError("FIFO requires minimal l2fwd with only tbl_l2_forward")


@contextmanager
def running_fifo(config: RuntimeConfig) -> Iterator[Any]:
    """Start an exclusive l2fwd network and stop it after the caller's real workload.

    The yielded Mininet network owns all interfaces; no preexisting network is reused. The
    caller supplies traffic and measurement, and must not label this lifecycle check as a run.
    """
    with running_owned_network(
        config,
        "fifo",
        "BMv2 minimal l2fwd; Linux bottleneck FIFO",
        validate_pipeline,
        lambda api: None,
    ) as network:
        yield network


@contextmanager
def running_owned_network(
    config: RuntimeConfig,
    system: str,
    mechanism: str,
    pipeline_validator: Callable[[Path], None],
    install_policy: Callable[[SwitchApi], None],
) -> Iterator[Any]:
    """Own one choke_v1 network while a baseline installs its specific policy."""
    pipeline_validator(config.pipeline_json)
    lock_path = Path(tempfile.gettempdir()) / "agent-aware-choke-v1-runtime.lock"
    with lock_path.open("a") as lock:
        # §7.19: serialize the fixed choke_v1 interface names across all baseline runtimes.
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimePrerequisiteError("another choke_v1 runtime is active") from exc
        validate_prerequisites(config)
        config.output_dir.mkdir(parents=True, exist_ok=False)
        network = None
        failed = False
        manifest: dict[str, Any] = {
            "system": system,
            "mechanism": mechanism,
            "topology": topology_manifest(config.link_mbps),
            "queue_rate_pps": config.queue_rate_pps,
            "installed": False,
        }
        try:
            network = create_network(config)
            network.build()
            network.start()
            api = SwitchApi(thrift_port=config.thrift_port)
            wait_for_switch(api)
            configure_switch_queues(api, config)
            install_l2_forwarding(api)
            install_policy(api)
            for host in HOSTS:
                for peer in HOSTS:
                    if host != peer:
                        network.get(host.name).setARP(
                            peer.ip_address.split("/")[0], peer.mac_address
                        )
            manifest["installed"] = True
            yield network
        except BaseException as exc:
            failed = True
            manifest["error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("owned baseline workload failed")
            raise
        finally:
            try:
                if network is not None:
                    network.stop()
                manifest["cleanup"] = "passed"
            except Exception:
                manifest["cleanup"] = "failed"
                logger.exception("failed to stop owned baseline network")
                if not failed:
                    raise
            finally:
                (config.output_dir / "baseline.json").write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
