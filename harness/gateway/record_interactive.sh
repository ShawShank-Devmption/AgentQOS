#!/usr/bin/env bash
# Record one labelled AGENT_INTERACTIVE session: Claude Code on this Mac, driven by you.
# usage (from Terminal.app, WireGuard on): harness/gateway/record_interactive.sh SESSION
#
# Quits every other app, opens the capture window over one pre-opened VM connection, starts
# Claude Code in a fresh folder with a task prompt, and closes the window when you exit Claude.
set -euo pipefail
. "$(dirname "$0")/record_lib.sh"

[ $# -eq 1 ] || {
	echo "usage: harness/gateway/record_interactive.sh SESSION" >&2
	exit 2
}
session=$(slug "$1")
prompt=$(pick_prompt interactive "$session")

preflight "$session"
quit_other_apps
workdir="$SESSIONS_DIR/$session"
mkdir -p "$workdir"
printf '%s\n' "$prompt" >"$workdir/prompt.txt"

start_window "$session" AGENT_INTERACTIVE claude-code-interactive
trap 'exec 3>&-' EXIT # if this script dies, the closed control channel still ends the window
cat <<EOF

=== Recording $session (AGENT_INTERACTIVE). Claude starts with this task:
$prompt
=== Approve its prompts and give follow-ups as you like. Use no other app.
=== Exit Claude (/exit or Ctrl-D) to end the recording.

EOF
status=0
(cd "$workdir" && claude "$prompt") || status=$?
stop_window
trap - EXIT
echo "record: Claude exited with status $status; session '$session' recorded"
