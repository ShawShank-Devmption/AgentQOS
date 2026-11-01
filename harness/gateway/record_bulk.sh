#!/usr/bin/env bash
# Record one labelled AGENT_BULK session: a headless coding agent fanning out polite crawlers.
# usage (from Terminal.app, WireGuard on): harness/gateway/record_bulk.sh [--agent claude|codex] SESSION
#
# Same checks, app quitting and capture window as record_interactive.sh. The agent runs without a
# person (claude -p, or codex exec) and launches harness/agents/crawler.py: one burst of small
# crawlers started together from random public pages, with robots.txt, per-site rate limits and caps
# enforced by the crawler. The window closes when the agent finishes (about a minute; 60 min cap).
set -euo pipefail
. "$(dirname "$0")/record_lib.sh"

parse_args "harness/gateway/record_bulk.sh [--agent claude|codex] SESSION" "$@"
session=$SESSION
python=${AAN_PYTHON:-$ROOT/.venv/bin/python}
crawler="$ROOT/harness/agents/crawler.py"
[ -x "$python" ] || die "missing $python: create the repository virtual environment first"
command=("$python" "$crawler")

# Everything Codex did besides talking and running the crawler (other commands, MCP or web
# tools), one per line, from its --json event log.
other_commands() {
	"$python" - "$1" <<'EOF'
import json
import sys

QUIET = {"agent_message", "reasoning", "todo_list"}
for line in open(sys.argv[1], encoding="utf-8"):
    event = json.loads(line)
    item = event.get("item", {})
    kind = item.get("type")
    if event.get("type") != "item.completed" or kind in QUIET:
        continue
    command = item.get("command", "")
    if kind != "command_execution" or "crawler.py" not in command:
        print(f"{kind}: {command or item.get('tool') or item.get('query') or ''}")
EOF
}
prompt=$(pick_prompt bulk "$session")
prompt=${prompt//@CRAWLER@/${command[*]}}

preflight "$session"
quit_other_apps
workdir="$SESSIONS_DIR/$session"
mkdir -p "$workdir"
printf '%s\n' "$prompt" >"$workdir/prompt.txt"

start_window "$session" AGENT_BULK "$(framework crawler)"
trap 'exec 3>&-' EXIT # if this script dies, the closed control channel still ends the window
cat <<EOF

=== Recording $session (AGENT_BULK). Headless $AGENT starts a crawler burst; about a minute.
=== Don't use the Mac meanwhile. Its task is in $workdir/prompt.txt.

EOF
status=0
case $AGENT in
claude)
	(cd "$workdir" && claude -p "$prompt" --allowedTools "Bash(${command[*]}:*)" Read Write) \
		|| status=$?
	;;
codex)
	# codex exec cannot be limited to one command as --allowedTools does: its sandbox keeps writes
	# in the session folder, network is allowed for the crawler, and every command it ran is
	# checked after the window.
	(cd "$workdir" && CODEX_HOME=$CODEX_RECORD_HOME codex exec --skip-git-repo-check \
		-s workspace-write -c sandbox_workspace_write.network_access=true --json "$prompt" \
		</dev/null >codex-events.jsonl) || status=$?
	;;
esac
stop_window
trap - EXIT
echo "record: $AGENT exited with status $status; session '$session' recorded"
if [ "$AGENT" = codex ]; then
	others=$(other_commands "$workdir/codex-events.jsonl")
	[ -z "$others" ] || echo "record: WARNING, Codex also ran (tell Shashank): $others" >&2
fi
