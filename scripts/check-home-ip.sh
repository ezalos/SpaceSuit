#!/bin/sh
# ABOUTME: Fetches the current public IP and appends it (append-only) to the identity home-IP block list.
# ABOUTME: Run on a timer; safe anywhere (no-op if curl fails or the block list is absent).
. "$(dirname "$0")/lib-protect-ip.sh"
ip=$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null) || exit 0
register_protected_ip "$ip"
