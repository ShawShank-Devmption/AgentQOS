#!/usr/bin/env bash
# Create a WireGuard profile for one device and print it as a QR code (docs/demo_tasks.md I2/I5).
# usage: add_device.sh NAME   (NAME: lowercase letters, digits, dashes; max 32 characters)
#
# One profile per device: two devices sharing a key keep stealing each other's endpoint.
# The client profile holds the device's private key; it stays root-only on the VM.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/gateway.env"

fail() { echo "add_device: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail "run as root"
name=${1:-}
[[ $name =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || fail "usage: add_device.sh NAME ([a-z0-9-], max 32)"
peer_file="$AAN_STATE_DIR/peers/$name.conf"
client_file="$AAN_STATE_DIR/clients/$name.conf"
[ ! -e "$peer_file" ] || fail "device '$name' already exists; revoke it first"

metadata=http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0
endpoint=${AAN_ENDPOINT:-$(curl -fsS -H 'Metadata-Flavor: Google' "$metadata/external-ip")}
[ -n "$endpoint" ] || fail "no external IP; set AAN_ENDPOINT"

prefix=${AAN_CLIENT_NET%.*}
shopt -s nullglob
peers=("$AAN_STATE_DIR"/peers/*.conf)
used=
if [ ${#peers[@]} -gt 0 ]; then
	used=$(awk '/^AllowedIPs/ {sub("/32", "", $3); print $3}' "${peers[@]}")
fi
address=
for host in $(seq "$AAN_CLIENT_FIRST" "$AAN_CLIENT_LAST"); do
	if ! grep -qxF "$prefix.$host" <<<"$used"; then
		address="$prefix.$host"
		break
	fi
done
[ -n "$address" ] || fail "no free client address in $AAN_CLIENT_NET"

private=$(wg genkey)
public=$(wg pubkey <<<"$private")
umask 077
cat >"$peer_file" <<EOF
# device $name, added $(date -u +%Y-%m-%dT%H:%M:%SZ)
[Peer]
PublicKey = $public
AllowedIPs = $address/32
EOF
# AllowedIPs includes ::/0 so IPv6 cannot leak around the gateway; the gateway is IPv4-only,
# so applications fall back to IPv4 through the tunnel.
cat >"$client_file" <<EOF
[Interface]
PrivateKey = $private
Address = $address/32
DNS = $AAN_CLIENT_DNS
MTU = $AAN_MTU

[Peer]
PublicKey = $(cat "$AAN_STATE_DIR/server.pub")
Endpoint = $endpoint:$AAN_WG_PORT
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
EOF
qrencode -t png -o "$AAN_STATE_DIR/clients/$name.png" <"$client_file"

if [ -e "/run/netns/$AAN_NETNS" ]; then
	ip netns exec "$AAN_NETNS" wg set "$AAN_WG_IF" peer "$public" allowed-ips "$address/32"
fi
echo "add_device: $name -> $address (profile $client_file)"
qrencode -t ansiutf8 <"$client_file"
