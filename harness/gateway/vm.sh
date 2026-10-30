#!/usr/bin/env bash
# Mac-side operations for the team VM and the live gateway (docs/demo_tasks.md I4).
# SSH goes through IAP only (port 22 is closed to the internet), so this works from any network.
set -euo pipefail

VM=${AAN_VM:-agent-qos-p4dev}
ZONE=${AAN_ZONE:-asia-south2-a}
PROJECT=${AAN_PROJECT:-vivid-rush-513007-k5}
# Python 3.11/3.9 environments built during VM setup; synced trees link to them.
VENV_SRC=${AAN_VENV_SRC:-'~/agent-aware-net-final'}
PROFILES=${AAN_PROFILES:-$HOME/.aan-gateway}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
. "$ROOT/harness/gateway/gateway.env"

usage() {
	cat >&2 <<'EOF'
usage: harness/gateway/vm.sh COMMAND
  start | stop | status      VM lifecycle (stop whenever you are done; it bills per second)
  ssh [ARGS...]              shell, or pass gcloud/ssh args (e.g. --command 'uptime')
  sync                       copy the committed HEAD to ~/cn_project (results/ are kept)
  gate                       make lint, test, check-py39, build on the VM
  install-gateway            (re)install WireGuard gateway scripts and boot unit
  selftest                   tunnel a throwaway client through the gateway and check egress
  add-device NAME            new WireGuard profile; QR + .conf saved under ~/.aan-gateway
  revoke-device NAME         remove a device's access
  tunnel                     forward VM localhost:8080 (dashboard) to http://localhost:8080
  capture-start SESSION DEVICE LABEL ACTIVITY
                             full-packet capture of DEVICE's traffic; LABEL is HUMAN_INTERACTIVE,
                             AGENT_INTERACTIVE or AGENT_BULK; start the activity only afterwards
  capture-stop SESSION       stop it; writes results/corpus/SESSION/labels.csv on the VM
EOF
	exit 2
}

# gcloud flags go before "$@": anything after "--" in "$@" is passed to ssh itself.
instances() { gcloud compute instances "$1" "$VM" --zone="$ZONE" --project="$PROJECT" "${@:2}"; }
ssh_vm() {
	# --verbosity=error hides the IAP tunnel's NumPy performance warning (harmless at our rates).
	gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap --quiet --verbosity=error "$@"
}

device_name() {
	[[ ${1:-} =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || { echo "vm.sh: invalid device name" >&2; exit 2; }
	echo "$1"
}

slug() {
	[[ ${1:-} =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] || { echo "vm.sh: invalid name '${1:-}'" >&2; exit 2; }
	echo "$1"
}

training_label() {
	[[ ${1:-} =~ ^(HUMAN_INTERACTIVE|AGENT_INTERACTIVE|AGENT_BULK)$ ]] \
		|| { echo "vm.sh: invalid label '${1:-}'" >&2; exit 2; }
	echo "$1"
}

capture_start() {
	ssh_vm --command "bash -s -- $1 $2 $3 $4 $AAN_LAN_SW" <<'REMOTE'
set -euo pipefail
session=$1 device=$2 label=$3 activity=$4 interface=$5
ip=$(sudo awk '/^AllowedIPs/ {sub("/32", "", $3); print $3}' "/etc/aan-gateway/peers/$device.conf")
cd ~/cn_project
sudo .venv/bin/python -m harness.capture start "results/corpus/$session" \
	--interface "$interface" --device-ip "$ip" --label "$label" --framework "$activity"
REMOTE
}

sync_tree() {
	local commit
	commit=$(git -C "$ROOT" rev-parse HEAD)
	if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
		echo "vm.sh: warning: uncommitted changes are not synced (git archive HEAD)" >&2
	fi
	git -C "$ROOT" archive --format=tar HEAD | gzip | ssh_vm --command 'cat >~/cn_project.upload.tgz'
	ssh_vm --command "bash -s -- $commit $VENV_SRC" <<'REMOTE'
set -euo pipefail
commit=$1
venvs=$2
new=$(mktemp -d ~/cn_project.new.XXXXXX)
tar -xzf ~/cn_project.upload.tgz -C "$new"
rm -f ~/cn_project.upload.tgz
if [ -d ~/cn_project/results ]; then mv ~/cn_project/results "$new/results"; fi
rm -rf ~/cn_project.old
if [ -d ~/cn_project ]; then mv ~/cn_project ~/cn_project.old; fi
mv "$new" ~/cn_project
ln -sfn "$venvs/.venv" ~/cn_project/.venv
ln -sfn "$venvs/.venv-ryu" ~/cn_project/.venv-ryu
echo "$commit" >~/cn_project/.synced-commit
echo "synced $commit"
REMOTE
}

fetch_profile() {
	local name=$1
	(umask 077 && mkdir -p "$PROFILES")
	for ext in conf png; do
		ssh_vm --command "sudo base64 /etc/aan-gateway/clients/$name.$ext" \
			| base64 --decode >"$PROFILES/$name.$ext"
		chmod 600 "$PROFILES/$name.$ext"
	done
	echo "profile: $PROFILES/$name.conf (laptops), QR: $PROFILES/$name.png (phones)"
	echo "both hold the device's private key; delete them once imported"
}

wait_for_ssh() {
	local attempt
	for attempt in $(seq 1 18); do
		if ssh_vm --command true </dev/null >/dev/null 2>&1; then
			echo "vm.sh: VM is up"
			return 0
		fi
		echo "vm.sh: waiting for the VM to boot ($attempt/18)" >&2
		sleep 10
	done
	echo "vm.sh: VM did not accept SSH within 3 minutes" >&2
	return 1
}

case "${1:-}" in
start)
	# RUNNING only means the VM was scheduled; sshd needs another 30-60 s, and IAP reports
	# "4003: failed to connect to backend" until it is up.
	instances start
	wait_for_ssh
	;;
stop) instances stop ;;
status)
	instances describe \
		--format='value(name,status,machineType.basename(),networkInterfaces[0].accessConfigs[0].natIP)'
	;;
ssh)
	shift
	ssh_vm "$@"
	;;
sync) sync_tree ;;
gate)
	ssh_vm --command 'cd ~/cn_project && make lint && make test \
		&& make check-py39 PYTHON39=.venv-ryu/bin/python && make build'
	;;
install-gateway) ssh_vm --command 'sudo ~/cn_project/harness/gateway/install.sh' ;;
selftest) ssh_vm --command 'sudo /usr/local/lib/aan-gateway/selftest.sh' ;;
add-device)
	name=$(device_name "${2:-}")
	ssh_vm --command "sudo /usr/local/lib/aan-gateway/add_device.sh $name" >/dev/null
	fetch_profile "$name"
	;;
revoke-device)
	name=$(device_name "${2:-}")
	ssh_vm --command "sudo /usr/local/lib/aan-gateway/revoke_device.sh $name"
	rm -f "$PROFILES/$name.conf" "$PROFILES/$name.png"
	;;
tunnel) ssh_vm -- -N -L 8080:localhost:8080 ;;
capture-start)
	# Assignments, not inline arguments: set -e ignores a failing $(...) inside an argument list.
	session=$(slug "${2:-}")
	device=$(device_name "${3:-}")
	label=$(training_label "${4:-}")
	activity=$(slug "${5:-}")
	capture_start "$session" "$device" "$label" "$activity"
	;;
capture-stop)
	session=$(slug "${2:-}")
	ssh_vm --command "cd ~/cn_project && sudo .venv/bin/python -m harness.capture stop \
		results/corpus/$session; cat results/corpus/$session/session.json"
	;;
*) usage ;;
esac
