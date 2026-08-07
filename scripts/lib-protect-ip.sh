#!/bin/sh
# ABOUTME: Shared append-only home-IP register for the identity content-scrub block list.
# ABOUTME: Sourced by scripts/check-home-ip.sh (cloudflare-dns/dns.sh to be consolidated onto this later).
# IPv4 literals only (matches the original cloudflare-dns behavior); hostnames/IPv6 skipped.
register_protected_ip() {
	ip="$1"; list="${2:-$HOME/.config/git-identity/forbidden/home-ip.txt}"
	[ -n "$ip" ] || return 0
	case "$ip" in ""|*[!0-9.]*) return 0 ;; esac
	[ -f "$list" ] || return 0
	if ! grep -qxF "$ip" "$list"; then
		printf '\n# home IP auto-added (%s)\n%s\n' "$(date +%Y-%m-%d)" "$ip" >> "$list"
		echo "  [home-ip-guard] registered a new home IP" >&2
	fi
}
