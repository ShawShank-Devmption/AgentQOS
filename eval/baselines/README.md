# Baseline runtimes (P4.1, design §10)

These adapters configure real tools and yield an owned Mininet network for the caller's workload.
They do not synthesize measurements. The frozen experiment YAML schema is unchanged; deployment
settings below belong to the explicitly invoked workload, which must consume the `AAN_*` cell
metadata from `eval.run_experiment` and archive its settings with each run.

| System | Implemented mechanism | Remaining evidence/prerequisites |
|---|---|---|
| `fifo` | `fifo.running_fifo(RuntimeConfig)`: compiled minimal `l2fwd`, static forwarding/ARP, existing `choke_v1` bandwidth/delay and best-effort queues | Linux, root, Mininet/BMv2, compiled `build/l2fwd.json`; real sanity run |
| `diffserv` | `diffserv.running_diffserv(RuntimeConfig, DiffServConfig)`: `p4src/diffserv.p4` selects a class by protocol and application port in both directions, then uses the shared P4 class actions, meters and egress ECN/protection stage. Unmatched traffic → UNKNOWN | Docker p4c compilation passed; Linux BMv2/PTF marking and queue checks, then a measured sanity run, remain required. |
| `fairq` | `fairq.running_fairq(RuntimeConfig, FairQueueConfig)`: seeded tuple hashing into Linux DRR/PFIFO buckets, HTB rate and netem delay preserved on the owned bottleneck | Linux `tc` with HTB/netem/DRR/flower/matchall support; explicit planned directional flow tuples; real sanity run |
| `app_limiter` | `app_limiter.running_app_limiter(RuntimeConfig, NginxConfig)`: owned foreground target-namespace nginx, validated via `nginx -t`, plus FIFO | nginx in Linux VM; caller starts real upstream and directs HTTP requests to proxy; real sanity run |

All contexts use the existing runtime's host lock and allocate new interfaces through Mininet.
They refuse an existing evidence directory. The fairq adapter replaces a qdisc only on its newly
allocated switch-to-target interface; partial setup failure destroys that network through its owner.
It never accepts an arbitrary host interface or invokes a shell. `baseline.json` records the FIFO
base lifecycle; `fairq.json` records scheduler configuration and exact `tc` argv. The nginx config
and process/access/error logs remain under the requested evidence directory. These files record
configuration/lifecycle, not successful measurements.

## Workload integration

Run the workload through `eval.run_experiment --execute ...`, under its experiment lock. The
workload constructs explicit runtime settings from the cell environment and enters one adapter:

```python
from pathlib import Path
from eval.baselines.fairq import FairQueueConfig, FlowTuple, running_fairq
from harness.runtime import RuntimeConfig

runtime = RuntimeConfig(
    pipeline_json=Path("build/l2fwd.json"),
    output_dir=cell_output / "network",  # a new child of AAN_OUTPUT_DIR
    link_mbps=cell_link_mbps,  # AAN_LINK_MBPS
    thrift_port=thrift_port,  # explicitly chosen unused deployment port
    duration_s=cell_duration_s,
)
queues = FairQueueConfig(
    seed=cell_seed,  # AAN_SEED, never an implicit default
    buckets=bucket_count,
    limit_packets=queue_depth,
    quantum_bytes=quantum,
    flows=tuple(planned_wire_flows),
)
with running_fairq(runtime, queues) as network:
    run_and_measure_real_workload(network)
```

`planned_wire_flows` contains `FlowTuple(src_ip, dst_ip, protocol, src_port, dst_port)` objects;
no ground-truth labels enter the scheduler. Hashing uses CRC32 with the explicit seed over IPv4
source/destination bytes, IP protocol number and network-order source/destination ports. Equal
DRR quantum approximates round-robin service across hash buckets. Hash collisions share service.
Plan both directional tuples if both directions traverse the configured egress. Unplanned flows,
non-IPv4 traffic and packets without matching L4 fields use bucket 1 via a catch-all filter, so
uncertainty does not reject them. Buffer overflow remains ordinary congestion loss. This is a
Linux scheduler on the same bottleneck, **not BMv2 priority queues**. Its seed, complete tuple list,
bucket count, queue depth, quantum, rate, delay and commands are retained in `fairq.json`.

For FIFO use `running_fifo(runtime)`. For the application limiter use:

```python
from eval.baselines.app_limiter import NginxConfig, running_app_limiter

limiter = NginxConfig(
    listen_ip=target_ip,
    listen_port=proxy_port,
    upstream_ip="127.0.0.1",
    upstream_port=application_port,
    requests_per_second=request_rate,
    burst=request_burst,
    prefix=runtime.output_dir / "nginx",
)
with running_app_limiter(runtime, limiter) as network:
    # Start the real target in h_target on application_port; HTTP clients use proxy_port.
    run_and_measure_real_workload(network)
```

nginx delays excess requests within the configured per-source burst. Beyond that burst it returns
HTTP 429, the explicit application-rate-limit baseline behavior; no traffic-class inference enters
that decision. Non-HTTP traffic continues over FIFO. HTTP/MCP request completions and 429 responses
must both be captured by the workload. The adapter terminates/reaps only its foreground child; it
does not use global nginx stop/reload commands or modify system configuration.

## DiffServ baseline

`diffserv.PortRule("tcp", application_port, TrafficClass.AGENT_INTERACTIVE)` and
`DiffServConfig((rule, ...))` define explicit static application-port classification. Treatments
come exclusively from `common.contracts.CLASS_TREATMENTS`. Build `p4src/diffserv.p4` to
`build/diffserv.json`, then pass that artifact to `RuntimeConfig` and enter
`running_diffserv(runtime, config)`. The adapter checks for its baseline-only port table, shared
class table, class meters and congestion register before allocating Mininet. It installs class
actions and meter presets through `SwitchApi`, and records exact rules and commands in
`diffserv.json`. The baseline-only `tbl_diffserv_port` is a **DESIGN GAP** requiring team review;
the frozen system table names are unchanged.

The static port rule sees one shared destination service port for all three workload classes. It
therefore gives all of those flows the same treatment; it has no orchestration labels or behavior
features. Its P4 compilation is checked, while PTF packet treatment and measured Linux evidence
remain pending.

Host tests verify validation, exact argv/config boundaries and cleanup. The optional real
`nginx -t` test skips when nginx is absent. Four completed live 30-second sanity runs and P4 queue
checks remain required before P4.1 can be reported complete.
