"""Measured UDP sender and receiver for the built-in synthetic stress workload."""

from __future__ import annotations

import argparse
import json
import logging
import socket
import struct
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
HEADER = struct.Struct("!IIQ")
WIRE_BYTES = 14 + 20 + 8


def packet_id(seed: int, wire_id: int, sequence: int) -> str:
    """Return the stable identity carried by one measured UDP datagram."""
    return HEADER.pack(seed, wire_id, sequence).hex()


def payload_sizes(flow: dict[str, Any]) -> list[int]:
    """Validate and return the UDP payload size for each planned packet, in sequence order."""
    sizes = flow.get("payload_sizes_bytes")
    if sizes is None:
        sizes = [flow["payload_bytes"]] * len(flow["offsets_s"])
    if (
        not isinstance(sizes, list)
        or len(sizes) != len(flow["offsets_s"])
        or any(type(size) is not int or not HEADER.size <= size <= 1472 for size in sizes)
    ):
        raise ValueError("payload sizes must match the schedule and fit the measurement header/MTU")
    return sizes


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")


def run_sender(plan: dict[str, Any], log_path: Path) -> None:
    """Send scheduled datagrams and record actual sender timestamps."""
    if log_path.exists():
        raise FileExistsError(log_path)
    seed = plan["seed"]
    start = plan["start_s"]
    end = plan["end_s"]
    events = sorted(
        (
            (start + offset, flow, index + 1, size)
            for flow in plan["flows"]
            for index, (offset, size) in enumerate(
                zip(flow["offsets_s"], payload_sizes(flow), strict=True)
            )
        ),
        key=lambda item: (item[0], item[1]["wire_id"], item[2]),
    )
    sockets: dict[str, socket.socket] = {}
    rows: list[dict[str, Any]] = []
    try:
        for flow in plan["flows"]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, plan["socket_buffer_bytes"])
            sock.bind((flow["src_ip"], flow["src_port"]))
            sockets[flow["flow_id"]] = sock
        for scheduled, flow, sequence, size in events:
            if scheduled > end:
                break
            delay = scheduled - time.time()
            if delay > 0:
                time.sleep(delay)
            identity = packet_id(seed, flow["wire_id"], sequence)
            payload = bytes.fromhex(identity) + bytes(size - HEADER.size)
            sent = time.time()
            sockets[flow["flow_id"]].sendto(payload, (flow["dst_ip"], flow["dst_port"]))
            rows.append(
                {
                    "packet_id": identity,
                    "flow_id": flow["flow_id"],
                    "sequence": sequence,
                    "sent_s": sent,
                    "size_bytes": len(payload) + WIRE_BYTES,
                }
            )
    finally:
        for sock in sockets.values():
            sock.close()
    _write_rows(log_path, rows)


def run_target(plan: dict[str, Any], log_path: Path, ready_path: Path) -> None:
    """Receive actual datagrams and retain IP TOS when the platform exposes it."""
    if log_path.exists() or ready_path.exists():
        raise FileExistsError(log_path if log_path.exists() else ready_path)
    sockets: list[socket.socket] = []
    rows: list[dict[str, Any]] = []
    try:
        for port in plan["ports"]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, plan["socket_buffer_bytes"])
            if hasattr(socket, "IP_RECVTOS"):
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_RECVTOS, 1)
            sock.bind((plan["bind_ip"], port))
            sock.settimeout(plan["poll_s"])
            sockets.append(sock)
        ready_path.write_text(json.dumps({"ready_s": time.time()}), encoding="utf-8")
        while time.time() < plan["end_s"]:
            for sock in sockets:
                try:
                    payload, ancillary, _, address = sock.recvmsg(65535, 256)
                except TimeoutError:
                    continue
                received = time.time()
                if received >= plan["end_s"]:
                    # recvmsg can return after the window closed; that arrival is outside it.
                    break
                if len(payload) < HEADER.size:
                    LOGGER.warning("discarding short measurement datagram")
                    continue
                tos = next(
                    (
                        data[0]
                        for level, kind, data in ancillary
                        if level == socket.IPPROTO_IP and kind == socket.IP_TOS and data
                    ),
                    0,
                )
                rows.append(
                    {
                        "packet_id": payload[: HEADER.size].hex(),
                        "received_s": received,
                        "size_bytes": len(payload) + WIRE_BYTES,
                        "src_ip": address[0],
                        "src_port": address[1],
                        "dst_ip": sock.getsockname()[0],
                        "dst_port": sock.getsockname()[1],
                        "tos": tos,
                    }
                )
    finally:
        for sock in sockets:
            sock.close()
    _write_rows(log_path, rows)


def main(argv: Sequence[str] | None = None) -> int:
    """Run a sender or receiver from an explicit JSON actor plan."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("sender", "target"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path)
    args = parser.parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if args.role == "sender":
        run_sender(plan, args.log)
    else:
        if args.ready is None:
            parser.error("target requires --ready")
        run_target(plan, args.log, args.ready)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
