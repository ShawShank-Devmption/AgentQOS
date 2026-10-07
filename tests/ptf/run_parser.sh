#!/bin/sh
set -eu

ip link add ptf1 type veth peer name sw1
ip link add ptf2 type veth peer name sw2
ip link set ptf1 up
ip link set ptf2 up
ip link set sw1 up
ip link set sw2 up

p4c-bm2-ss --std p4-16 -DPARSER_PROBE=1 -o /tmp/l2fwd.json p4src/l2fwd.p4
simple_switch --interface 1@sw1 --interface 2@sw2 --thrift-port 9090 /tmp/l2fwd.json \
    >/tmp/agentqos-simple-switch.log 2>&1 &
switch_pid=$!
trap 'kill "$switch_pid" 2>/dev/null || true' EXIT

ready=0
attempt=0
while [ "$attempt" -lt 10 ]; do
    if printf 'table_add tbl_l2_forward set_egress_port 0x000000000002 => 2\n' \
        | simple_switch_CLI --thrift-port 9090 >/tmp/agentqos-cli.log 2>&1; then
        ready=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    sed -n '1,100p' /tmp/agentqos-simple-switch.log
    exit 1
fi

ptf --test-dir tests/ptf --log-file /tmp/agentqos-ptf.log \
    --interface 0@ptf1 --interface 1@ptf2 \
    test_parser.ParserForwardingTest test_parser.ParserMetadataTest
