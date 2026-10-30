"""Ground-truth flow labeling from orchestration windows and captured packets.

Also runs gateway capture sessions: one tcpdump per labelled device activity, whose orchestration
window becomes the ground truth (design.md §4.4, §7.20).
"""

import argparse
import csv
import json
import logging
import os
import pwd
import re
import signal
import struct
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

from scapy.all import IP, TCP, UDP, PcapReader
from scapy.error import Scapy_Exception

from common.contracts import LABEL_FIELDS, TRAINING_LABELS, TrafficClass

LOGGER = logging.getLogger(__name__)
SESSION_PCAP = "capture.pcap"
SESSION_LOG = "tcpdump.log"
SESSION_MANIFEST = "session.json"
SESSION_LABELS = "labels.csv"
TCPDUMP_START_TIMEOUT_S = 10.0
TCPDUMP_STOP_TIMEOUT_S = 15.0
DROP_PATTERN = re.compile(r"(\d+) packets dropped by (?:kernel|interface)")


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


def validate_capture(path: Path) -> None:
    """Validate classic Ethernet pcap framing before any consumer trusts packet records.

    Rejects truncated records, capture truncation, unsupported link types and malformed timestamp
    fractions. Convert pcapng to classic pcap before ingestion so all stages share this check.
    """
    # Scapy can silently accept a truncated final record; check framing before consuming data.
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
    }
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as handle:
        header = handle.read(24)
        if len(header) != 24 or header[:4] not in formats:
            raise PcapInputError("expected classic Ethernet pcap; convert pcapng before extraction")
        endian, resolution = formats[header[:4]]
        major, minor, _, _, snaplen, linktype = struct.unpack(f"{endian}HHiIII", header[4:])
        if (major, minor) != (2, 4) or linktype != 1 or not snaplen:
            raise PcapInputError("unsupported pcap version, snapshot length or Ethernet link type")
        total = path.stat().st_size
        while record := handle.read(16):
            if len(record) != 16:
                raise PcapInputError("truncated pcap record header")
            _, fraction, captured, original = struct.unpack(f"{endian}IIII", record)
            if captured > total - handle.tell():
                raise PcapInputError("truncated pcap packet record")
            if captured > snaplen or captured != original or fraction >= resolution:
                raise PcapInputError("truncated capture or invalid pcap packet lengths/timestamp")
            handle.seek(captured, 1)


class CaptureSessionError(RuntimeError):
    """Raised when a capture session cannot produce complete, trustworthy evidence."""


def tcpdump_command(
    interface: str, device_ip: IPv4Address, pcap: Path, user: str | None = None
) -> list[str]:
    """Build a full-packet, per-packet-flushed capture of one device's traffic.

    Args:
        interface: Interface every packet of the device crosses (the switch LAN port).
        device_ip: Tunnel address of the device; the only traffic captured.
        pcap: Classic pcap destination.
        user: Account tcpdump drops to after opening the interface; None keeps the caller.

    Returns:
        tcpdump argv.
    """
    command = ["tcpdump", "-i", interface, "-s", "0", "-U"]
    if user is not None:
        command += ["-Z", user]
    return [*command, "-w", str(pcap), "host", str(device_ip)]


def start_session(
    session: Path,
    interface: str,
    device_ip: IPv4Address,
    label: TrafficClass,
    source_framework: str,
) -> int:
    """Start capturing one labelled device activity into a new session directory.

    Start the activity only after this returns: the orchestration window opens once tcpdump is
    listening, so packets sent earlier belong to no window.

    Args:
        session: New directory; an existing one is never reused (§7.19).
        interface: Interface every packet of the device crosses.
        device_ip: Tunnel address of the device.
        label: Ground-truth class of everything the device sends during the session.
        source_framework: Activity/framework name recorded in labels.csv.

    Returns:
        The tcpdump process ID.

    Raises:
        FileExistsError: If the session directory exists.
        ValueError: If the label or framework is invalid.
        CaptureSessionError: If tcpdump does not start listening.
    """
    # Same validation as the final window, before anything is created.
    CaptureWindow(device_ip, Decimal(0), Decimal(0), label, source_framework)
    session.parent.mkdir(parents=True, exist_ok=True)
    session.mkdir()
    owner = _sudo_owner()
    if owner is not None:
        os.chown(session, owner.pw_uid, owner.pw_gid)
    manifest: dict[str, Any] = {
        "status": "starting",
        "interface": interface,
        "device_ip": str(device_ip),
        "label": label.name,
        "source_framework": source_framework,
        "pcap_file": SESSION_PCAP,
    }
    _write_manifest(session, manifest)
    command = tcpdump_command(
        interface, device_ip, session / SESSION_PCAP, owner.pw_name if owner else None
    )
    with (session / SESSION_LOG).open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=log, start_new_session=True
        )
    try:
        _wait_until_listening(session, process)
    except CaptureSessionError as exc:
        _fail(session, manifest, str(exc))
        raise
    manifest.update(status="capturing", tcpdump_pid=process.pid, start_time_s=repr(time.time()))
    _write_manifest(session, manifest)
    LOGGER.info("capturing %s (%s) on %s, pid %d", device_ip, label.name, interface, process.pid)
    return process.pid


def stop_session(session: Path) -> tuple["FlowLabel", ...]:
    """Close the orchestration window, stop tcpdump and write the session's labels.csv.

    Args:
        session: Directory created by `start_session`.

    Returns:
        Labels of device-originated flows that started inside the window.

    Raises:
        CaptureSessionError: If the session is not capturing, tcpdump died early, or packets
            were dropped; no labels are written then.
    """
    manifest = json.loads((session / SESSION_MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("status") != "capturing":
        raise CaptureSessionError(f"session {session} is not capturing")
    manifest["end_time_s"] = repr(time.time())
    pcap = session / SESSION_PCAP
    try:
        manifest["dropped_by_kernel"] = _stop_tcpdump(session, manifest["tcpdump_pid"], pcap)
        if manifest["dropped_by_kernel"]:
            raise CaptureSessionError(
                f"tcpdump dropped {manifest['dropped_by_kernel']} packets; capture incomplete"
            )
        validate_capture(pcap)
        window = CaptureWindow(
            IPv4Address(manifest["device_ip"]),
            Decimal(manifest["start_time_s"]),
            Decimal(manifest["end_time_s"]),
            TrafficClass[manifest["label"]],
            manifest["source_framework"],
        )
        midstream = _midstream_tcp_flows(pcap, window.source_ip)
        labels = tuple(
            label
            for label in label_pcap_flows(pcap, (window,))
            if (label.src_ip, label.dst_ip, label.proto, label.src_port, label.dst_port)
            not in midstream
        )
    except (CaptureSessionError, PcapInputError, OSError) as exc:
        _fail(session, manifest, str(exc))
        raise CaptureSessionError(str(exc)) from exc
    write_labels(labels, session / SESSION_LABELS)
    manifest.update(
        status="complete",
        labelled_flows=len(labels),
        excluded_midstream_tcp_flows=len(midstream),
    )
    _write_manifest(session, manifest)
    LOGGER.info("session %s: %d labelled flows", session, len(labels))
    return labels


def _sudo_owner() -> pwd.struct_passwd | None:
    """Account that invoked sudo, so tcpdump can drop to it and write the session directory."""
    if os.geteuid() != 0 or "SUDO_UID" not in os.environ:
        return None
    return pwd.getpwuid(int(os.environ["SUDO_UID"]))


def _write_manifest(session: Path, manifest: dict[str, Any]) -> None:
    temporary = session / f".{SESSION_MANIFEST}.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(session / SESSION_MANIFEST)


def _fail(session: Path, manifest: dict[str, Any], error: str) -> None:
    LOGGER.error("capture session %s failed: %s", session, error)
    manifest.update(status="failed", error=error)
    _write_manifest(session, manifest)


def _wait_until_listening(session: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + TCPDUMP_START_TIMEOUT_S
    log = session / SESSION_LOG
    while "listening on" not in log.read_text(encoding="utf-8", errors="replace"):
        if process.poll() is not None:
            raise CaptureSessionError(f"tcpdump exited: {log.read_text().strip()}")
        if time.monotonic() > deadline:
            process.terminate()
            raise CaptureSessionError("tcpdump did not start listening")
        time.sleep(0.05)


def _stop_tcpdump(session: Path, pid: int, pcap: Path) -> int:
    """Terminate this session's tcpdump, wait for it to exit and return its drop count."""
    if not _is_capture_process(pid, pcap):
        raise CaptureSessionError(f"tcpdump (pid {pid}) is no longer running; capture incomplete")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + TCPDUMP_STOP_TIMEOUT_S
    while not _exited(pid):
        if time.monotonic() > deadline:
            raise CaptureSessionError(f"tcpdump (pid {pid}) did not exit")
        time.sleep(0.05)
    drops = DROP_PATTERN.findall((session / SESSION_LOG).read_text(encoding="utf-8"))
    if not drops:
        raise CaptureSessionError("tcpdump exited without reporting packet statistics")
    return sum(int(count) for count in drops)


def _is_capture_process(pid: int, pcap: Path) -> bool:
    """True if `pid` is alive and, where /proc exists, is the tcpdump writing `pcap`."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    cmdline = Path(f"/proc/{pid}/cmdline")
    if not Path("/proc").is_dir():
        return True
    try:
        return str(pcap).encode() in cmdline.read_bytes().split(b"\0")
    except FileNotFoundError:
        return False


def _exited(pid: int) -> bool:
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        return reaped != 0
    except ChildProcessError:
        # Started by an earlier CLI invocation: not our child, so poll for its disappearance.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False


def _midstream_tcp_flows(pcap: Path, device: IPv4Address) -> set[tuple[str, str, int, int, int]]:
    """Device-side TCP flows whose first captured packet is not the device's SYN.

    Such flows started before the capture; their packet counts, first sizes and age would not
    match what the switch computes for a new flow, so they are excluded from the corpus.
    """
    first_is_syn: dict[tuple[str, str, int, int, int], bool] = {}
    device_ip = str(device)
    try:
        with PcapReader(str(pcap)) as packets:
            for packet in packets:
                if not packet.haslayer(IP) or not packet.haslayer(TCP):
                    continue
                ip, tcp = packet[IP], packet[TCP]
                outbound = ip.src == device_ip
                key = (
                    (ip.src, ip.dst, 6, int(tcp.sport), int(tcp.dport))
                    if outbound
                    else (ip.dst, ip.src, 6, int(tcp.dport), int(tcp.sport))
                )
                if key not in first_is_syn:
                    flags = int(tcp.flags)
                    first_is_syn[key] = outbound and bool(flags & 0x02) and not flags & 0x10
    except (OSError, Scapy_Exception) as exc:
        raise PcapInputError(f"could not parse pcap: {pcap}") from exc
    return {key for key, syn in first_is_syn.items() if not syn}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: `start SESSION --interface IF --device-ip IP --label NAME --framework NAME` | `stop`."""
    parser = argparse.ArgumentParser(description="Labelled gateway capture sessions.")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("session", type=Path)
    start.add_argument("--interface", required=True)
    start.add_argument("--device-ip", type=IPv4Address, required=True)
    start.add_argument("--label", choices=[label.name for label in TRAINING_LABELS], required=True)
    start.add_argument("--framework", required=True)
    stop = commands.add_parser("stop")
    stop.add_argument("session", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "start":
            start_session(
                args.session,
                args.interface,
                args.device_ip,
                TrafficClass[args.label],
                args.framework,
            )
        else:
            stop_session(args.session)
    except (CaptureSessionError, FileExistsError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
