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
EOF
	exit 2
}

# gcloud flags go before "$@": anything after "--" in "$@" is passed to ssh itself.
instances() { gcloud compute instances "$1" "$VM" --zone="$ZONE" --project="$PROJECT" "${@:2}"; }
ssh_vm() {
	gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap --quiet "$@"
}

device_name() {
	[[ ${1:-} =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || { echo "vm.sh: invalid device name" >&2; exit 2; }
	echo "$1"
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

case "${1:-}" in
start) instances start ;;
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
*) usage ;;
esac
