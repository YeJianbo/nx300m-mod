#!/bin/sh
# Invoke only under `unshare -n`; no external network or photo transfers.
set -eu
if [ "$#" -ne 3 ]; then
    echo 'usage: discover_isolated.sh FILTER QEMU ARM_BINARY' >&2
    exit 2
fi
ip link set lo up
ip link add qs0 type veth peer name qs1
ip address add 192.0.2.1/24 dev qs0
ip address add 192.0.2.2/24 dev qs1
ip link set qs0 up
ip link set qs1 up
export RUST_LOG=info
exec "$1" "$2" -cpu cortex-a9 -strace "$3" discover 2
