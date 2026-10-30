#!/usr/bin/env bash
# Remove everything netsetup.sh creates. Safe to run when nothing is set up.
# Keys and device profiles under $AAN_STATE_DIR are kept.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/gateway.env"

[ "$(id -u)" -eq 0 ] || { echo "teardown: run as root" >&2; exit 1; }

# Delete wg0 explicitly first: netns removal destroys devices asynchronously, and a lingering
# wg0 would still hold UDP $AAN_WG_PORT when netsetup.sh recreates it. Deleting one veth end
# destroys its peer, so the links go before the netns for the same reason.
if [ -e "/run/netns/$AAN_NETNS" ]; then
	if ip -n "$AAN_NETNS" link show "$AAN_WG_IF" >/dev/null 2>&1; then
		ip -n "$AAN_NETNS" link del "$AAN_WG_IF"
	fi
fi
for link in "$AAN_LAN_SW" "$AAN_WAN_IF" "$AAN_BYPASS_BR"; do
	if [ -e "/sys/class/net/$link" ]; then
		ip link del "$link"
	fi
done
if [ -e "/run/netns/$AAN_NETNS" ]; then
	ip netns del "$AAN_NETNS"
fi
iptables -t nat -S POSTROUTING | awk '/--comment aan-gateway/ {sub(/^-A /, "-D "); print}' \
	| while read -r -a rule; do iptables -t nat "${rule[@]}"; done
