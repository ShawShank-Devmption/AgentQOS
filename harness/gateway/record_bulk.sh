#!/usr/bin/env bash
# Record one labelled AGENT_BULK session: headless Claude Code fanning out polite crawlers.
# usage (from Terminal.app, WireGuard on): harness/gateway/record_bulk.sh SESSION
#
# Same checks, app quitting and capture window as record_interactive.sh. Claude runs without a
# person (claude -p) and may only run harness/agents/crawler.py, which enforces robots.txt, per-site
# rate limits and caps; the window closes when Claude finishes (at most ~20 min, 60 min VM cap).
set -euo pipefail
. "$(dirname "$0")/record_lib.sh"

[ $# -eq 1 ] || {
	echo "usage: harness/gateway/record_bulk.sh SESSION" >&2
	exit 2
}
session=$(slug "$1")
python=${AAN_PYTHON:-$ROOT/.venv/bin/python}
crawler="$ROOT/harness/agents/crawler.py"
[ -x "$python" ] || die "missing $python: create the repository virtual environment first"
command=("$python" "$crawler")
prompt=$(pick_prompt bulk "$session")
prompt=${prompt//@CRAWLER@/${command[*]}}

preflight "$session"
quit_other_apps
workdir="$SESSIONS_DIR/$session"
mkdir -p "$workdir"
printf '%s\n' "$prompt" >"$workdir/prompt.txt"

start_window "$session" AGENT_BULK claude-code-crawler
trap 'exec 3>&-' EXIT # if this script dies, the closed control channel still ends the window
cat <<EOF

=== Recording $session (AGENT_BULK). Headless Claude is crawling; this takes up to ~20 minutes.
=== Don't use the Mac meanwhile. Its task is in $workdir/prompt.txt.

EOF
status=0
(cd "$workdir" && claude -p "$prompt" --allowedTools "Bash(${command[*]}:*)" Read Write) \
	|| status=$?
stop_window
trap - EXIT
echo "record: Claude exited with status $status; session '$session' recorded"
