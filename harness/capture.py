"""Ground-truth flow labeling from orchestration windows and captured packets."""

import argparse
import csv
import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from ipaddress import IPv4Address
from pathlib import Path

from scapy.all import IP, TCP, UDP, PcapReader
from scapy.error import Scapy_Exception

from common.contracts import LABEL_FIELDS, TRAINING_LABELS, TrafficClass

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureWindow:
    """Ground-truth interval emitted by a traffic runner."""

    source_ip: IPv4Address
    start_time_s: Decimal
    end_time_s: Decimal
    label: TrafficClass
    source_framework: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_ip, IPv4Address):
            raise TypeError("source_ip must be an IPv4Address")
        if not isinstance(self.start_time_s, Decimal) or not isinstance(self.end_time_s, Decimal):
            raise TypeError("capture window timestamps must be Decimal values")
        if not self.start_time_s.is_finite() or not self.end_time_s.is_finite():
            raise ValueError("capture window timestamps must be finite")
        if self.start_time_s < 0 or self.end_time_s < 0:
            raise ValueError("capture window timestamps must be non-negative")
        if self.end_time_s < self.start_time_s:
            raise ValueError("capture window end must not precede its start")
        if not isinstance(self.label, TrafficClass):
            raise TypeError("label must be a TrafficClass")
        if self.label not in TRAINING_LABELS:
            raise ValueError("training capture windows cannot use the UNKNOWN label")
        if not isinstance(self.source_framework, str) or not self.source_framework.strip():
            raise ValueError("source_framework must not be empty")


@dataclass(frozen=True, order=True)
class FlowLabel:
    """One row in the frozen labels.csv schema."""

    flow_id: str
    src_ip: str
    dst_ip: str
    proto: int
    src_port: int
    dst_port: int
    label: int
    source_framework: str
    pcap_file: str

    def as_dict(self) -> dict[str, str | int]:
        """Return a dictionary ordered by the frozen label fields.

        Returns:
            Values ready for `csv.DictWriter`.
        """
        values: dict[str, str | int] = {
            "flow_id": self.flow_id,
            "src_ip": self.src_ip,
            "dst_ip": self.dst_ip,
            "proto": self.proto,
            "src_port": self.src_port,
            "dst_port": self.dst_port,
            "label": self.label,
            "source_framework": self.source_framework,
            "pcap_file": self.pcap_file,
        }
        return {field: values[field] for field in LABEL_FIELDS}


class PcapInputError(ValueError):
    """Raised when a pcap cannot be read or labeled unambiguously."""


@dataclass(frozen=True)
class CorpusVerification:
    """Orchestration consistency summary for a labeled corpus."""

    total_flows: int
    verified_flows: int
    verification_rate: float
    framework_counts: dict[str, int]


def label_pcap_flows(pcap_path: Path, windows: tuple[CaptureWindow, ...]) -> tuple[FlowLabel, ...]:
    """Join pcap flows to runner-owned intervals without inspecting traffic features.

    Args:
        pcap_path: Captured packet file to read.
        windows: Orchestration intervals carrying the ground-truth class and framework.

    Returns:
        Deterministically ordered, unique labels for forward-direction TCP/UDP flows.

    Raises:
        FileNotFoundError: If `pcap_path` does not exist.
        PcapInputError: If windows overlap ambiguously or the pcap cannot be parsed.
    """
    if not pcap_path.is_file():
        raise FileNotFoundError(pcap_path)
    _validate_non_overlapping_windows(windows)

    labels: dict[tuple[str, str, int, int, int], FlowLabel] = {}
    try:
        with PcapReader(str(pcap_path)) as packets:
            for packet in packets:
                packet_label = _label_packet(packet, pcap_path.name, windows)
                if packet_label is None:
                    continue
                key = (
                    packet_label.src_ip,
                    packet_label.dst_ip,
                    packet_label.proto,
                    packet_label.src_port,
                    packet_label.dst_port,
                )
                labels.setdefault(key, packet_label)
    except (InvalidOperation, OSError, Scapy_Exception) as exc:
        raise PcapInputError(f"could not parse pcap: {pcap_path}") from exc

    return tuple(labels[key] for key in sorted(labels))


def write_labels(labels: tuple[FlowLabel, ...], output_path: Path) -> None:
    """Write flow labels using the frozen CSV column order.

    Args:
        labels: Ground-truth rows returned by `label_pcap_flows`.
        output_path: Destination labels.csv path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=LABEL_FIELDS)
        writer.writeheader()
        writer.writerows(label.as_dict() for label in labels)


def write_capture_window(window: CaptureWindow, output_path: Path) -> None:
    """Append one runner-owned ground-truth interval to a JSONL log.

    Args:
        window: Validated orchestration interval.
        output_path: Append-only JSONL destination.
    """
    entry = {
        "source_ip": str(window.source_ip),
        "start_time_s": str(window.start_time_s),
        "end_time_s": str(window.end_time_s),
        "label": window.label.value,
        "source_framework": window.source_framework,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as output_file:
        output_file.write(json.dumps(entry, sort_keys=True) + "\n")


def read_capture_windows(log_path: Path) -> tuple[CaptureWindow, ...]:
    """Read runner intervals from an orchestration JSONL log.

    Args:
        log_path: Log created by `write_capture_window`.

    Returns:
        Windows in file order.

    Raises:
        FileNotFoundError: If the log does not exist.
        PcapInputError: If any non-empty line is malformed.
    """
    if not log_path.is_file():
        raise FileNotFoundError(log_path)
    windows: list[CaptureWindow] = []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PcapInputError(f"could not read orchestration log: {log_path}") from exc
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
            if not isinstance(entry, Mapping):
                raise ValueError("entry must be an object")
            source_ip = entry["source_ip"]
            start_time_s = entry["start_time_s"]
            end_time_s = entry["end_time_s"]
            label = entry["label"]
            source_framework = entry["source_framework"]
            if not isinstance(source_ip, str):
                raise TypeError("source_ip must be a string")
            if not isinstance(start_time_s, str) or not isinstance(end_time_s, str):
                raise TypeError("timestamps must be decimal strings")
            if type(label) is not int:
                raise TypeError("label must be an integer")
            if not isinstance(source_framework, str):
                raise TypeError("source_framework must be a string")
            window = CaptureWindow(
                source_ip=IPv4Address(source_ip),
                start_time_s=Decimal(start_time_s),
                end_time_s=Decimal(end_time_s),
                label=TrafficClass(label),
                source_framework=source_framework,
            )
        except (
            InvalidOperation,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise PcapInputError(
                f"invalid orchestration log entry on line {line_number}: {log_path}"
            ) from exc
        windows.append(window)
    return tuple(windows)


def verify_corpus(
    labels: tuple[FlowLabel, ...],
    windows: tuple[CaptureWindow, ...],
) -> CorpusVerification:
    """Check that labels remain traceable to runner-owned orchestration windows.

    Args:
        labels: Produced `labels.csv` rows.
        windows: Runner intervals used to derive those rows.

    Returns:
        Counts and the fraction of flows consistent with orchestration metadata.
    """
    verified = sum(
        any(
            label.src_ip == str(window.source_ip)
            and label.label == window.label.value
            and label.source_framework == window.source_framework
            for window in windows
        )
        for label in labels
    )
    total = len(labels)
    framework_counts = dict(sorted(Counter(label.source_framework for label in labels).items()))
    return CorpusVerification(
        total_flows=total,
        verified_flows=verified,
        verification_rate=verified / total if total else 0.0,
        framework_counts=framework_counts,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Join one pcap to orchestration windows and write frozen-schema labels.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero when at least one labeled flow is written, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", type=Path, required=True)
    parser.add_argument("--orchestration-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        windows = read_capture_windows(args.orchestration_log)
        labels = label_pcap_flows(args.pcap, windows)
        if not labels:
            raise PcapInputError("capture contains no flows owned by orchestration windows")
        write_labels(labels, args.output)
        verification = verify_corpus(labels, windows)
    except (FileNotFoundError, PcapInputError, TypeError, ValueError) as exc:
        LOGGER.error("capture labeling failed: %s", exc)
        return 1
    LOGGER.info(
        "wrote %d labels with %.1f%% orchestration verification",
        verification.total_flows,
        verification.verification_rate * 100,
    )
    return 0


def _validate_non_overlapping_windows(windows: tuple[CaptureWindow, ...]) -> None:
    ordered = sorted(windows, key=lambda window: (window.source_ip, window.start_time_s))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.source_ip != current.source_ip:
            continue
        if current.start_time_s <= previous.end_time_s:
            raise PcapInputError(
                f"overlapping orchestration windows for source {current.source_ip}"
            )


def _label_packet(
    packet: object,
    pcap_name: str,
    windows: tuple[CaptureWindow, ...],
) -> FlowLabel | None:
    if not hasattr(packet, "haslayer") or not packet.haslayer(IP):
        return None
    if packet.haslayer(TCP):
        transport = packet[TCP]
        protocol = 6
    elif packet.haslayer(UDP):
        transport = packet[UDP]
        protocol = 17
    else:
        return None

    ip_header = packet[IP]
    timestamp = Decimal(str(packet.time))
    window = next(
        (
            candidate
            for candidate in windows
            if str(candidate.source_ip) == ip_header.src
            and candidate.start_time_s <= timestamp <= candidate.end_time_s
        ),
        None,
    )
    if window is None:
        return None

    flow_id = f"{ip_header.src}:{transport.sport}-{ip_header.dst}:{transport.dport}-{protocol}"
    return FlowLabel(
        flow_id=flow_id,
        src_ip=ip_header.src,
        dst_ip=ip_header.dst,
        proto=protocol,
        src_port=int(transport.sport),
        dst_port=int(transport.dport),
        label=window.label.value,
        source_framework=window.source_framework,
        pcap_file=pcap_name,
    )


if __name__ == "__main__":
    raise SystemExit(main())
