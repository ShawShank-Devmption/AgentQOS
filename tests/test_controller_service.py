"""Daemon wiring: refinement retries never change packet forwarding (§7.5/§7.6)."""

import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from common.contracts import TrafficClass
from controller.app import ControllerService, controller_owner
from controller.punt_handler import BoundedPuntQueue, FlowIdentity, FlowObservation
from controller.reclassifier import OverrideChange
from controller.switch_api import SwitchApiError

FLOW = FlowIdentity(0x0A000001, 0x0A000064, 6, 40000, 443)


def test_service_applies_and_acknowledges_only_successful_changes() -> None:
    api, classifier, epochs, handler = Mock(), Mock(), Mock(), Mock()
    change = OverrideChange("upsert", FLOW, TrafficClass.AGENT_INTERACTIVE)
    classifier.sweep.return_value = (change,)
    queue = BoundedPuntQueue(2)
    queue.offer(b"clone")
    observation = FlowObservation(1, FLOW, TrafficClass.UNKNOWN)
    handler.handle.return_value = observation
    service = ControllerService(api, classifier, epochs, handler, queue)
    with patch("controller.app.collect_snapshot", return_value="snapshot"):
        service.step(0)
    classifier.observe.assert_called_once_with(observation)
    epochs.tick.assert_called_once_with(0)
    api.apply_flow_overrides.assert_called_once_with(
        {(0x0A000001, 0x0A000064, 6, 40000, 443): TrafficClass.AGENT_INTERACTIVE}
    )
    classifier.acknowledge.assert_called_once_with((change,))


def test_service_retries_uncertain_writes_and_does_not_catch_up_sweeps() -> None:
    api, classifier, epochs = Mock(), Mock(), Mock()
    change = OverrideChange("delete", FLOW, None)
    classifier.sweep.return_value = (change,)
    api.apply_flow_overrides.side_effect = [SwitchApiError("partial"), None]
    service = ControllerService(api, classifier, epochs, Mock(), BoundedPuntQueue(1))
    with patch("controller.app.collect_snapshot", return_value="snapshot") as snapshot:
        service.step(0)
        classifier.acknowledge.assert_not_called()
        service.step(1_000_000)
        assert snapshot.call_count == 1
        service.step(8_000_000)
    assert snapshot.call_count == 2
    classifier.acknowledge.assert_called_once_with((change,))


def test_epoch_or_sweep_failure_is_logged_and_later_sweeps_continue() -> None:
    api, classifier, epochs = Mock(), Mock(), Mock()
    classifier.sweep.return_value = ()
    epochs.tick.side_effect = [SwitchApiError("down"), True]
    service = ControllerService(api, classifier, epochs, Mock(), BoundedPuntQueue(1))
    with patch("controller.app.collect_snapshot", side_effect=[SwitchApiError("down"), "snapshot"]):
        service.step(0)
        service.step(2_000_000)
    classifier.sweep.assert_called_once_with("snapshot")


def test_owner_lock_refuses_a_second_controller(tmp_path: Path) -> None:
    with (
        controller_owner(9091, lock_dir=tmp_path),
        pytest.raises(SwitchApiError),
        controller_owner(9091, lock_dir=tmp_path),
    ):
        pytest.fail("second owner acquired the switch")


def test_stopped_service_does_no_work() -> None:
    stop = threading.Event()
    stop.set()
    service = ControllerService(Mock(), Mock(), Mock(), Mock(), BoundedPuntQueue(1))
    with patch.object(service, "step") as step:
        service.run(stop)
    step.assert_not_called()


def test_live_service_reports_failed_cpu_clone_intake() -> None:
    failed = threading.Event()
    failed.set()
    service = ControllerService(Mock(), Mock(), Mock(), Mock(), BoundedPuntQueue(1))
    with pytest.raises(RuntimeError, match="CPU clone intake failed"):
        service.run(threading.Event(), failed)


def test_empty_refiner_avoids_switch_snapshot_but_keeps_epoch_maintenance() -> None:
    from common.compiled_tree import load_compiled_tree
    from controller.reclassifier import Reclassifier

    api, epochs = Mock(), Mock()
    classifier = Reclassifier(load_compiled_tree(Path("tests/fixtures/smoke_tree.json")))
    service = ControllerService(api, classifier, epochs, Mock(), BoundedPuntQueue(1))
    with patch("controller.app.collect_snapshot") as snapshot:
        service.step(0)
        service.step(2_000_000)
    snapshot.assert_not_called()
    assert epochs.tick.call_count == 2
    api.apply_flow_overrides.assert_not_called()


def test_first_identity_and_unacknowledged_override_require_snapshots() -> None:
    from common.compiled_tree import load_compiled_tree
    from controller.reclassifier import Reclassifier

    tree = load_compiled_tree(Path("tests/fixtures/smoke_tree.json"))
    classifier = Reclassifier(tree)
    assert classifier.needs_snapshot is False
    classifier.observe(FlowObservation(1, FLOW, TrafficClass.UNKNOWN))
    assert classifier.needs_snapshot is True
    replacement = Reclassifier(tree)
    replacement.acknowledge((OverrideChange("upsert", FLOW, TrafficClass.AGENT_INTERACTIVE),))
    assert replacement.needs_snapshot is True
    replacement.acknowledge((OverrideChange("delete", FLOW, None),))
    assert replacement.needs_snapshot is False
