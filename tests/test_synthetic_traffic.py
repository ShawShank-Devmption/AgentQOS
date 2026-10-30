"""Synthetic packet-level corpus: schema, determinism, ground truth, TLS and sketch modeling."""

from pathlib import Path

import pandas as pd

from common.contracts import FEATURE_CHECKPOINTS, SKETCH_EPOCH_US, AlpnClass, TrafficClass
from common.feature_math import parse_client_hello_meta
from harness.synthetic_traffic import (
    MAX_PACKETS,
    MSS_BYTES,
    PROFILES,
    SyntheticFlow,
    corpus_frame,
    fanout_estimates,
    generate_flows,
    load_client_hellos,
    main,
)
from ml.extract_features import PacketObservation
from ml.train import train, validate_feature_frame

FLOWS = generate_flows(seed=5, clients_per_profile=6)
FRAME = corpus_frame(FLOWS)


def test_frame_satisfies_the_training_contract_for_every_profile() -> None:
    validate_feature_frame(FRAME)
    assert set(FRAME["source_framework"]) == {f"synthetic:{name}" for name in PROFILES}
    assert set(FRAME["checkpoint"]) == set(FEATURE_CHECKPOINTS)


def test_generation_is_seeded() -> None:
    assert corpus_frame(generate_flows(seed=5, clients_per_profile=6)).equals(FRAME)
    assert not corpus_frame(generate_flows(seed=6, clients_per_profile=6)).equals(FRAME)


def test_labels_come_from_the_generating_profile_only() -> None:
    # 7.20: ground truth is the orchestration side (which profile emitted the flow).
    for source, labels in FRAME.groupby("source_framework")["label"]:
        assert set(labels) == {int(PROFILES[source.removeprefix("synthetic:")][0])}


def test_flows_are_time_ordered_and_capped_at_the_last_checkpoint() -> None:
    for flow in FLOWS:
        stamps = [packet.timestamp_us for packet in flow.packets]
        assert stamps == sorted(stamps)
        assert len(flow.packets) <= MAX_PACKETS


def test_short_flows_produce_no_rows_below_their_packet_count() -> None:
    reached = FRAME.groupby("flow_id")["checkpoint"].max()
    for flow in FLOWS:
        expected = [c for c in FEATURE_CHECKPOINTS if c <= len(flow.packets)]
        assert reached.get(flow.flow_id, 0) == (expected[-1] if expected else 0)


def test_extractor_sees_only_the_first_clienthello_segment() -> None:
    # Chrome's post-quantum ClientHello spans two segments, so ALPN lies past the first one.
    chrome = load_client_hellos()["chrome"]
    assert len(chrome) > MSS_BYTES
    assert parse_client_hello_meta(chrome[:MSS_BYTES]) == (7, AlpnClass.NONE)
    browsing = FRAME[FRAME["source_framework"] == "synthetic:human_browsing"]
    assert set(browsing["tls_ext_count"]) == {7}
    assert set(browsing["tls_alpn_class"]) == {int(AlpnClass.NONE)}


def test_non_tls_media_fails_open_to_no_tls_features() -> None:
    video = FRAME[FRAME["source_framework"] == "synthetic:human_video_call"]
    assert set(video["tls_ext_count"]) == {0}
    assert set(video["tls_alpn_class"]) == {int(AlpnClass.NONE)}


def _flow(flow_id: str, client: str, stamps: list[int]) -> SyntheticFlow:
    packets = tuple(PacketObservation(ts, 100, True) for ts in stamps)
    return SyntheticFlow(flow_id, client, stamps[0], packets, TrafficClass.AGENT_BULK, "test")


def test_fanout_counts_same_client_claims_in_the_active_epoch_only() -> None:
    # 7.10: the reading epoch holds only claims since its flip; other clients never count.
    epoch = SKETCH_EPOCH_US
    flows = [
        _flow("origin", "b", list(range(6))),
        _flow("early", "a", [epoch - 10 + 3 * i for i in range(6)]),  # claimed in epoch 0
        _flow("same", "a", [epoch + 8 + i for i in range(6)]),
        _flow("later", "a", [epoch + 50 + i for i in range(6)]),
        _flow("other", "b", [epoch + 1 + i for i in range(6)]),
    ]
    assert fanout_estimates(flows) == {"origin": 1, "early": 0, "same": 1, "later": 2, "other": 1}


def test_fanout_epochs_start_at_the_first_captured_packet() -> None:
    epoch = SKETCH_EPOCH_US
    flows = [
        _flow("first", "a", [epoch - 10 + 3 * i for i in range(6)]),
        _flow("second", "a", [epoch + 10 + i for i in range(6)]),
    ]
    # Neither checkpoint is one full epoch after the capture origin.
    assert fanout_estimates(flows) == {"first": 1, "second": 2}


def test_corpus_reads_fanout_at_each_checkpoint_without_future_claims() -> None:
    epoch = SKETCH_EPOCH_US
    stamps = list(range(6)) + list(range(100, 110)) + list(range(epoch, epoch + 48))
    flows = [
        _flow("observed", "a", stamps),
        _flow("new", "a", [50]),  # Short flows still contribute a claim.
        _flow("future", "a", [200]),
        _flow("next_epoch", "a", [epoch]),
        _flow("other_client", "b", [60]),
    ]
    rows = corpus_frame(flows).set_index("checkpoint")
    assert rows["fanout_new_flows"].to_dict() == {6: 1, 16: 2, 64: 1}


def test_fanout_claim_uses_the_first_packet_not_the_scheduled_start() -> None:
    first = _flow("observed", "a", list(range(6)))
    delayed = SyntheticFlow(
        "delayed", "a", 0, (PacketObservation(20, 100, True),), TrafficClass.AGENT_BULK, "test"
    )
    assert fanout_estimates([first, delayed])["observed"] == 1


def test_cli_output_trains(tmp_path: Path) -> None:
    out = tmp_path / "features.csv"
    assert main(["--seed", "2", "--clients-per-profile", "6", "--out", str(out)]) == 0
    result = train(pd.read_csv(out), seed=2)
    assert result.report["depth"] <= 8
