"""Bounded CPU-clone intake and fail-open packet-to-flow refinement (P3.11)."""

from __future__ import annotations

import logging
import socket
import sys
import threading
from collections.abc import Collection
from dataclasses import dataclass
from queue import Empty, Full, Queue
from types import TracebackType

from common.contracts import CPU_HEADER_FIELDS, PUNT_REASON_FIRST_PACKET, TrafficClass
from common.feature_math import flow_key_bytes, flow_slot, flow_tag
from controller.ja4 import ClientHelloError, ja4

logger = logging.getLogger(__name__)
_CPU_HEADER_BYTES = sum(bits for _, bits in CPU_HEADER_FIELDS) // 8


class PuntParseError(ValueError):
    """Raised when a CPU clone cannot safely identify a canonical IPv4 flow."""


@dataclass(frozen=True)
class CpuHeader:
    """Decoded frozen CPU clone metadata."""

    flow_hash: int
    traffic_class: TrafficClass
    reason: int
    ingress_port: int


@dataclass(frozen=True)
class FlowIdentity:
    """Canonical IPv4 flow, always client endpoint first (design §4.2/§7.1)."""

    client_ip: int
    server_ip: int
    proto: int
    client_port: int
    server_port: int

    @property
    def key(self) -> bytes:
        """Return the exact 104-bit key consumed by common flow hash functions."""
        return flow_key_bytes(
            self.client_ip, self.server_ip, self.proto, self.client_port, self.server_port
        )

    @property
    def tag(self) -> int:
        """Return the switch's salted ownership tag."""
        return flow_tag(self.key)


@dataclass(frozen=True)
class ParsedPunt:
    """Validated clone metadata and segment-local transport payload."""

    header: CpuHeader
    flow: FlowIdentity
    payload: bytes


@dataclass(frozen=True)
class FlowObservation:
    """Punt-learned identity; evidence is provisional until switch ownership is checked."""

    slot: int
    flow: FlowIdentity
    traffic_class: TrafficClass
    evidence_class: TrafficClass | None = None
    fingerprint: str | None = None


class BoundedPuntQueue:
    """Nonblocking FIFO: excess clones lose refinement, never affect forwarding."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("punt queue capacity must be positive")
        self._queue: Queue[bytes] = Queue(capacity)
        self.dropped = 0

    def offer(self, frame: bytes) -> bool:
        """Enqueue a clone without waiting; return False when the queue is full."""
        try:
            self._queue.put_nowait(frame)
        except Full:
            # §7.6: overload discards only CPU clones; the original packet remains in P4.
            self.dropped += 1
            return False
        return True

    def pop(self) -> bytes | None:
        """Return the oldest clone, or None immediately when empty."""
        try:
            return self._queue.get_nowait()
        except Empty:
            return None


class PuntReceiver:
    """Own a Linux CPU-interface socket and feed only clones into the bounded queue."""

    def __init__(self, interface: str, queue: BoundedPuntQueue) -> None:
        if (
            not interface
            or len(interface) > 15
            or any(ch.isspace() or ch == "\x00" for ch in interface)
        ):
            raise ValueError("invalid CPU interface name")
        self._interface = interface
        self._queue = queue
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self.failed = threading.Event()

    def __enter__(self) -> PuntReceiver:
        """Bind the explicitly supplied Linux interface and start clone intake."""
        if not sys.platform.startswith("linux"):
            raise RuntimeError("CPU-clone reception requires Linux AF_PACKET")
        transport = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
        try:
            transport.bind((self._interface, 0))
            transport.settimeout(0.1)
            self._socket = transport
            self._thread = threading.Thread(target=self._receive, name="cpu-clones", daemon=True)
            self._thread.start()
        except (OSError, RuntimeError):
            transport.close()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop intake and close only this receiver's socket."""
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise RuntimeError("CPU receiver did not stop")

    def _receive(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                frame, address = self._socket.recvfrom(65535 + 14 + _CPU_HEADER_BYTES)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                if self._stop.is_set():
                    logger.debug("CPU socket closed during shutdown", exc_info=True)
                else:
                    logger.exception("CPU clone reception failed; fast-path forwarding continues")
                    self.failed.set()
                return
            # §7.6: local outgoing frames are not switch-originated CPU clones.
            if len(address) >= 3 and address[2] == getattr(socket, "PACKET_OUTGOING", 4):
                continue
            self._queue.offer(frame)


class PuntHandler:
    """Parse clones and attach only explicitly configured JA4 evidence."""

    def __init__(
        self,
        upstream_ports: Collection[int],
        downstream_ports: Collection[int],
        agent_fingerprints: frozenset[str] = frozenset(),
    ) -> None:
        _validate_ports(upstream_ports, downstream_ports)
        self._upstream = frozenset(upstream_ports)
        self._downstream = frozenset(downstream_ports)
        self._agent_fingerprints = agent_fingerprints

    def handle(self, frame: bytes) -> FlowObservation | None:
        """Return a provisional flow observation; malformed clones fail open with a log."""
        try:
            parsed = parse_punt(frame, self._upstream, self._downstream)
        except PuntParseError as exc:
            logger.debug("Ignoring malformed or unsupported CPU clone: %s", exc)
            return None
        fingerprint = None
        evidence = None
        if (
            parsed.header.reason == PUNT_REASON_FIRST_PACKET
            and parsed.flow.proto == 6
            and parsed.payload.startswith(b"\x16\x03")
        ):
            try:
                fingerprint = ja4(parsed.payload)
            except ClientHelloError as exc:
                # §7.15: a split/malformed ClientHello cannot turn uncertainty into agent evidence.
                logger.debug("ClientHello refinement unavailable: %s", exc)
            if fingerprint in self._agent_fingerprints:
                evidence = TrafficClass.AGENT_INTERACTIVE
        return FlowObservation(
            parsed.header.flow_hash,
            parsed.flow,
            parsed.header.traffic_class,
            evidence,
            fingerprint,
        )


def parse_punt(
    frame: bytes, upstream_ports: Collection[int], downstream_ports: Collection[int]
) -> ParsedPunt:
    """Parse a CPU header plus Ethernet/IPv4/TCP-or-UDP using topology port direction.

    Raises:
        PuntParseError: For malformed, unsupported, fragmented or inconsistent frames.
        ValueError: If the topology's port direction sets overlap or are empty.
    """
    _validate_ports(upstream_ports, downstream_ports)
    if len(frame) < _CPU_HEADER_BYTES + 14:
        raise PuntParseError("truncated CPU/Ethernet header")
    values: dict[str, int] = {}
    offset = 0
    for name, bits in CPU_HEADER_FIELDS:
        size = bits // 8
        values[name] = int.from_bytes(frame[offset : offset + size], "big")
        offset += size
    try:
        label = TrafficClass(values["class"])
    except ValueError as exc:
        raise PuntParseError("invalid CPU class") from exc
    ingress_port = values["ingress_port"]
    if ingress_port not in upstream_ports and ingress_port not in downstream_ports:
        raise PuntParseError("ingress port has no known direction")
    header = CpuHeader(values["flow_hash"], label, values["reason"], ingress_port)
    if frame[offset + 12 : offset + 14] != b"\x08\x00":
        raise PuntParseError("not Ethernet IPv4")
    ip = frame[offset + 14 :]
    if len(ip) < 20 or ip[0] >> 4 != 4:
        raise PuntParseError("invalid IPv4 header")
    ip_size = (ip[0] & 0xF) * 4
    total_size = int.from_bytes(ip[2:4], "big")
    if ip_size < 20 or total_size < ip_size or total_size > len(ip):
        raise PuntParseError("invalid IPv4 lengths")
    # §7.15: no IP/TCP reassembly; fragmented clones retain the data-plane fail-open decision.
    if int.from_bytes(ip[6:8], "big") & 0x3FFF:
        raise PuntParseError("fragmented IPv4 is unsupported")
    transport = ip[ip_size:total_size]
    proto = ip[9]
    payload = _transport_payload(transport, proto)
    source = int.from_bytes(ip[12:16], "big")
    destination = int.from_bytes(ip[16:20], "big")
    source_port = int.from_bytes(transport[:2], "big")
    destination_port = int.from_bytes(transport[2:4], "big")
    if ingress_port in downstream_ports:
        source, destination = destination, source
        source_port, destination_port = destination_port, source_port
    flow = FlowIdentity(source, destination, proto, source_port, destination_port)
    # §7.1/7.11: a CPU slot is never trusted merely because the clone names it.
    if flow_slot(flow.key) != header.flow_hash:
        raise PuntParseError("CPU slot disagrees with canonical flow hash")
    return ParsedPunt(header, flow, payload)


def _transport_payload(transport: bytes, proto: int) -> bytes:
    if proto == 6:
        if len(transport) < 20:
            raise PuntParseError("truncated TCP header")
        header_size = (transport[12] >> 4) * 4
        if header_size < 20 or header_size > len(transport):
            raise PuntParseError("invalid TCP header length")
        return transport[header_size:]
    if proto == 17:
        if len(transport) < 8:
            raise PuntParseError("truncated UDP header")
        size = int.from_bytes(transport[4:6], "big")
        if size < 8 or size > len(transport):
            raise PuntParseError("invalid UDP length")
        return transport[8:size]
    raise PuntParseError("not TCP or UDP")


def _validate_ports(upstream: Collection[int], downstream: Collection[int]) -> None:
    if not upstream or not downstream or set(upstream) & set(downstream):
        raise ValueError("upstream and downstream ports must be nonempty and disjoint")
    port_bits = dict(CPU_HEADER_FIELDS)["ingress_port"]
    if any(
        not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 1 << port_bits
        for port in (*upstream, *downstream)
    ):
        raise ValueError("topology ports must be positive CPU-header-width integers")
