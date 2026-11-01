# Linux traffic harness

P1.5 implements the M1 skeleton only: existing `p4src/l2fwd.p4`, BMv2, `choke_v1`, one positive
iperf3 throughput measurement, and a live `policy_version` read. It does not certify classifier,
queue, DSCP, or dashboard behavior. P2.5/P3.4/P3.5 provide real bounded HTTP MCP tools, deterministic
MCP task execution, and timing-faithful human trace replay. No real framework corpus is bundled.

## M1 on Linux

Install the pinned environment in `docs/ENVIRONMENT.md`. The selected Python interpreter must see
Mininet (`python -c 'import mininet'`), and root's PATH must contain `simple_switch`,
`simple_switch_CLI`, `iperf3`, `ip`, and `tc`. These binaries are prerequisites, not installed by
the launcher. The command requires an unused Thrift port and unused `s1-eth*` interface names.

```sh
make build
sudo .venv/bin/python -m harness.runtime --pipeline build/l2fwd.json --output /tmp/aan-m1-001
```

Use a fresh output directory each time. If Mininet is supplied by Debian's Python packages,
configure the interpreter to include `/usr/lib/python3/dist-packages` (for example, create the
Python 3.11 venv with `--system-site-packages` and verify the import). Do not substitute the default
OVS switch: the launcher selects its own BMv2 `Switch` subclass and uses each `HOSTS.switch_port`
explicitly. The target link uses the existing 20 Mbps/1 ms topology defaults, configurable via
`--link-mbps`. `--duration` and `--thrift-port` are explicit CLI inputs.

The launcher serializes the fixed topology names, programs the skeleton's actual `tbl_l2_forward`
and multicast group through `controller.switch_api.SwitchApi.execute`, populates neighbor entries,
and reads the live version without modifying it. A new exclusively owned BMv2 starts with no PRE
nodes, so its first node handle is zero. The generic CLI boundary is intentional: M1's table is not
one of the frozen classifier tables. No contract names are added or changed.

`smoke.json` records pass/failure and throughput; `iperf-client.json`, `iperf-server.json`, stderr
files, and `simple-switch.log` retain diagnostics. Startup/workload failures stop the owned children
and network. There is no global `mn -c`, process-name kill, or cleanup of somebody else's network.
SIGKILL and machine crashes necessarily require operator recovery.

## MCP target and deterministic task client

The target implements stateless MCP Streamable HTTP protocol `2025-03-26` with POST `/mcp`:
`initialize`, `notifications/initialized`, `ping`, `tools/list`, and `tools/call`. Search/fetch use a
small local reference corpus; fetch accepts a document identifier, never an arbitrary URL. Compute
sums at most 128 finite numbers with absolute value at most 1,000,000. Bodies are bounded to 64 KiB,
HTTP connections time out after five seconds, and at most 32 handlers run concurrently. `/health`
provides an HTTP health check. It requires no MCP SDK or newly approved dependency.

```sh
.venv/bin/python -m harness.mcp_target.server --host 127.0.0.1 --port 8080 --log /tmp/mcp-target-001.jsonl
.venv/bin/python -m harness.agents.runner --endpoint http://127.0.0.1:8080/mcp --source-ip 127.0.0.1 --seed 7 --parallelism 2 --think-time 0 --task-script harness/agents/tasks.json --label 2 --log /tmp/mcp-runner-001.jsonl
```

In the real topology launch the target in `h_target`, run the client in `h_agent1`, and use
`http://10.0.0.100:8080/mcp` and `--source-ip 10.0.0.1`. `McpClient` binds outgoing sockets to the
claimed source address; the label cannot silently refer to some other interface. Each request
opens a real TCP connection. Scalar think time sleeps before each scheduled tool call;
`RunnerConfig` also accepts an empirical tuple of seconds, sampled with the explicit seed before
concurrent execution. OS scheduling and observed completion timings are measurements, not promises
of identical wall-clock ordering. Tasks are shuffled deterministically per seed.

The library entrypoint is `run_tasks(RunnerConfig(parallelism, think_time, task_script, seed),
endpoint, source_ip, TrafficClass, output_log) -> CaptureWindow`. JSON task scripts contain only
`tool` and `arguments` objects; `harness/agents/tasks.json` is an executable example. Task and final
run failure records are preserved; failures raise `RunnerError`. Existing logs are never overwritten.
The target records source IP, start/end epoch seconds, method, tool, and HTTP status without logging
tool arguments. The runner records each task's `completion_ms`, success, and start/end epoch seconds.

For a container target, build from the repository root:

```sh
docker build -f harness/mcp_target/Dockerfile -t aan-mcp-target .
docker run --rm -p 8080:8080 -v aan-mcp-logs:/logs aan-mcp-target
```

Use a new volume or a new log filename for each run: the log is created exclusively. The image
needs only the standard library and starts as an unprivileged user. Docker itself remains an
external prerequisite. A container started on the host bridge is not automatically in Mininet;
connect the actual container namespace explicitly before labeling network traffic.

## Labels and genuine frameworks

`source_framework` is honestly `mcp_task_client`. This client is useful for exercising the real
MCP endpoint but does not fulfill the four-framework corpus requirement. Browser Use, Playwright,
AutoGen, and Claude runners need their actual approved environments, framework dependencies, and
where applicable credentials. They have not been installed, run, or captured here.

`run_external_framework(argv, output_log, timeout_s)` executes a caller-supplied genuine runner
without a shell and captures stdout/stderr. It emits no labels or assertions about what framework
ran; the actual runner must own seed/task configuration and its orchestration log. Child exit or
timeout fails visibly. Do not use a successful process exit as evidence of a labeled corpus.

Runner `run_end.capture_window` carries `source_ip`, Decimal-compatible `start_time_s` and
`end_time_s`, integer `label`, and `source_framework`. Construct the existing `CaptureWindow` with
`IPv4Address`, `Decimal`, and `TrafficClass` conversions, then call
`label_pcap_flows(pcap_path, windows)` and `write_labels(...)` in `harness/capture.py`. Only capture.py
produces frozen `labels.csv`. Capture packets on the relevant switch/client interface during the
whole runner interval. A run failure is not successful completion evidence; its traffic still has
known orchestration provenance. Keep framework intervals disjoint per source (§7.20).

## Human trace replay

```sh
sudo .venv/bin/python -m harness.human.replay /data/consented-human.pcap --interface h_human-eth0 --log /tmp/replay-001.log --timeout 180
```

Run that command inside the `h_human` namespace, or use that Mininet host's `popen` with the same
argv. On the Linux root namespace, `h_human-eth0` intentionally does not exist. The wrapper checks
that the trace is readable, nonempty and timestamp-monotonic before invoking exactly
`tcpreplay --intf1 INTERFACE --multiplier 1.0 TRACE`. It does not rewrite trace addresses. Join labels
against the trace's actual source addresses and known replay intervals; the topology host address
alone does not label packets carrying original MAWI/CAIDA addresses. Trace licensing, consent,
anonymization, download, browsing capture, and actual packet tap collection remain external work.
The subprocess timeout kills/reaps tcpreplay and leaves the exclusive diagnostic log intact.

## Burst preset status

The frozen experiment schema names `low`, `med`, and `high`, but the design does not specify their
flows/s, parallel connections, or payload profiles. DESIGN GAP: these numerical presets require a
team decision before corpus/grid collection; this harness does not silently invent them. The task
runner accepts explicit parallelism/think time/task payloads; use these recorded inputs until the
single agreed mapping is documented here. No zero-result or fabricated framework data fills gaps.

## Host verification

```sh
.venv/bin/python -m pytest tests/test_runtime.py tests/test_target_app.py tests/test_human_replay.py tests/test_agent_runners.py
make lint
```

Two tests bind ephemeral loopback HTTP ports and exercise actual concurrent HTTP requests. They
require permission to use local sockets. Runtime tests cover process cleanup/prerequisites with
fakes; actual BMv2/Mininet smoke is a separate Linux check.

### D2 application-shaped traffic profiles

`eval/configs/workload_demo.json` extends the workload profile (`load_profile`, optional
`traffic_shapes` field) with seeded application cadences: 30-fps video frame trains with
lognormal frame sizes in 1200-byte payloads plus 20 ms audio (HUMAN_INTERACTIVE), MCP tool calls
with think gaps (AGENT_INTERACTIVE), and recurring ingest/crawler waves (AGENT_BULK). Shapes live
in `harness/demo_traffic.py`; the sender sends each packet's planned payload size. Each class's
offered frame bytes over `duration_s - drain_s` must be within 2 % of the cell's share, or the
plan is rejected. The BMv2 queue rate uses the planned mean frame size. Without `--profile` the
constant-size `eval/configs/workload_runtime.json` is used, as before.

Run a cell on the VM through the normal locked runner (the profile is archived with its hash):

```sh
sudo env PATH=$PATH HOME=$HOME PYTHONPATH=$PWD AAN_COMPILED_TREE=results/<run>/model/compiled_tree.json \
  .venv/bin/python -m eval.run_experiment eval/configs/demo_profile_smoke.yaml \
  --execute .venv/bin/python -m eval.workload --profile eval/configs/workload_demo.json
```

These are one-direction UDP timing/size approximations, not TCP/TLS sessions. DESIGN GAP: the
profile's low/med/high settings are experimental until the burst presets are agreed.
