#!/usr/bin/env bash
# Shared steps of record_interactive.sh and record_bulk.sh (source it): preflight checks, quitting
# other apps, and a capture window controlled over one SSH connection opened beforehand.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/vm_lib.sh"

DEVICE=${AAN_RECORD_DEVICE:-team-laptop}
CAP_SECONDS=${AAN_RECORD_CAP_S:-3600}
SESSIONS_DIR=${AAN_SESSIONS_DIR:-$HOME/aan-agent-sessions}
# Never quit: the terminal running the recorder, Finder, and the WireGuard app that owns the tunnel.
KEEP_APPS=(com.apple.Terminal com.apple.finder com.wireguard.macos)

die() {
	echo "record: $*" >&2
	exit 1
}

# One prompt per line in harness/agents/prompts/KIND.txt, picked deterministically per session name.
pick_prompt() {
	local file="$ROOT/harness/agents/prompts/$1.txt" count index
	count=$(grep -c . "$file")
	index=$(($(printf '%s' "$2" | cksum | cut -d' ' -f1) % count + 1))
	grep . "$file" | sed -n "${index}p"
}

preflight() {
	local session=$1 state ip seen
	[ "${TERM_PROGRAM:-}" = "Apple_Terminal" ] \
		|| die "run this from Terminal.app: it quits every other app, including editors"
	command -v claude >/dev/null || die "claude (Claude Code) is not installed"
	[ ! -e "$SESSIONS_DIR/$session" ] || die "session '$session' already exists locally"
	read -r state ip < <(instances describe \
		--format='value(status,networkInterfaces[0].accessConfigs[0].natIP)')
	[ "$state" = RUNNING ] || die "VM is $state; run harness/gateway/vm.sh start"
	seen=$(curl -fsS --max-time 8 https://1.1.1.1/cdn-cgi/trace | sed -n 's/^ip=//p') \
		|| die "no internet; is WireGuard on?"
	[ "$seen" = "$ip" ] || die "traffic leaves via $seen, not the gateway $ip: turn WireGuard on"
	ssh_vm --command "test ! -e ~/aan-results/corpus/$session" </dev/null \
		|| die "session '$session' already exists on the VM"
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
