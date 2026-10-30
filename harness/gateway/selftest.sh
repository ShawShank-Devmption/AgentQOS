#!/usr/bin/env bash
# End-to-end gateway check without a phone: a throwaway WireGuard client in its own netns on the
# VM tunnels to wg0, fetches its public IP through the gateway, and confirms the traffic crossed
# the switch's LAN port. Works in bypass mode and with BMv2 attached (G2).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/gateway.env"

NS=aan-selftest
OUTER=aan-st1
INNER=aan-st0
fail() { echo "selftest: FAIL: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail "run as root"
[ -e "/run/netns/$AAN_NETNS" ] || fail "gateway is not up (netsetup.sh)"
[ ! -e "/run/netns/$NS" ] || fail "$NS exists; another selftest running?"

workdir=$(umask 077 && mktemp -d)
public=
cleanup() {
	if [ -n "$public" ]; then
		ip netns exec "$AAN_NETNS" wg set "$AAN_WG_IF" peer "$public" remove
	fi
	if [ -e "/run/netns/$NS" ]; then ip netns del "$NS"; fi
	if [ -e "/sys/class/net/$OUTER" ]; then ip link del "$OUTER"; fi
	rm -rf "$workdir"
}
trap cleanup EXIT

address="${AAN_CLIENT_NET%.*}.$AAN_SELFTEST_HOST"
(umask 077 && wg genkey >"$workdir/key")
public=$(wg pubkey <"$workdir/key")
ip netns exec "$AAN_NETNS" wg set "$AAN_WG_IF" peer "$public" allowed-ips "$address/32"

ip netns add "$NS"
ip -n "$NS" link set lo up
ip link add "$OUTER" type veth peer name "$INNER"
ip link set "$INNER" netns "$NS"
ip addr add 10.44.0.1/30 dev "$OUTER"
ip link set "$OUTER" up
ip -n "$NS" addr add 10.44.0.2/30 dev "$INNER"
ip -n "$NS" link set "$INNER" up
ip netns exec "$NS" ip link add wgt type wireguard
ip netns exec "$NS" wg set wgt private-key "$workdir/key" \
	peer "$(cat "$AAN_STATE_DIR/server.pub")" endpoint "10.44.0.1:$AAN_WG_PORT" allowed-ips 0.0.0.0/0
ip -n "$NS" addr add "$address/32" dev wgt
ip -n "$NS" link set wgt mtu "$AAN_MTU" up
ip -n "$NS" route add default dev wgt

metadata=http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0
expected=$(curl -fsS -H 'Metadata-Flavor: Google' "$metadata/external-ip")
before=$(cat "/sys/class/net/$AAN_LAN_SW/statistics/rx_packets")
trace=$(ip netns exec "$NS" curl -fsS --max-time 15 https://1.1.1.1/cdn-cgi/trace) \
	|| fail "no HTTPS through the tunnel"
seen=$(sed -n 's/^ip=//p' <<<"$trace")
rtt=$(ip netns exec "$NS" ping -c 5 -q 1.1.1.1 | sed -n 's/^rtt .* = //p')
after=$(cat "/sys/class/net/$AAN_LAN_SW/statistics/rx_packets")

[ "$seen" = "$expected" ] || fail "egress IP $seen, expected $expected"
[ $((after - before)) -gt 0 ] || fail "no packets crossed $AAN_LAN_SW"
echo "selftest: PASS egress_ip=$seen lan_port_packets=$((after - before)) rtt_1.1.1.1=$rtt"
