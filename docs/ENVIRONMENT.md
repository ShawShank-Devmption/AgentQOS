# Development Environment (docs/ENVIRONMENT.md)

> Task P0.4. All three developers must run the SAME environment described here.
> **Hard constraint: Mininet and BMv2 require a Linux kernel — they do not run on macOS or
> Windows.** All P4/Mininet/controller work happens inside the Linux VM below. Editing,
> git, and LLM tools can stay on the host.

## 1. The one supported setup

| Component | Pinned choice |
|---|---|
| Guest OS | Ubuntu 22.04 LTS (arm64 on Apple Silicon, amd64 otherwise) |
| VM manager | Multipass (simplest) or UTM on Apple Silicon; VirtualBox/Multipass on x86 |
| VM resources | 4 vCPU · 8 GB RAM · 40 GB disk (minimum; eval runs want the VM otherwise idle) |
| Python (repo default) | 3.11 (`deadsnakes` PPA on 22.04) |
| Python (controller venv only) | 3.9 — see §4, Ryu does not run on 3.10+ |
| P4 toolchain | p4c + BMv2 built via the `jafingerhut/p4-guide` install script (§3) |
| Mininet | 2.3.x (installed by the same script) |

Create the VM:

```bash
# host (macOS)
brew install multipass
multipass launch 22.04 --name p4dev --cpus 4 --memory 8G --disk 40G
multipass shell p4dev
```

Mount the repo into the VM (`multipass mount ~/path/to/agent-aware-net p4dev:/home/ubuntu/agent-aware-net`)
so you edit on the host and build/run inside the VM.

## 2. Base packages (inside the VM)

```bash
sudo apt update && sudo apt install -y git build-essential python3-pip curl \
  software-properties-common tcpdump tshark tcpreplay iperf3
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt install -y python3.11 python3.11-venv python3.9 python3.9-venv
```

## 3. P4 toolchain: p4c + BMv2 + Mininet + PTF

Use the community-standard install script (builds and wires everything consistently):

```bash
git clone https://github.com/jafingerhut/p4-guide
# use the install script matching Ubuntu 22.04 in that repo (install-p4dev-v6.sh at time of pinning)
cd p4-guide/bin && ./install-p4dev-v6.sh |& tee install.log   # takes ~1–2 h
```

**Required BMv2 rebuild — priority queueing.** Our QoS design (design.md §5.5, §4.1) depends on
`simple_switch` priority queues, which are OFF in default builds. After the script finishes:

```bash
cd ~/behavioral-model            # cloned by the install script
./configure 'CXXFLAGS=-O3 -DSSWITCH_PRIORITY_QUEUEING_ON'
make -j4 && sudo make install && sudo ldconfig
simple_switch --help | grep -i priority   # sanity: binary rebuilt
```

Record the exact BMv2 and p4c commit hashes the script checked out into this file
(`git -C ~/behavioral-model rev-parse HEAD`, same for p4c) the first time anyone installs —
those hashes become the team pin.

## 4. Python environments

**Repo venv (everything except the controller)** — Python 3.11:

```bash
cd ~/agent-aware-net
python3.11 -m venv .venv && source .venv/bin/activate
pip install scikit-learn pandas matplotlib pyyaml scapy pytest ruff
```

**Controller venv — Python 3.9 (Ryu constraint).** Ryu 4.34 (last release, unmaintained)
depends on eventlet APIs removed in Python 3.10+. Decision: the Ryu controller runs in its own
3.9 venv; everything else stays on 3.11. Tradeoff accepted because the controller talks to the
rest of the system only via the switch API and files, never via imports.

```bash
python3.9 -m venv .venv-ryu && source .venv-ryu/bin/activate
pip install ryu==4.34 eventlet==0.30.2 dnspython==1.16.0
ryu-manager --version   # sanity
```

(If this pin set ever breaks, the fallback is the maintained fork `os-ken` — an API-compatible
drop-in that runs on 3.11 — but that changes imports, so it requires a team decision + contracts
note, not a silent switch.)

## 5. Sanity check = milestone M1 prerequisite

```bash
sudo mn --test pingall                      # Mininet works
p4c --version                               # p4c installed
echo 'header x_t {bit<8> f;}' > /tmp/x.p4   # (real check: compile p4src/l2fwd.p4 once it exists)
```

Done when: `make dev-env` (which just verifies the above tools exist and prints versions)
passes for all three developers on their own VMs.

## 6. Rules

- Never run experiments on a VM doing anything else; eval numbers depend on it (design.md §7.18).
- Wireshark/tshark needs the `wireshark` group or sudo inside the VM for live capture.
- Do not upgrade any pinned component mid-project. Toolchain changes are a team decision logged
  in `docs/notebook.md`.
