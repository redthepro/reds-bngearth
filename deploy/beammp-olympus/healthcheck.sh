#!/bin/sh
set -eu
# Read socket state without connecting a fake player or generating server log noise.
awk '$2 ~ /:785E$/ && $4 == "0A" { found=1 } END { exit !found }' /proc/net/tcp /proc/net/tcp6
awk '$2 ~ /:785E$/ && $4 == "07" { found=1 } END { exit !found }' /proc/net/udp /proc/net/udp6
