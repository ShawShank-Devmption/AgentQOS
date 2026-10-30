"""Controller policy startup and serialized refinement service (P3.2/P3.11)."""

from __future__ import annotations

import argparse
import fcntl
import logging
import signal
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from common.compiled_tree import load_compiled_tree
from controller.policy import push_policy
from controller.punt_handler import BoundedPuntQueue, PuntHandler, PuntReceiver
from controller.reclassifier import EpochScheduler, Reclassifier, collect_snapshot
from controller.switch_api import SwitchApi, SwitchApiError

LOGGER = logging.getLogger(__name__)
_SWEEP_PERIOD_US = 2_000_000
_PUNT_QUEUE_CAPACITY = 256


@contextmanager
def controller_owner(thrift_port: int, lock_dir: Path | None = None) -> Iterator[None]:
    """Hold exclusive controller ownership through startup, refinement and shutdown."""
    if not 0 < thrift_port <= 65535:
        raise ValueError("invalid Thrift port")
    directory = lock_dir if lock_dir is not None else Path(tempfile.gettempdir())
    # §7.5: one owner reconciles table handles; restart waits until this process releases it.
    with (directory / f"agent-aware-controller-{thrift_port}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SwitchApiError("another controller owns this switch") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class ControllerService:
    """Consume bounded clone evidence and serialize all epoch/sweep/override operations."""

    def __init__(
        self,
        api: SwitchApi,
        classifier: Reclassifier,
        epochs: EpochScheduler,
        handler: PuntHandler,
        queue: BoundedPuntQueue,
    ) -> None:
        self._api = api
        self._classifier = classifier
        self._epochs = epochs
        self._handler = handler
        self._queue = queue
        self._next_sweep_us = 0

    def step(self, now_us: int) -> None:
        """Process bounded evidence and due work; switch refinement failures remain fail-open."""
        # §7.6: a continually busy producer cannot starve the scheduled sweep.
        for _ in range(_PUNT_QUEUE_CAPACITY):
            clone = self._queue.pop()
            if clone is None:
                break
            observation = self._handler.handle(clone)
            if observation is not None:
                self._classifier.observe(observation)
        try:
            self._epochs.tick(now_us)
        except SwitchApiError:
            LOGGER.exception("Epoch refinement failed; forwarding continues with the tree")
        if now_us < self._next_sweep_us:
            return
        self._next_sweep_us = now_us + _SWEEP_PERIOD_US
        try:
            changes = self._classifier.sweep(collect_snapshot(self._api))
            desired = {
                (
                    flow.client_ip,
                    flow.server_ip,
                    flow.proto,
                    flow.client_port,
                    flow.server_port,
                ): change.traffic_class if change.operation == "upsert" else None
                for change in changes
                for flow in (change.flow,)
            }
            self._api.apply_flow_overrides(desired)
            # §7.5: a partial/uncertain write is never acknowledged; next sweep reconciles.
            self._classifier.acknowledge(changes)
        except SwitchApiError:
            LOGGER.exception("Flow refinement failed; unacknowledged changes will be retried")

    def run(self, stop: threading.Event, intake_failed: threading.Event | None = None) -> None:
        """Run until shutdown; report a dead CPU receiver instead of claiming live refinement."""
        while not stop.is_set():
            if intake_failed is not None and intake_failed.is_set():
                raise RuntimeError("CPU clone intake failed")
            self.step(time.monotonic_ns() // 1000)
            stop.wait(0.05)


def main(argv: Sequence[str] | None = None) -> int:
    """Push the full policy to BMv2 and report the resulting policy version.

    Args:
        argv: Optional command-line arguments for tests or programmatic use.

    Returns:
        Zero when the policy is installed, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-path", type=Path, default=Path("simple_switch_CLI"))
    parser.add_argument("--thrift-port", type=int, default=9090)
    parser.add_argument("--link-mbps", type=int, required=True)
    parser.add_argument("--compiled-tree", type=Path, default=None)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--cpu-interface")
    parser.add_argument("--upstream-port", action="append", type=int, default=[])
    parser.add_argument("--downstream-port", action="append", type=int, default=[])
    parser.add_argument("--agent-ja4", action="append", default=[])
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        tree = load_compiled_tree(args.compiled_tree) if args.compiled_tree else None
        if args.run and (
            tree is None
            or not args.cpu_interface
            or not args.upstream_port
            or not args.downstream_port
        ):
            raise ValueError("--run requires a compiled tree, CPU interface and direction ports")
        handler = (
            PuntHandler(args.upstream_port, args.downstream_port, frozenset(args.agent_ja4))
            if args.run
            else None
        )
        api = SwitchApi(args.cli_path, args.thrift_port)
        with controller_owner(args.thrift_port):
            version = push_policy(api, args.link_mbps, tree)
            LOGGER.info("controller policy installed; version=%d", version)
            if args.run:
                stop = threading.Event()
                previous = {
                    signum: signal.signal(signum, lambda _sig, _frame: stop.set())
                    for signum in (signal.SIGINT, signal.SIGTERM)
                }
                try:
                    queue = BoundedPuntQueue(_PUNT_QUEUE_CAPACITY)
                    service = ControllerService(
                        api, Reclassifier(tree), EpochScheduler(api), handler, queue
                    )
                    with PuntReceiver(args.cpu_interface, queue) as receiver:
                        LOGGER.info(
                            "controller live intake ready; interface=%s", args.cpu_interface
                        )
                        service.run(stop, receiver.failed)
                finally:
                    for signum, old_handler in previous.items():
                        signal.signal(signum, old_handler)
    except (OSError, SwitchApiError, ValueError, RuntimeError) as exc:
        LOGGER.error("controller startup failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
