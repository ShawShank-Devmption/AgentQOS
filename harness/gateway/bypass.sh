#!/usr/bin/env bash
# Bridge the switch's LAN and WAN ports directly (on) or hand them to the switch (off).
#
# Exactly one of {bypass bridge, BMv2} may own gw-lan-sw/gw-wan-sw at a time: with both, every
# frame is forwarded twice. The switch launcher (G2) runs `bypass.sh off` before starting
# simple_switch and `bypass.sh on` as soon as it stops; with bypass off and no switch running,
# clients have no connectivity.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/gateway.env"

[ "$(id -u)" -eq 0 ] || { echo "bypass: run as root" >&2; exit 1; }

state() {
	if ip -o link show "$AAN_LAN_SW" | grep -q "master $AAN_BYPASS_BR"; then
		echo on
	else
		echo off
	fi
}

case "${1:-}" in
on)
	for port in "$AAN_LAN_SW" "$AAN_WAN_SW"; do ip link set "$port" master "$AAN_BYPASS_BR"; done
	;;
off)
	for port in "$AAN_LAN_SW" "$AAN_WAN_SW"; do ip link set "$port" nomaster; done
	;;
status) ;;
*)
	echo "usage: bypass.sh on|off|status" >&2
	exit 2
	;;
esac
echo "bypass: $(state)"
