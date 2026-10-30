# C1 — approved live_gateway contract change

Status: **team approval confirmed by the project owner on 2026-10-31**. Task C1 in `docs/demo_tasks.md`;
design §§4.2, 4.6, 5.5, 7.7, 7.15, 7.16. Approval was reported by the owner in this session.
The approved constants and topology values are reflected in `common/contracts.py` and §4.
Live deployment still requires I3 integration and Linux verification. `choke_v1` behavior and its existing contracts stay unchanged.

The original topology and congestion ABI describe a single downstream bottleneck. C1 extends
them explicitly for two-direction shaping/congestion and safe gateway flooding, retaining the
original choke_v1 ABI. DESIGN GAP: I3's required handover and aggregate shaping integration
are still absent; live activation remains blocked on that infrastructure work.

## Approved additions to design §4 and common/contracts.py

| Constant | Value / meaning |
|---|---|
| `GATEWAY_TOPOLOGY` | `live_gateway` |
| `GATEWAY_LAN_PORT` | 1 |
| `GATEWAY_WAN_PORT` | 4 |
| `GATEWAY_PORTS` | `(GATEWAY_LAN_PORT, GATEWAY_WAN_PORT)` |
| `GATEWAY_LAN_INTERFACE` | `gw-lan-sw` |
| `GATEWAY_WAN_INTERFACE` | `gw-wan-sw` |
| `GATEWAY_CPU_INTERFACE` | `gw-cpu`, device port remains frozen `CPU_PORT=255` |
| `GATEWAY_MAX_FRAME_BYTES` | 1514, Ethernet header plus IPv4 MTU 1500; no VLANs/jumbos |
| `GATEWAY_HEALTH_POLL_S` | 0.25; invoke I3 bypass after unexpected switch exit |

MACs are read from I3's owned `gw-lan` / `gw-wan` interfaces at startup and validated as distinct
unicast addresses. Never invent fixed peer MACs. Both switch interfaces and their peer links
must exist before startup. I3 provides executable `harness/gateway/bypass.sh` and its matching
netsetup/teardown scripts; gateway startup fails before modifying the switch if they are absent.

Extend the allowed topology values in §4.6 to `{choke_v1, live_gateway}`; preserve the existing
field set. A gateway consumes one experiment cell (`system` fifo/ours and `link_mbps`, plus
existing identity/seed metadata); do not silently run Mininet for `live_gateway` cells.
The topology selection must be validated explicitly in each runner that supports it.

## Bidirectional shaping and congestion (gateway ABI)

Both LAN and WAN ports receive `set_queue_depth 4*CONGESTION_HIGH_PACKETS` and
`set_queue_rate floor(link_mbps*1_000_000/(8*GATEWAY_MAX_FRAME_BYTES))`. BMv2 shapes in packets,
not bytes. For variable packet sizes this is a conservative upper bound under the MTU
assumption, not a guarantee of reaching link_mbps; report achieved iperf throughput. The rate
is per priority queue on BMv2, so an aggregate byte-rate cap additionally needs I3 Linux `tc`
shaping on **both** egress veth links. The approval includes this requirement; I3 still must implement and verify it;
per-queue BMv2 rates alone must not be described as an aggregate Mbps cap.

Build a separately named gateway pipeline artifact with a compile-time gateway mode. In that
mode only, `reg_congestion_flag` has two entries: index 0 for LAN egress, index 1 for WAN egress.
Egress chooses the index by `egress_port` and maintains existing HI/LO hysteresis separately.
Ingress chooses the corresponding index after L2 egress selection. The original artifact keeps
one entry/index 0; controller sweeps, existing PTF and `choke_v1` remain byte-for-byte compatible
at the ABI boundary. All RMW sequences remain atomic (§7.2). Neither direction may clear the
other's flag (§7.7). CPU clones never update either flag.

The existing M_AI/M_AB normal/protective presets remain unchanged. They are aggregate switch
meters shared across directions; do not claim per-direction shares without a separately
approved meter ABI change. Gateway tests must explicitly check this limitation.

## Forwarding and fail-open behavior

Unicast entries route to the peer-port MAC through `tbl_l2_forward`. Broadcast/unknown uses the
existing flood group across LAN/WAN only. Gateway egress suppresses replicas whose egress port
matches original ingress; the CPU port is never a flood member. That loop-prevention exception
is topological, not classification-based dropping. Known, UNKNOWN, malformed and collision
traffic otherwise forwards best effort; only AGENT_BULK red may be metered away (§7.15/7.16).

Start with network bypass already available; switch launch failure, policy installation failure,
unexpected exit, interrupt or normal shutdown must stop the owned switch and invoke bypass.
Measure switch-death-to-connectivity-recovery <5 s on the VM; a mocked process test is not
sufficient evidence. Do not delete or replace interfaces owned by I3. Never leave both the
bypass bridge and BMv2 forwarding active simultaneously; I3 must document the handover API.

## Review/verification checklist for the contracts PR

- [x] Project owner confirmed team approval in the implementation session (2026-10-31).
- [ ] I3 owner agrees to aggregate shaping and specifies safe bypass-to-switch handover.
- [x] Patch §4 and common/contracts.py together with the agreed exact values/layout above.
- [x] Extend `tests/test_contracts.py` and config/topology tests for both modes and legacy ABI.
- [ ] Build both original and gateway artifacts; verify independent bidirectional hysteresis,
      original-ingress flood exclusion, CPU clone exclusion and unchanged choke_v1 PTF.
- [ ] Run `make lint`, `make test`, `make build` on the Linux VM.

G2 is implemented with prerequisite checks; activation awaits compiler/PTF and I3 integration. D4 on the
existing choke_v1 topology does not require these changes and can proceed independently once
C4 and D3 evidence is available.
