# Demo runbook

Part 1 covers the VM and the live gateway (task I4, owner Shashank). Part 2, the demo sequence
itself, is added by task D7 (Dev C).

## 1. Operations: VM and live gateway

### Prerequisites

- `gcloud auth login` with an account that has `roles/iap.tunnelResourceAccessor` and
  `roles/compute.instanceAdmin.v1` on project `vivid-rush-513007-k5`.
- Run everything from the repository root on the Mac. SSH goes through IAP (port 22 is closed to
  the internet), so the commands work from any network, including the phone hotspot.

### Daily cycle

```sh
harness/gateway/vm.sh start     # boots in ~30 s; the gateway comes up by itself in bypass mode
harness/gateway/vm.sh sync      # ships the committed HEAD; uncommitted edits are NOT synced
harness/gateway/vm.sh gate      # lint, tests, py3.9 tests, P4 build on the VM
...                             # work
harness/gateway/vm.sh stop      # Shashank only — running costs ≈ ₹16/h, stopped ≈ ₹1,000/month
```

| Command | What it does |
|---|---|
| `status` | name, state, machine type, IP |
| `ssh [--command '…']` | shell on the VM, or run one command |
| `install-gateway` | re-install the gateway scripts after `sync` changed `harness/gateway/` |
| `selftest` | throwaway WireGuard client on the VM → tunnel → switch LAN port → internet; PASS/FAIL |
| `add-device NAME` | new profile; `~/.aan-gateway/NAME.png` (QR, phones) and `NAME.conf` (laptops) |
| `revoke-device NAME` | remove a device's access and its profile |
| `tunnel` | dashboard on the VM's `localhost:8080` → `http://localhost:8080` on the Mac |

Budget alert: "AgentQOS VM", ₹3,000/month, e-mails at 50/90/100 % actual and 100 % forecast. It
only alerts; it never stops the VM.

### How the gateway works

```
device ═ WireGuard UDP 51820 ═► wg0 10.42.0.1 [netns aan-lan] → gw-lan ═ gw-lan-sw
   gw-lan-sw ⇄ (bypass bridge, or BMv2) ⇄ gw-wan-sw ═ gw-wan [root] → NAT → internet
```

Every decrypted device packet crosses `gw-lan-sw`/`gw-wan-sw`. In **bypass** mode a Linux bridge
joins them (plain forwarding, no classification). The switch launcher (G2) turns bypass off and
attaches BMv2 to the same two interfaces. Exactly one of the two may own them; with bypass off and
no switch running, devices have no connectivity.

```sh
harness/gateway/vm.sh ssh --command 'sudo /usr/local/lib/aan-gateway/bypass.sh status'
harness/gateway/vm.sh ssh --command 'sudo ip netns exec aan-lan wg show wg0'   # handshakes, bytes
```

All names and addresses live in `harness/gateway/gateway.env`.

### Onboarding a device

1. `harness/gateway/vm.sh add-device NAME` (lowercase letters, digits, dashes). One profile per
   device: two devices sharing a profile keep stealing each other's endpoint.
2. Phone: install the WireGuard app, tap **+ → Scan from QR code**, scan `~/.aan-gateway/NAME.png`.
   Laptop: import `~/.aan-gateway/NAME.conf` in the WireGuard app.
3. Turn the tunnel on and open `https://1.1.1.1/cdn-cgi/trace`: `ip=34.131.42.32` means all of the
   device's traffic goes through the gateway.
4. Delete the files under `~/.aan-gateway/` once imported; they contain the device's private key.

The profile tunnels IPv4 and IPv6 (`::/0`) so IPv6 cannot bypass the switch; the gateway is
IPv4-only, so apps fall back to IPv4. DNS (`1.1.1.1`, `8.8.8.8`) also goes through the tunnel.

Existing profiles: `shashank-phone` (10.42.0.10), `team-laptop` (10.42.0.11).

### Troubleshooting

| Symptom | Check |
|---|---|
| Tunnel on, no internet | `bypass.sh status`: `off` with no switch running means no path; `bypass.sh on` |
| No handshake in `wg show` | VM running? `status`; firewall rule `aan-allow-wireguard`; device clock |
| Slow pages, some sites hang | MTU: profiles use 1280; check the device did not override it |
| Gateway missing after a change | `vm.sh install-gateway`, then `vm.sh selftest` |

### Evidence after each session (task I6)

Run results stay append-only on the VM; copy them to the Mac before stopping (never into git):

```sh
D=results/vm-evidence/$(date +%F) && mkdir -p "$D"
harness/gateway/vm.sh ssh --command 'cd ~/cn_project/results && tar -czf - linux-live linux-ptf' \
  >"$D/evidence.tar.gz" && shasum -a 256 "$D/evidence.tar.gz" >"$D/SHA256SUMS"
```

### Granting a teammate VM access (project owner)

```sh
P=vivid-rush-513007-k5
U=teammate@example.com
gcloud projects add-iam-policy-binding $P --member=user:$U \
  --role=roles/iap.tunnelResourceAccessor --condition=None
gcloud projects add-iam-policy-binding $P --member=user:$U --role=roles/compute.viewer --condition=None
gcloud compute instances add-iam-policy-binding agent-qos-p4dev --zone=asia-south2-a --project=$P \
  --member=user:$U --role=roles/compute.instanceAdmin.v1
```

`instanceAdmin.v1` is granted on this one VM only (start/stop/SSH), not the whole project. The
project sits in an organization; a domain-restriction policy may reject outside accounts, in which
case grant the teammate's organization account instead.

### Recording sessions as a teammate (one-time setup)

Step-by-step guide for teammates, with the clean-session checklist and targets:
`docs/recording_guide.md`.

The two recorders below run on **macOS** only (Terminal.app, `osascript`). Everyone logs in to the
VM as the one account `shashankbhat` (`AAN_VM_USER` in `harness/gateway/vm_lib.sh`), which owns
`~/cn_project`, the Python environments and `~/aan-results`; every recording lands in
`~/aan-results/corpus/SESSION/` there, whoever made it.

1. The project owner grants your Google account access (section above). If your first
   `vm.sh ssh` fails with *does not have access to service account*, the owner also grants you
   `roles/iam.serviceAccountUser` on the VM's service account (adding your SSH key edits the VM's
   metadata).
2. `gcloud auth login`; clone the repository and check out `origin/shashank`; create the venv
   (`python3.11 -m venv .venv`; the crawler is stdlib-only); install Claude Code and log in.
3. `harness/gateway/vm.sh ssh --command true` once: creates your SSH key and registers it.
4. Your own WireGuard profile, never a shared one: `harness/gateway/vm.sh add-device NAME-mac`,
   import `~/.aan-gateway/NAME-mac.conf` in the WireGuard app, then delete the files.

Each session: WireGuard on, then `record_interactive.sh` or `record_bulk.sh` as below. The
recorder finds which profile owns this Mac's tunnel address and prints
`record: capturing device 'NAME-mac' (10.42.0.x)` — that device, and only it, is labelled.
Session names are shared by the whole team: prefix them with your name (`ananya-agent-01`).

Sharing the VM: only Shashank starts, stops and syncs it; teammates never run `vm.sh stop`,
`sync`, `gate` or `install-gateway` (each one breaks a recording in progress). `vm.sh sync`
replaces the VM's code with the local committed HEAD, so Shashank syncs only from an
up-to-date `origin/shashank`.

### Recording a Claude Code session (AGENT_INTERACTIVE), one command

From **Terminal.app** on the Mac, with this Mac's WireGuard profile on and the VM running:

```sh
cd ~/Projects/cn_project          # your clone
harness/gateway/record_interactive.sh agent-03
```

It checks the VM and that this Mac's traffic leaves via the gateway, quits every other app
(Terminal, Finder and WireGuard stay; apps with unsaved work ask first), opens the capture over one
SSH connection made before recording starts, and launches Claude Code in
`~/aan-agent-sessions/SESSION/` with a task from `harness/agents/prompts/interactive.txt` (the same
session name always gets the same task). Drive Claude as long as you like; **exiting Claude
(`/exit` or Ctrl-D) ends the recording** and prints the result. Ctrl-C to interrupt Claude is safe.
If the connection drops, the VM ends the window at that moment; it ends by itself after 60 minutes.
The first run asks to let Terminal control System Events (needed to quit apps): allow it.
Codex CLI instead of Claude Code: `record_interactive.sh --agent codex SESSION` (and the same
for `record_bulk.sh`); setup in `docs/recording_guide.md`.
Background services are not apps: pause macOS update downloads and iCloud sync beforehand.

### Recording a crawler session (AGENT_BULK), one command

Same preconditions as the interactive recorder (Terminal.app, WireGuard on, VM running):

```sh
harness/gateway/record_bulk.sh bulk-01
```

Same checks, app quitting and capture window; then Claude Code runs **headless** (`claude -p`)
and launches one burst: 6–12 small crawlers started at the same moment, each from a random start
page (`--random-seed`, chosen by Claude and logged) on a different public site, 15–25 pages each.
The whole session takes about a minute. Claude may only run `harness/agents/crawler.py` (and
read/write its session folder). The crawler, not Claude, enforces politeness: only the sites in
`SEED_URLS` (Wikipedia in five languages, Wiktionary, Wikibooks, docs.python.org,
rfc-editor.org); robots.txt and Crawl-delay obeyed; at most 1 request/s per site across all crawler
processes; identifying User-Agent; at most 50 pages per crawler and 3 minutes; page bodies not
stored.

### Labelled corpus sessions (real traffic, ground truth from orchestration)

One session = one device doing one kind of activity. Everything the device sends during the
session gets the session's label (design.md §4.4, §7.20), so the device must do nothing else:
close other apps and background sync before starting.

```sh
harness/gateway/vm.sh capture-start call-01 shashank-phone HUMAN_INTERACTIVE video-call
#   ... only now start the activity (e.g. a 3-minute video call), then end it ...
harness/gateway/vm.sh capture-stop call-01        # prints session.json; status must be "complete"
```

- Session names are new each time (`call-02`, …); existing directories are never reused.
- Labels: `HUMAN_INTERACTIVE`, `AGENT_INTERACTIVE`, `AGENT_BULK`. The last argument names the
  activity/framework and becomes `source_framework` in `labels.csv`.
- Output on the VM: `results/corpus/SESSION/{capture.pcap, labels.csv, session.json, tcpdump.log}`.
  Full packets of your own devices only; never run a session with someone else's device.
- TCP flows already open when capture started are excluded (their first packets are missing);
  the count is `excluded_midstream_tcp_flows` in `session.json`. UDP/QUIC flows cannot be told
  apart this way, so start the activity after `capture-start` returns and avoid long-lived apps.
- A session with kernel packet drops is marked `failed` and gets no labels; repeat it.
- Features for training: on the VM,
  `.venv/bin/python -m ml.extract_features results/corpus/S/capture.pcap results/corpus/S/labels.csv --output results/corpus/S/features.csv`.

## 2. Demo sequence

To be written by task D7 (FIFO run, then ours, dashboard, live devices).
