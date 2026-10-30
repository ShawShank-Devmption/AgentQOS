#!/usr/bin/env bash
# Remove a device's WireGuard access and delete its profile. usage: revoke_device.sh NAME
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/gateway.env"

fail() { echo "revoke_device: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail "run as root"
name=${1:-}
[[ $name =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || fail "usage: revoke_device.sh NAME"
peer_file="$AAN_STATE_DIR/peers/$name.conf"
[ -e "$peer_file" ] || fail "no device '$name'"

public=$(awk '/^PublicKey/ {print $3}' "$peer_file")
if [ -e "/run/netns/$AAN_NETNS" ]; then
	ip netns exec "$AAN_NETNS" wg set "$AAN_WG_IF" peer "$public" remove
fi
rm -f "$peer_file" "$AAN_STATE_DIR/clients/$name.conf" "$AAN_STATE_DIR/clients/$name.png"
echo "revoke_device: $name removed"
