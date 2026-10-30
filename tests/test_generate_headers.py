"""P2.1: generated wire and feature layouts consume the frozen contracts."""

import re
from pathlib import Path

from common.contracts import CPU_HEADER_FIELDS, FEATURE_BIT_WIDTHS, FEATURE_ORDER, TREE_NODE_BITS
from ml.generate_headers import generate_headers, main


def _fields(source: str, kind: str, name: str) -> list[tuple[str, int]]:
    body = re.search(rf"{kind} {name} \{{(.*?)\}}", source, re.S)
    assert body is not None
    return [(name, int(bits)) for bits, name in re.findall(r"bit<(\d+)> (\w+);", body[1])]


def test_cpu_and_feature_layouts_preserve_frozen_order_and_widths() -> None:
    source = generate_headers()
    assert _fields(source, "header", "cpu_header_t") == list(CPU_HEADER_FIELDS)
    assert _fields(source, "struct", "features_t") == list(
        zip(FEATURE_ORDER, FEATURE_BIT_WIDTHS, strict=True)
    )
    assert f"bit<{TREE_NODE_BITS}> tree_node;" in source
    assert "features_t features;" in source


def test_checked_in_include_is_exactly_reproducible() -> None:
    assert Path("p4src/headers.p4").read_text() == generate_headers()


def test_cli_checks_drift_without_overwriting(tmp_path: Path) -> None:
    destination = tmp_path / "headers.p4"
    assert main(["--output", str(destination)]) == 0
    original = destination.read_text()
    assert main(["--output", str(destination), "--check"]) == 0
    destination.write_text(original + "// drift\n")
    assert main(["--output", str(destination), "--check"]) == 1
    assert destination.read_text().endswith("// drift\n")


def test_check_missing_output_returns_failure(tmp_path: Path) -> None:
    assert main(["--output", str(tmp_path / "absent.p4"), "--check"]) == 1


def test_pipeline_constants_and_metadata_are_generated_from_contracts() -> None:
    from common import contracts

    source = generate_headers()
    for name in (
        "FLOW_SLOTS",
        "WARMUP_PACKETS",
        "IDLE_TIMEOUT_US",
        "EWMA_SHIFT",
        "CM_SKETCH_COLUMNS",
        "TLS_MAX_EXTENSIONS",
        "PROTO_META_PAYLOAD_SEEN",
    ):
        assert f" {name} = {getattr(contracts, name)};" in source
    assert "bit<32> client_ip;" in source
    assert "bit<16> flow_hash;" in source
    assert "bit<1> new_flow;" in source
