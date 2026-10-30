#!/usr/bin/env bash
# Install the live gateway on the VM: packages, server key, scripts, boot-time systemd unit.
# Re-run after syncing changed scripts. usage (on the VM): sudo harness/gateway/install.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/gateway.env"

[ "$(id -u)" -eq 0 ] || { echo "install: run as root" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wireguard-tools qrencode >/dev/null

install -d -m 700 "$AAN_STATE_DIR" "$AAN_STATE_DIR/peers" "$AAN_STATE_DIR/clients"
if [ ! -s "$AAN_STATE_DIR/server.key" ]; then
	(umask 077 && wg genkey >"$AAN_STATE_DIR/server.key")
fi
wg pubkey <"$AAN_STATE_DIR/server.key" >"$AAN_STATE_DIR/server.pub"

install -d -m 755 "$AAN_LIB_DIR"
install -m 644 "$HERE/gateway.env" "$AAN_LIB_DIR/"
for script in netsetup teardown bypass add_device revoke_device selftest; do
	install -m 755 "$HERE/$script.sh" "$AAN_LIB_DIR/"
done
install -m 644 "$HERE/aan-gateway.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet aan-gateway.service
systemctl restart aan-gateway.service
systemctl --no-pager --lines=3 status aan-gateway.service
