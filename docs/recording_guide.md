# Recording labelled sessions — guide for teammates

We are building the real training corpus (tasks P3.4, P3.8, G6). A **session** is one of your
devices doing one kind of activity while the VM captures its traffic through the WireGuard
gateway. Every packet the device sends during the session gets the session's label (design.md
§4.4, §7.20), so a clean session means the device does that one thing and nothing else.

Roles: **Shashank** owns the VM (start, stop, sync), grants access, extracts features and
retrains. **You** record sessions and send him the list.

## 1. What you need

| Item | Notes |
|---|---|
| A Mac | The one-command recorders need macOS Terminal.app. Without a Mac you can still do phone calls (§5.3) from any machine with gcloud and bash. |
| A phone (Android or iOS) | For video-call sessions |
| A Google account | Send it to Shashank; he grants VM access |
| gcloud CLI | Install from <https://cloud.google.com/sdk/docs/install>, then `gcloud auth login` |
| WireGuard app | Mac App Store and your phone's app store |
| Claude Code and/or Codex CLI | At least one, installed and logged in; sessions use your own Claude or ChatGPT account. Codex also needs a recording login (§2 step 6). |
| git and Python 3.11 | For the repository and its virtual environment |
| Internet | Any network works — it does not need to be Shashank's. It must allow outbound UDP port 51820 (some campus/office Wi-Fi blocks it; then use a phone hotspot). |

## 2. One-time setup

1. Send Shashank your Google account; wait until he says access is granted.
2. Sign in and get the code:
   ```sh
   gcloud auth login
   git clone git@github.com:ShawShank-Devmption/AgentQOS.git cn_project
   cd cn_project
   git checkout shashank
   python3.11 -m venv .venv
   ```
3. Check the VM is running (`harness/gateway/vm.sh status`), then register your SSH key once:
   `harness/gateway/vm.sh ssh --command true`. If it fails with *does not have access to service
   account*, tell Shashank (he grants one more role).
4. Create **your own** WireGuard profiles — never use someone else's:
   ```sh
   harness/gateway/vm.sh add-device NAME-mac      # e.g. ananya-mac
   harness/gateway/vm.sh add-device NAME-phone    # e.g. ananya-phone
   ```
   - Mac: WireGuard app → *Import Tunnel(s) from File* → `~/.aan-gateway/NAME-mac.conf`.
   - Phone: WireGuard app → *+* → *Scan from QR code* → open `~/.aan-gateway/NAME-phone.png`.
   - Then delete the files in `~/.aan-gateway/`: they contain private keys.
5. Test each device: tunnel on, open <https://1.1.1.1/cdn-cgi/trace>; `ip=34.131.42.32` means its
   traffic goes through the gateway.
6. **Codex users only:** log Codex in once for recordings:
   `CODEX_HOME=~/.aan-codex codex login`. Recordings run Codex from this separate home, so your
   personal Codex settings, model provider, MCP servers, skills and AGENTS.md — which add their own
   traffic and differ between people — stay out of the data. Your normal Codex is untouched.
7. The first recorder run asks to let Terminal control *System Events* (needed to quit apps):
   click **Allow**.

## 3. VM rules

- **Only Shashank stops the VM.** Never run `vm.sh stop`, not even at the end of the day — stopping
  it ends everyone's recording in progress. When you are done, tell Shashank.
- Before a recording block run `harness/gateway/vm.sh status`. `RUNNING`: go ahead. Anything else:
  ask Shashank to start it.
- The only `vm.sh` commands you use are `status`, `ssh --command true` (setup), `add-device` (your
  own devices), and `capture-start` / `capture-stop` / `capture-status`. Never `sync`, `gate`,
  `install-gateway` or `revoke-device`: they change the shared VM.
- Post in the team chat when you start and when you finish a recording block.
- `git pull` before each block so your recorder scripts match the VM.

## 4. Clean-session checklist (every session)

**Before**

- [ ] WireGuard on with **your** profile for this device. No other VPN. On the Mac and iPhone,
      iCloud Private Relay off for the session.
- [ ] The device is otherwise idle: quit every app you do not need (the agent recorders quit
      them for you on the Mac). Pause cloud sync (iCloud Drive, Google Drive, Dropbox, OneDrive),
      backups (Time Machine; Google Photos/WhatsApp backup on the phone) and app/system updates.
- [ ] Stable network. Do not switch Wi-Fi or hotspot during the session; with the phone, stay put.
      The capture measures timing as packets reach the VM, so your connection's jitter is part
      of the data.
- [ ] A new session name: `NAME-KIND-NN`, lowercase letters, digits and hyphens
      (`ananya-agent-01`, `ananya-codex-bulk-01`, `ananya-call-02`). Names are shared by the whole
      team and never reused; a taken name is refused.

**During**

- [ ] Start the activity only **after** the capture is open (the recorder prints
      `=== Recording …`, or `capture-start` has returned). Connections already open before the
      window are only partly excluded, and UDP/QUIC (video calls, much browser traffic) cannot be
      excluded at all — so open the call app or browser after the capture starts.
- [ ] Do only that activity. In an agent session, use nothing but the Terminal running the agent;
      do not open a browser even if the agent prints a link.
- [ ] End the activity before ending the capture (hang up, then `capture-stop`).

**After**

- [ ] The printed JSON shows `"status": "complete"`. `failed` means packet drops: repeat with a new
      name. A large `excluded_midstream_tcp_flows` (well above ~10) means something was already
      running when the window opened: note it, and repeat if you can.
- [ ] Add a line to your log: session name, activity, network (home Wi-Fi, hotspot, …), anything
      unusual. Send the log to Shashank at the end of the block.

## 5. The sessions

Run everything from the repository root on the Mac, in **Terminal.app**.

### 5.1 Coding-agent session — AGENT_INTERACTIVE (Mac)

```sh
harness/gateway/record_interactive.sh NAME-agent-01                       # Claude Code
harness/gateway/record_interactive.sh --agent codex NAME-codex-agent-01   # Codex CLI
```

It checks the VM and your tunnel, prints `record: capturing device 'NAME-mac' (10.42.0.x)` — that
is the device being labelled — quits other apps (apps with unsaved work ask first), opens the
capture and starts the agent with a task from `harness/agents/prompts/interactive.txt`. Approve
its tool prompts and give one to three follow-ups in the same direction ("add tests", "also handle
errors"); aim for 10–20 minutes. Codex runs in its workspace sandbox and asks before any command
that needs the network: approve those as you normally would.

**Exiting the agent ends the recording:** Claude Code `/exit` or Ctrl-D; Codex `/quit` or Ctrl-C
twice. A single Ctrl-C only interrupts the agent. The VM ends the window by itself after 60 minutes
or if your connection drops.

### 5.2 Crawler burst — AGENT_BULK (Mac)

```sh
harness/gateway/record_bulk.sh NAME-bulk-01                        # Claude Code
harness/gateway/record_bulk.sh --agent codex NAME-codex-bulk-01    # Codex CLI
```

Same checks; then the agent runs headless and launches a burst of polite crawlers (about a
minute). Do not touch the Mac until it prints the result. Codex cannot be limited to the crawler
command the way Claude Code is, so the recorder checks afterwards: a line
`record: WARNING, Codex also ran …` means it did something else — the session is recorded, but
send that line to Shashank.

### 5.3 Video call — HUMAN_INTERACTIVE (phone)

Only the phone is captured; you control the session from the Mac.

```sh
harness/gateway/vm.sh capture-start NAME-call-01 NAME-phone HUMAN_INTERACTIVE video-call
#   wait until it returns; close every other app on the phone; then start the call
#   3–5 minutes, camera on; hang up
harness/gateway/vm.sh capture-stop NAME-call-01
```

Vary the app across sessions (Google Meet, WhatsApp, Zoom). The other person can be anywhere;
tell them the call's traffic is captured (it is encrypted; no content is readable).

### 5.4 Browsing — HUMAN_INTERACTIVE (Mac)

Quit every app except WireGuard and Terminal, **including the browser**, then:

```sh
harness/gateway/vm.sh capture-start NAME-browse-01 NAME-mac HUMAN_INTERACTIVE browsing 300
```

When it returns, open one browser and browse normally for the 5 minutes (news, Wikipedia,
shopping, documentation). Do not use Terminal or `vm.sh` during the window: the trailing `300`
makes the VM close the window itself, so the Mac's own connection to the VM is never labelled
as human. Afterwards:

```sh
harness/gateway/vm.sh capture-status NAME-browse-01
```

### 5.5 Optional: another agent — AGENT_INTERACTIVE (Mac)

For an agent the recorders do not drive (Gemini CLI, Cursor's agent, …), quit other apps, then:

```sh
harness/gateway/vm.sh capture-start NAME-gemini-01 NAME-mac AGENT_INTERACTIVE gemini-cli 900
```

Start the agent in Terminal immediately with a task like those in
`harness/agents/prompts/interactive.txt`, and exit it before the 15 minutes end; then
`vm.sh capture-status NAME-gemini-01`. The last word before the duration names the framework and
is recorded as `source_framework`.

## 6. How many

Per person (about 1.5–2 hours; spreading it over two or three days and networks is good):

| Session | Section | Count |
|---|---|---|
| Coding agent (Claude Code or Codex) | §5.1 | 5 |
| Crawler burst (Claude Code or Codex) | §5.2 | 5 |
| Video call on phone | §5.3 | 3 |
| Browsing on Mac | §5.4 | 2 |
| Other agent (optional) | §5.5 | 2 |

If you have both Claude Code and Codex, split the agent and crawler sessions between them: each
agent is a separate framework in the corpus.

Why: the model is tested on whole sessions it never trained on. With four or five sessions per
class the score depended almost entirely on which one session was held out (held-out macro-F1
0.55 to 0.89 between splits). About 20 sessions per class from three or more people gives a stable
number and a test on devices the model has never seen.

## 7. When something goes wrong

| Message or symptom | Fix |
|---|---|
| `run this from Terminal.app` | Use macOS Terminal.app, not VS Code's terminal or iTerm |
| `claude is not installed` / `codex is not installed` | Install that agent and log in |
| `Codex has no recording login` | Run once: `CODEX_HOME=~/.aan-codex codex login` |
| `record: WARNING, Codex also ran …` | Session is recorded; send the line to Shashank |
| `VM is TERMINATED` | Ask Shashank to start it |
| `traffic leaves via …, not the gateway` | Turn the WireGuard tunnel on |
| `no WireGuard address in 10.42.0.0/24 on this Mac` | Tunnel not active, or another VPN took over |
| `no device profile has address …` | Your profile was removed or made elsewhere: ask Shashank |
| `session '…' already exists` | Pick a new name |
| `capture did not start` / `VM check failed` | Send the full output to Shashank |
| `"status": "failed"` | Packet drops: repeat with a new name |
| Tunnel on but no internet, no handshake in the app | The network blocks UDP 51820: use a phone hotspot |
| `does not have access to service account` | Shashank grants you one more role |

## 8. Privacy

- Full packets of your own devices are stored on the VM. Most content is encrypted (HTTPS, call
  media), but DNS names and IP addresses are visible: do not log into anything sensitive
  (banking, health) during a session.
- Record only your own devices, never someone else's.
- The files in `~/.aan-gateway/` contain private keys: delete them once imported and never share
  them.
