#!/usr/bin/env bash
# Shared steps of record_interactive.sh and record_bulk.sh (source it): agent choice, preflight
# checks, quitting other apps, and a capture window controlled over one SSH connection opened
# beforehand.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/vm_lib.sh"

CAP_SECONDS=${AAN_RECORD_CAP_S:-3600}
SESSIONS_DIR=${AAN_SESSIONS_DIR:-$HOME/aan-agent-sessions}
# Codex records from its own home: a personal config.toml (model provider, MCP servers, hooks),
# skills and AGENTS.md add their own traffic and differ between teammates. One-time login:
# CODEX_HOME=~/.aan-codex codex login
CODEX_RECORD_HOME=${AAN_CODEX_HOME:-$HOME/.aan-codex}
# Plugins and ChatGPT apps off: Codex otherwise downloads a ~50 MB plugin catalog at start-up,
# inside the capture window, from the same host as its model traffic (so it cannot be filtered).
CODEX_RECORD_FLAGS=(--disable remote_plugin --disable plugins --disable apps)
AGENT=claude
# Never quit: the terminal running the recorder, Finder, and the WireGuard app that owns the tunnel.
KEEP_APPS=(com.apple.Terminal com.apple.finder com.wireguard.macos)

die() {
	echo "record: $*" >&2
	exit 1
}

# Sets AGENT (claude, the default, or codex) and SESSION from: [--agent claude|codex] SESSION
parse_args() {
	local usage=$1
	shift
	if [ "${1:-}" = --agent ] && [ $# -ge 2 ]; then
		AGENT=$2
		shift 2
	fi
	if [ $# -ne 1 ] || [[ ! $AGENT =~ ^(claude|codex)$ ]]; then
		echo "usage: $usage" >&2
		exit 2
	fi
	SESSION=$(slug "$1")
}

# source_framework in labels.csv: which agent made the traffic, and in which mode.
framework() {
	case $AGENT in
	claude) echo "claude-code-$1" ;;
	codex) echo "codex-cli-$1" ;;
	esac
}

# One prompt per line in harness/agents/prompts/KIND.txt, picked deterministically per session name.
pick_prompt() {
	local file="$ROOT/harness/agents/prompts/$1.txt" count index
	count=$(grep -c . "$file")
	index=$(($(printf '%s' "$2" | cksum | cut -d' ' -f1) % count + 1))
	grep . "$file" | sed -n "${index}p"
}

# This Mac's WireGuard address: the capture filters on it, so it decides which device is labelled.
tunnel_address() {
	ifconfig | awk -v net="${AAN_CLIENT_NET%0/24}" \
		'$1 == "inet" && index($2, net) == 1 { print $2; exit }'
}

# Sets DEVICE: the profile this Mac's tunnel uses, looked up on the VM (never a default name, so
# one person's session can never capture another person's device).
preflight() {
	local session=$1 state ip seen tunnel remote
	[ "${TERM_PROGRAM:-}" = "Apple_Terminal" ] \
		|| die "run this from Terminal.app: it quits every other app, including editors"
	command -v "$AGENT" >/dev/null || die "$AGENT is not installed"
	if [ "$AGENT" = codex ]; then
		CODEX_HOME=$CODEX_RECORD_HOME codex login status >/dev/null 2>&1 \
			|| die "Codex has no recording login; run once: CODEX_HOME=$CODEX_RECORD_HOME codex login"
	fi
	[ ! -e "$SESSIONS_DIR/$session" ] || die "session '$session' already exists locally"
	read -r state ip < <(instances describe \
		--format='value(status,networkInterfaces[0].accessConfigs[0].natIP)')
	[ "$state" = RUNNING ] || die "VM is $state; run harness/gateway/vm.sh start"
	seen=$(curl -fsS --max-time 8 https://1.1.1.1/cdn-cgi/trace | sed -n 's/^ip=//p') \
		|| die "no internet; is WireGuard on?"
	[ "$seen" = "$ip" ] || die "traffic leaves via $seen, not the gateway $ip: turn WireGuard on"
	tunnel=$(tunnel_address)
	[ -n "$tunnel" ] || die "no WireGuard address in $AAN_CLIENT_NET on this Mac"
	remote="test ! -e ~/aan-results/corpus/$session"
	remote+=" || { echo \"session '$session' already exists on the VM\" >&2; exit 1; }"
	remote+="; peer=\$(sudo grep -lx 'AllowedIPs = $tunnel/32' $AAN_STATE_DIR/peers/*.conf)"
	remote+=" || { echo 'no device profile has address $tunnel' >&2; exit 1; }"
	remote+="; basename \"\$peer\" .conf"
	DEVICE=$(ssh_vm --command "$remote" </dev/null) || die "VM check failed (see above)"
	DEVICE=$(device_name "$DEVICE")
	echo "record: capturing device '$DEVICE' ($tunnel)"
}

running_app_ids() {
	local query='tell application "System Events" to get bundle identifier of'
	query+=' (processes where background only is false)'
	osascript -e "$query" | tr ',' '\n' | sed 's/^ *//' | grep -v '^missing value$' \
		| grep -vxF -f <(printf '%s\n' "${KEEP_APPS[@]}") || true
}

quit_other_apps() {
	local id remaining attempt answer
	while read -r id; do
		[ -n "$id" ] || continue
		echo "record: quitting $id"
		osascript -e "tell application id \"$id\" to quit" >/dev/null 2>&1 \
			|| echo "record: could not ask $id to quit" >&2
	done < <(running_app_ids)
	for attempt in $(seq 1 20); do
		remaining=$(running_app_ids)
		[ -n "$remaining" ] || return 0
		sleep 1
	done
	echo "record: still open (unsaved work?): $(echo "$remaining" | tr '\n' ' ')" >&2
	read -r -p "Continue anyway? Their traffic will be labelled too [y/N] " answer </dev/tty
	[ "$answer" = y ] || die "cancelled before recording"
}

# Opens the control connection, starts the capture and waits for the window to open. File
# descriptor 3 is the control channel (bash 3.2 on macOS has no named descriptors): writing "stop"
# or closing it ends the window. Sets REMOTE_PID and REMOTE_LOG.
start_window() {
	local session=$1 label=$2 activity=$3 fifo attempt remote
	remote="bash ~/cn_project/harness/gateway/attached_capture.sh"
	remote+=" $session $DEVICE $label $activity $CAP_SECONDS"
	CONTROL_DIR=$(mktemp -d)
	fifo="$CONTROL_DIR/control"
	REMOTE_LOG="$CONTROL_DIR/remote.log"
	mkfifo "$fifo"
	# Ctrl-C is how you interrupt Claude; it must not reach the control connection.
	(
		trap '' INT QUIT
		ssh_vm --command "$remote"
	) <"$fifo" >"$REMOTE_LOG" 2>&1 &
	REMOTE_PID=$!
	exec 3>"$fifo"
	for attempt in $(seq 1 90); do
		grep -q '^AAN_READY$' "$REMOTE_LOG" && return 0
		kill -0 "$REMOTE_PID" 2>/dev/null || {
			cat "$REMOTE_LOG" >&2
			die "capture did not start"
		}
		sleep 1
	done
	die "capture did not start within 90 s"
}

# Ends the window over the already-open connection and prints the session result.
stop_window() {
	printf 'stop\n' >&3
	exec 3>&-
	wait "$REMOTE_PID" || echo "record: VM reported an error; see the session status" >&2
	sed -n '/^{/,/^}/p' "$REMOTE_LOG"
	rm -rf "$CONTROL_DIR"
}
