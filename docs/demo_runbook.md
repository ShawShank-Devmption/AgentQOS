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
harness/gateway/vm.sh stop      # always — running costs ≈ ₹16/h, stopped ≈ ₹1,000/month
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

## 2. Demo sequence

To be written by task D7 (FIFO run, then ours, dashboard, live devices).
