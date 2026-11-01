#!/usr/bin/env bash
# Record one labelled AGENT_INTERACTIVE session: a coding agent on this Mac, driven by you.
# usage (from Terminal.app, WireGuard on):
#   harness/gateway/record_interactive.sh [--agent claude|codex] SESSION
#
# Quits every other app, opens the capture window over one pre-opened VM connection, starts the
# agent (Claude Code by default, or Codex CLI) in a fresh folder with a task prompt, and closes the
# window when you exit the agent.
set -euo pipefail
. "$(dirname "$0")/record_lib.sh"

parse_args "harness/gateway/record_interactive.sh [--agent claude|codex] SESSION" "$@"
session=$SESSION
prompt=$(pick_prompt interactive "$session")

preflight "$session"
quit_other_apps
workdir="$SESSIONS_DIR/$session"
mkdir -p "$workdir"
printf '%s\n' "$prompt" >"$workdir/prompt.txt"

start_window "$session" AGENT_INTERACTIVE "$(framework interactive)"
trap 'exec 3>&-' EXIT # if this script dies, the closed control channel still ends the window
case $AGENT in
claude) quit="/exit or Ctrl-D" ;;
codex) quit="/quit, or Ctrl-C twice" ;;
esac
cat <<EOF

=== Recording $session (AGENT_INTERACTIVE). $AGENT starts with this task:
$prompt
=== Approve its prompts and give follow-ups as you like. Use no other app.
=== Exit $AGENT ($quit) to end the recording.

EOF
status=0
case $AGENT in
claude) (cd "$workdir" && claude "$prompt") || status=$? ;;
codex)
	# --no-daemon: a shared background server would hold model connections opened before the
	# window (excluded from labels). --search: web search, as Claude Code has. Same sandbox and
	# approvals for everyone: commands that need the network ask you first.
	(cd "$workdir" && CODEX_HOME=$CODEX_RECORD_HOME codex --no-daemon --search \
		-s workspace-write -a on-request "$prompt") || status=$?
	;;
esac
stop_window
trap - EXIT
echo "record: $AGENT exited with status $status; session '$session' recorded"
