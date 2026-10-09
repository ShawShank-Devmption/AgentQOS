"""The reference arithmetic must keep reproducing the P2.4 worked examples Dev A's PTF uses."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from common.contracts import FEATURE_CHECKPOINTS
from common.feature_math import flow_key_bytes, flow_slot, flow_tag
from ml.extract_features import FlowState, PacketObservation, feature_vector, update_flow

FIXTURE = Path(__file__).parent / "fixtures" / "feature_worked_examples.json"
SCENARIOS = json.loads(FIXTURE.read_text(encoding="utf-8"))["scenarios"]


def _replay(scenario: dict) -> dict:
    state = FlowState()
    checkpoints = {}
    for index, packet in enumerate(scenario["packets"], start=1):
        update_flow(
            state,
            PacketObservation(
                packet["timestamp_us"],
                packet["size_bytes"],
                packet["upstream"],
                bytes.fromhex(packet["payload_hex"]),
            ),
        )
        if index in FEATURE_CHECKPOINTS:
            registers = json.loads(json.dumps(asdict(state)))
            features = list(feature_vector(state, scenario["fanout_new_flows"]))
            checkpoints[str(index)] = {"features": features, "registers": registers}
    key = flow_key_bytes(**scenario["flow"])
    return {"slot": flow_slot(key), "tag": flow_tag(key), "checkpoints": checkpoints}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_reference_reproduces_worked_example(scenario: dict) -> None:
    assert _replay(scenario) == scenario["expected"]


def test_examples_cover_every_checkpoint() -> None:
    reached = {cp for s in SCENARIOS for cp in s["expected"]["checkpoints"]}
    assert reached == {str(cp) for cp in FEATURE_CHECKPOINTS}


def test_fixture_contains_hand_checked_values() -> None:
    by_name = {s["name"]: s["expected"]["checkpoints"] for s in SCENARIOS}
    assert by_name["agent_constant_pacing"]["64"]["features"] == [
        2000,
        0,
        64,
        200,
        39,
        255,
        1,
        0,
        0,
        123,
    ]
    assert by_name["human_variable_pacing"]["6"]["features"] == [
        113086,
        49707,
        6,
        51,
        10,
        84,
        1,
        0,
        0,
        610,
    ]
    assert by_name["tls_client_hello"]["6"]["features"][7:9] == [3, 2]
