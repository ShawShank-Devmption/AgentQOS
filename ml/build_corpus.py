"""Build the training corpus from labelled capture sessions, leaving background noise out.

Sessions stay exactly as recorded. A flow whose server is a known background service (rules file:
the name from its TLS ClientHello or the capture's own DNS answers, or a fixed port such as NTP)
gets no training rows. It is still replayed, so it counts towards slot ownership and fanout
exactly as it would on the switch.

usage: python -m ml.build_corpus results/corpus --rules eval/configs/corpus_noise.json \\
           --output results/corpus-build-NAME
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from scapy.all import DNS, TCP

from common.contracts import LABEL_FIELDS, TrafficClass
from controller.ja4 import ClientHelloError, server_name
from harness.capture import (
    SESSION_LABELS,
    SESSION_MANIFEST,
    SESSION_PCAP,
    PcapInputError,
)
from ml.extract_features import _observation, _packet_key, _pcap_packets, extract_features

LOGGER = logging.getLogger(__name__)
# A ClientHello larger than this, or spread over more segments, is left unnamed.
MAX_HELLO_BYTES = 16384
MAX_HELLO_SEGMENTS = 8

FlowTuple = tuple[str, str, int, int, int]


class CorpusError(ValueError):
    """Raised for an invalid rules file, session or output directory."""


@dataclass(frozen=True)
class NoiseRules:
    """Background services by (protocol, server port) and by DNS-name suffix."""

    ports: Mapping[tuple[int, int], str]
    domains: tuple[tuple[str, str], ...]

    def domain_reason(self, name: str) -> str | None:
        name = name.rstrip(".").lower()
        for suffix, reason in self.domains:
            if name == suffix or name.endswith("." + suffix):
                return reason
        return None


@dataclass
class _HelloBuffer:
    """The first client payload bytes of one TCP flow, until its ClientHello is complete."""

    data: bytearray = field(default_factory=bytearray)
    seqs: set[int] = field(default_factory=set)
    done: bool = False


@dataclass
class SessionNames:
    """Server names seen in one capture: TLS SNI and DNS queries per flow, DNS answers per IP."""

    sni: dict[FlowTuple, str] = field(default_factory=dict)
    dns_query: dict[FlowTuple, str] = field(default_factory=dict)
    ip_names: dict[str, set[str]] = field(default_factory=dict)


def load_rules(path: Path) -> NoiseRules:
    """Validate the background-service rules file.

    Raises:
        CorpusError: If the file is unreadable or any rule is malformed.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorpusError(f"cannot read rules: {path}") from exc
    if not isinstance(raw, dict) or set(raw) != {"ports", "domains"}:
        raise CorpusError("rules must contain exactly 'ports' and 'domains'")
    ports: dict[tuple[int, int], str] = {}
    for rule in raw["ports"]:
        if (
            not isinstance(rule, dict)
            or set(rule) != {"proto", "port", "reason"}
            or rule["proto"] not in (6, 17)
            or type(rule["port"]) is not int
            or not 1 <= rule["port"] <= 65535
            or not _text(rule["reason"])
        ):
            raise CorpusError(f"invalid port rule: {rule}")
        ports[(rule["proto"], rule["port"])] = rule["reason"]
    domains: list[tuple[str, str]] = []
    for rule in raw["domains"]:
        if (
            not isinstance(rule, dict)
            or set(rule) != {"suffix", "reason"}
            or not _text(rule["suffix"])
            or rule["suffix"] != rule["suffix"].lower().strip(".")
            or not _text(rule["reason"])
        ):
            raise CorpusError(f"invalid domain rule: {rule}")
        domains.append((rule["suffix"], rule["reason"]))
    return NoiseRules(ports, tuple(domains))


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def collect_names(pcap: Path, flows: set[FlowTuple]) -> SessionNames:
    """Name the servers of the labelled flows (oriented device -> server) in one capture."""
    names = SessionNames()
    hellos: dict[FlowTuple, _HelloBuffer] = {}
    for packet in _pcap_packets(pcap):
        key = _packet_key(packet)
        if key is None:
            continue
        flow = (key.client_ip, key.server_ip, key.proto, key.client_port, key.server_port)
        payload = _observation(packet, True).payload
        if key.proto == 17 and 53 in (key.client_port, key.server_port) and payload:
            _read_dns(names, flow, payload)
        elif key.proto == 6 and flow in flows and payload:
            buffer = hellos.setdefault(flow, _HelloBuffer())
            if not buffer.done:
                _read_hello(names, flow, buffer, payload, int(packet[TCP].seq))
    return names


def _read_dns(names: SessionNames, flow: FlowTuple, payload: bytes) -> None:
    try:
        message = DNS(payload)
        questions = [_dns_name(q.qname) for q in message.qd or []]
        if not message.qr:
            if questions:
                names.dns_query.setdefault(flow, questions[0])
            return
        answers = list(message.an or [])
    except (AttributeError, IndexError, TypeError, ValueError, UnicodeDecodeError):
        LOGGER.debug("unparsable DNS message on %s", flow, exc_info=True)
        return
    for record in answers:
        if getattr(record, "type", None) != 1:
            continue  # A records only; CNAME targets are attached through the question name.
        names.ip_names.setdefault(str(record.rdata), set()).update(
            {*questions, _dns_name(record.rrname)}
        )


def _dns_name(value: bytes | str) -> str:
    text = value.decode("ascii") if isinstance(value, bytes) else value
    return text.rstrip(".").lower()


def _read_hello(
    names: SessionNames, flow: FlowTuple, buffer: _HelloBuffer, payload: bytes, seq: int
) -> None:
    if seq in buffer.seqs:
        return  # retransmission
    buffer.seqs.add(seq)
    buffer.data += payload
    data = buffer.data
    if data[0] != 22 or len(data) > MAX_HELLO_BYTES or len(buffer.seqs) > MAX_HELLO_SEGMENTS:
        buffer.done = True  # not a TLS ClientHello we can read
        return
    if len(data) < 5 or len(data) < 5 + int.from_bytes(data[3:5], "big"):
        return  # the record continues in the next segment
    buffer.done = True
    try:
        name = server_name(bytes(data))
    except ClientHelloError:
        LOGGER.debug("unreadable ClientHello on %s", flow, exc_info=True)
        return
    if name:
        names.sni[flow] = name


def noise_reason(row: Mapping[str, str], names: SessionNames, rules: NoiseRules) -> str | None:
    """Why a labelled flow is background noise, or None when it may be agent or human traffic.

    A flow with no TLS name is noise only when every DNS name for its server IP is a background
    service: CDN addresses are shared, so one background name among others proves nothing.
    """
    flow = _flow(row)
    port_reason = rules.ports.get((flow[2], flow[4]))
    if port_reason is not None:
        return port_reason
    query = names.dns_query.get(flow)
    if query is not None:
        reason = rules.domain_reason(query)
        return None if reason is None else f"{reason} (DNS lookup of {query})"
    candidates = {names.sni[flow]} if flow in names.sni else names.ip_names.get(flow[1], set())
    reasons = {rules.domain_reason(name) for name in candidates}
    if not candidates or None in reasons:
        return None
    return f"{' / '.join(sorted(reasons))} ({', '.join(sorted(candidates))})"


def _flow(row: Mapping[str, str]) -> FlowTuple:
    return (
        row["src_ip"],
        row["dst_ip"],
        int(row["proto"]),
        int(row["src_port"]),
        int(row["dst_port"]),
    )


def _read_label_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != LABEL_FIELDS:
            raise CorpusError(f"{path}: header must exactly match LABEL_FIELDS")
        return list(reader)


def _write_rows(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def build_session(
    session: Path, rules: NoiseRules, output: Path
) -> tuple[pd.DataFrame, list[dict[str, str]], dict[str, object]]:
    """Clean one complete session's labels and extract its training rows.

    Returns:
        The session's feature rows (flow IDs prefixed with the session name), its noise rows, and
        its summary counts.
    """
    name = session.name
    rows = _read_label_rows(session / SESSION_LABELS)
    pcap = session / SESSION_PCAP
    names = collect_names(pcap, {_flow(row) for row in rows})
    kept, noise = [], []
    for row in rows:
        reason = noise_reason(row, names, rules)
        if reason is None:
            kept.append(row)
        else:
            noise.append({"session": name, "flow_id": f"{name}:{row['flow_id']}", "reason": reason})
    clean = output / "sessions" / name
    clean.mkdir(parents=True)
    _write_rows(clean / SESSION_LABELS, LABEL_FIELDS, kept)
    frame = extract_features(pcap, clean / SESSION_LABELS)
    frame["flow_id"] = name + ":" + frame["flow_id"]
    summary: dict[str, object] = {
        "labels": sorted({TrafficClass(int(row["label"])).name for row in rows}),
        "source_frameworks": sorted({row["source_framework"] for row in rows}),
        "labelled_flows": len(rows),
        "noise_flows": len(noise),
        "training_flows": len(kept),
        "flows_with_feature_rows": int(frame["flow_id"].nunique()),
        "feature_rows": len(frame),
        "collision_packets": int(frame.attrs.get("collision_packets", 0)),
    }
    LOGGER.info("%s: %d labelled, %d noise, %d kept", name, len(rows), len(noise), len(kept))
    return frame, noise, summary


def build_corpus(corpus: Path, rules_path: Path, output: Path) -> dict[str, object]:
    """Write features.csv, groups.csv, noise.csv, cleaned labels and summary.json to output.

    Raises:
        CorpusError: If output exists, the rules are invalid or a session is malformed.
    """
    rules = load_rules(rules_path)
    try:
        output.mkdir(parents=True, exist_ok=False)  # §7.19: a build never replaces another
    except FileExistsError as exc:
        raise CorpusError(f"output already exists: {output}") from exc
    frames, noise = [], []
    sessions: dict[str, object] = {}
    skipped: dict[str, str] = {}
    for session in sorted(path for path in corpus.iterdir() if (path / SESSION_MANIFEST).is_file()):
        status = json.loads((session / SESSION_MANIFEST).read_text(encoding="utf-8")).get("status")
        if status != "complete":
            skipped[session.name] = str(status)
            continue
        frame, session_noise, sessions[session.name] = build_session(session, rules, output)
        frames.append(frame)
        noise.extend(session_noise)
    if not frames:
        raise CorpusError(f"no complete sessions in {corpus}")
    features = pd.concat(frames, ignore_index=True)
    features.to_csv(output / "features.csv", index=False)
    group_rows = [
        {"flow_id": flow_id, "group_id": flow_id.split(":", 1)[0]}
        for flow_id in features["flow_id"].unique()
    ]
    _write_rows(output / "groups.csv", ["flow_id", "group_id"], group_rows)
    _write_rows(output / "noise.csv", ["session", "flow_id", "reason"], noise)
    by_reason: dict[str, int] = {}
    for row in noise:
        service = row["reason"].split(" (", 1)[0]
        by_reason[service] = by_reason.get(service, 0) + 1
    summary = {
        "corpus": str(corpus),
        "rules": str(rules_path),
        "rules_sha256": hashlib.sha256(rules_path.read_bytes()).hexdigest(),
        "sessions": sessions,
        "skipped": skipped,
        "noise_by_service": dict(sorted(by_reason.items(), key=lambda item: -item[1])),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """Build a cleaned training corpus; returns 1 without replacing anything on error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, help="directory of capture sessions")
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new directory")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        summary = build_corpus(args.corpus, args.rules, args.output)
    except (CorpusError, PcapInputError, OSError) as exc:
        LOGGER.error("corpus build failed: %s", exc)
        return 1
    LOGGER.info("built %d sessions into %s", len(summary["sessions"]), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
