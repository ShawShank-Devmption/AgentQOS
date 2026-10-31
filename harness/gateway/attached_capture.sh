#!/usr/bin/env bash
# VM side of a recorder session (record_*.sh): capture one device until the controlling SSH
# connection sends a line or closes, with a systemd cap in case it never does.
# usage (on the VM, from ~/cn_project): attached_capture.sh SESSION DEVICE LABEL ACTIVITY CAP_SECONDS
#
# The connection is opened before the window starts, so its own packets are pre-existing TCP and
# never labelled; the stop travels over it, so ending a session adds no traffic from the device.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/gateway.env"

session=$1 device=$2 label=$3 activity=$4 cap_s=$5
dir="results/corpus/$session"
unit="aan-capture-$session"
python=(sudo /usr/bin/env PYTHONDONTWRITEBYTECODE=1 "$HOME/cn_project/.venv/bin/python" -m harness.capture)
fail() { echo "attached_capture: $*" >&2; exit 1; }

peer="$AAN_STATE_DIR/peers/$device.conf"
sudo test -e "$peer" || fail "no device '$device'"
ip=$(sudo awk '/^AllowedIPs/ {sub("/32", "", $3); print $3}' "$peer")
cd "$HOME/cn_project"
"${python[@]}" start "$dir" --interface "$AAN_LAN_SW" --device-ip "$ip" --label "$label" \
	--framework "$activity"
sudo systemd-run --quiet --unit "$unit" --on-active="${cap_s}s" \
	--working-directory="$HOME/cn_project" "${python[@]:1}" stop "$dir"
echo "AAN_READY"

# A dropped connection must still close the window: ignore the hangup and write the stop's output
# to a file, because the pipe back to the Mac may already be gone.
trap '' HUP PIPE
read -r _ || true # a line ("stop") or EOF (connection closed) ends the window
if systemctl is-active --quiet "$unit.timer"; then
	sudo systemctl stop "$unit.timer"
fi
if grep -q '"status": "capturing"' "$dir/session.json"; then
	"${python[@]}" stop "$dir" >>"$dir/attached.log" 2>&1 || true
fi
cat "$dir/session.json"
