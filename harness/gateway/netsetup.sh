#!/usr/bin/env bash
# Bring up the live gateway in bypass mode (docs/demo_tasks.md I2/I3).
#
# client ═ WireGuard ═ wg0 [netns aan-lan] → gw-lan ═ gw-lan-sw (switch LAN port)
#                       gw-wan-sw (switch WAN port) ═ gw-wan [root] → MASQUERADE → uplink
#
# Every decrypted client packet must cross gw-lan-sw/gw-wan-sw, so the switch sees all of it.
# Bypass mode bridges those two ports directly; the switch launcher (G2) turns bypass off before
# attaching BMv2 and back on when the switch dies. Idempotent: tears down any previous state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/gateway.env"

fail() { echo "netsetup: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail "run as root"
[ -s "$AAN_STATE_DIR/server.key" ] || fail "missing $AAN_STATE_DIR/server.key; run install.sh"
[ "$(iptables -S FORWARD | sed -n 1p)" = '-P FORWARD ACCEPT' ] \
	|| fail "root FORWARD policy is not ACCEPT; add explicit forwarding rules first"
uplink=$(ip -4 route show default | awk '{print $5; exit}')
[ -n "$uplink" ] || fail "no IPv4 default route"

"$HERE/teardown.sh"

# Offloads off: BMv2 reads frames with pcap and must see real MTU-sized frames with complete
# checksums, not GSO super-frames. IPv6 off keeps router solicitations out of the pipeline.
prepare_link() {
	local netns=$1 link=$2
	local run=()
	[ -z "$netns" ] || run=(ip netns exec "$netns")
	"${run[@]}" sysctl -qw "net.ipv6.conf.$link.disable_ipv6=1"
	"${run[@]}" ethtool -K "$link" rx off tx off tso off gso off gro off >/dev/null
	"${run[@]}" ip link set "$link" up
}

sysctl -qw net.ipv4.ip_forward=1
ip netns add "$AAN_NETNS"
ip -n "$AAN_NETNS" link set lo up
ip netns exec "$AAN_NETNS" sysctl -qw net.ipv4.ip_forward=1

ip link add "$AAN_LAN_IF" type veth peer name "$AAN_LAN_SW"
ip link set "$AAN_LAN_IF" netns "$AAN_NETNS"
ip link add "$AAN_WAN_IF" type veth peer name "$AAN_WAN_SW"
prepare_link "$AAN_NETNS" "$AAN_LAN_IF"
prepare_link "" "$AAN_LAN_SW"
prepare_link "" "$AAN_WAN_IF"
prepare_link "" "$AAN_WAN_SW"
ip -n "$AAN_NETNS" addr add "$AAN_LAN_ADDR" dev "$AAN_LAN_IF"
ip -n "$AAN_NETNS" route add default via "${AAN_WAN_ADDR%/*}" dev "$AAN_LAN_IF"
ip addr add "$AAN_WAN_ADDR" dev "$AAN_WAN_IF"
ip route add "$AAN_CLIENT_NET" via "${AAN_LAN_ADDR%/*}" dev "$AAN_WAN_IF"

ip link add "$AAN_BYPASS_BR" type bridge stp_state 0 forward_delay 0
sysctl -qw "net.ipv6.conf.$AAN_BYPASS_BR.disable_ipv6=1"
ip link set "$AAN_BYPASS_BR" up
"$HERE/bypass.sh" on

# The UDP socket stays in the namespace where wg0 is created (root, on the uplink); only the
# decrypted interface moves into the client netns.
ip link add "$AAN_WG_IF" type wireguard
ip link set "$AAN_WG_IF" netns "$AAN_NETNS"
conf=$(umask 077 && mktemp)
trap 'rm -f "$conf"' EXIT
{
	printf '[Interface]\nPrivateKey = %s\nListenPort = %s\n\n' \
		"$(cat "$AAN_STATE_DIR/server.key")" "$AAN_WG_PORT"
	shopt -s nullglob
	for peer in "$AAN_STATE_DIR"/peers/*.conf; do
		cat "$peer"
		echo
	done
} >"$conf"
ip netns exec "$AAN_NETNS" wg setconf "$AAN_WG_IF" "$conf"
ip -n "$AAN_NETNS" addr add "$AAN_WG_ADDR" dev "$AAN_WG_IF"
ip -n "$AAN_NETNS" link set "$AAN_WG_IF" mtu "$AAN_MTU" up

iptables -t nat -A POSTROUTING -s "$AAN_CLIENT_NET" -o "$uplink" \
	-m comment --comment aan-gateway -j MASQUERADE
ip netns exec "$AAN_NETNS" iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN \
	-j TCPMSS --clamp-mss-to-pmtu

peers=$(ip netns exec "$AAN_NETNS" wg show "$AAN_WG_IF" peers | wc -l)
echo "netsetup: gateway up, bypass on, uplink=$uplink, peers=$peers"
