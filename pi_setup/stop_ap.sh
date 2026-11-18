#!/usr/bin/env bash
set -euo pipefail

IFACE="wlan0"

if [ -f /tmp/pi-atc-dnsmasq.pid ]; then
    kill "$(cat /tmp/pi-atc-dnsmasq.pid)" 2>/dev/null || true
    rm -f /tmp/pi-atc-dnsmasq.pid
fi

killall hostapd 2>/dev/null || true

iptables -t nat -F PREROUTING 2>/dev/null || true
iptables -t nat -F POSTROUTING 2>/dev/null || true

ip addr flush dev "$IFACE" 2>/dev/null || true

nmcli device set "$IFACE" managed yes 2>/dev/null || true

rm -f /tmp/pi-atc-hostapd.conf /tmp/pi-atc-dnsmasq.conf

echo "[ap] Access point stopped"
