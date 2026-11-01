#!/usr/bin/env bash
# VM access and argument validation shared by vm.sh and the record_*.sh recorders (source it).
# SSH goes through IAP only (port 22 is closed to the internet), so this works from any network.
set -euo pipefail

VM=${AAN_VM:-agent-qos-p4dev}
ZONE=${AAN_ZONE:-asia-south2-a}
PROJECT=${AAN_PROJECT:-vivid-rush-513007-k5}
# Everyone logs in as the one VM account that owns ~/cn_project, the venvs and ~/aan-results;
# gcloud would otherwise create an empty account named after each person's local user.
VM_USER=${AAN_VM_USER:-shashankbhat}
# Python 3.11/3.9 environments built during VM setup; synced trees link to them.
VENV_SRC=${AAN_VENV_SRC:-'~/agent-aware-net-final'}
PROFILES=${AAN_PROFILES:-$HOME/.aan-gateway}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$ROOT/harness/gateway/gateway.env"

# gcloud flags go before "$@": anything after "--" in "$@" is passed to ssh itself.
instances() { gcloud compute instances "$1" "$VM" --zone="$ZONE" --project="$PROJECT" "${@:2}"; }
ssh_vm() {
	# --verbosity=error hides the IAP tunnel's NumPy performance warning (harmless at our rates).
	gcloud compute ssh "$VM_USER@$VM" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap --quiet \
		--verbosity=error "$@"
}

device_name() {
	[[ ${1:-} =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || { echo "error: invalid device name" >&2; exit 2; }
	echo "$1"
}

slug() {
	[[ ${1:-} =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] || { echo "error: invalid name '${1:-}'" >&2; exit 2; }
	echo "$1"
}

training_label() {
	[[ ${1:-} =~ ^(HUMAN_INTERACTIVE|AGENT_INTERACTIVE|AGENT_BULK)$ ]] \
		|| { echo "error: invalid label '${1:-}'" >&2; exit 2; }
	echo "$1"
}

duration() {
	[[ ${1:-} =~ ^[0-9]{2,4}$ ]] && [ "$1" -ge 30 ] && [ "$1" -le 3600 ] \
		|| { echo "error: SECONDS must be 30-3600, got '${1:-}'" >&2; exit 2; }
	echo "$1"
}
