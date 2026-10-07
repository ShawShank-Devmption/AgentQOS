"""Per-flow feature stage mirror: hand-checked checkpoints and section 7 edge cases."""

from common.contracts import AlpnClass
from ml.extract_features import (
    FlowState,
    PacketObservation,
    checkpoint_vectors,
    update_flow,
)
from tests.test_feature_math import _alpn, _client_hello


def _agent_flow() -> list[PacketObservation]:
    return [
        PacketObservation(1_000_000 + 2_000 * i, 400 if i % 2 == 0 else 1_200, i % 2 == 0)
        for i in range(64)
    ]


def test_agent_constant_pacing_checkpoints() -> None:
    vectors = checkpoint_vectors(_agent_flow(), fanout_new_flows=1)
    assert vectors[6] == (2_000, 0, 6, 300, 58, 252, 1, 0, 0, 9)
    assert vectors[16] == (2_000, 0, 16, 200, 39, 255, 1, 0, 0, 29)
    assert vectors[64] == (2_000, 0, 64, 200, 39, 255, 1, 0, 0, 123)


def test_human_variable_pacing_six_packets() -> None:
    gaps = [0, 100_000, 20_000, 350_000, 5_000, 150_000]
    sizes = [74, 1_500, 66, 900, 66, 1_500]
    timestamp = 0
    packets = []
    for index, (gap, size) in enumerate(zip(gaps, sizes, strict=True)):
        timestamp += gap
        packets.append(PacketObservation(timestamp, size, index % 2 == 0))
    vectors = checkpoint_vectors(packets, fanout_new_flows=1)
    assert vectors == {6: (113_086, 49_707, 6, 51, 10, 84, 1, 0, 0, 610)}


def test_first_gap_seeds_mean_and_zero_gap_is_skipped() -> None:
    state = FlowState()
    for timestamp in (0, 0, 1_000):  # §7.12
        update_flow(state, PacketObservation(timestamp, 100, True))
    assert (state.iat_ewma, state.iat_var_ewma, state.pkt_count) == (1_000, 0, 3)


def test_byte_counters_saturate() -> None:
    state = FlowState(pkt_count=1, bytes_up=(1 << 32) - 10)
    update_flow(state, PacketObservation(10, 100, True))
    assert state.bytes_up == (1 << 32) - 1  # §7.3


def test_only_first_payload_packet_is_parsed_for_tls() -> None:
    hello = _client_hello([(0x0010, _alpn(b"h2"))])
    state = FlowState()
    update_flow(state, PacketObservation(0, 74, True))
    update_flow(state, PacketObservation(10, 66 + len(hello), True, hello))
    update_flow(state, PacketObservation(20, 66 + len(hello), True, _client_hello([])))
    assert (state.tls_ext_count, state.tls_alpn_class) == (1, AlpnClass.H2)


def test_non_tls_first_payload_locks_tls_features_to_fail_open() -> None:
    state = FlowState()
    update_flow(state, PacketObservation(0, 200, True, b"GET / HTTP/1.1\r\n"))
    update_flow(state, PacketObservation(10, 300, True, _client_hello([(0x0010, _alpn(b"h2"))])))
    assert (state.tls_ext_count, state.tls_alpn_class) == (0, AlpnClass.NONE)


def test_short_flow_has_no_checkpoints() -> None:
    assert checkpoint_vectors(_agent_flow()[:5], fanout_new_flows=1) == {}
