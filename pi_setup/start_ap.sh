#!/usr/bin/env bash
set -euo pipefail

IFACE="wlan0"
AP_IP="192.168.4.1"
AP_SSID="ATIS Relay Setup"
AP_PSK="aviation"
if [ -f /opt/pi-atc/.env ]; then
    AP_OVERRIDE=$(grep '^AP_PASSWORD=' /opt/pi-atc/.env 2>/dev/null | cut -d= -f2- || true)
    [ -n "$AP_OVERRIDE" ] && AP_PSK="$AP_OVERRIDE"
fi
HOSTAPD_CONF="/tmp/pi-atc-hostapd.conf"
DNSMASQ_CONF="/tmp/pi-atc-dnsmasq.conf"

nmcli device set "$IFACE" managed no 2>/dev/null || true
nmcli radio wifi on

ip link set "$IFACE" down
ip addr flush dev "$IFACE"
ip addr add "${AP_IP}/24" dev "$IFACE"
ip link set "$IFACE" up

cat > "$HOSTAPD_CONF" <<EOF
interface=${IFACE}
driver=nl80211
ssid=${AP_SSID}
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=${AP_PSK}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

cat > "$DNSMASQ_CONF" <<EOF
interface=${IFACE}
bind-interfaces
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,24h
address=/#/${AP_IP}
EOF

hostapd -B "$HOSTAPD_CONF"
dnsmasq -C "$DNSMASQ_CONF" --pid-file=/tmp/pi-atc-dnsmasq.pid

iptables -t nat -A PREROUTING -i "$IFACE" -p tcp --dport 80 \
  -j DNAT --to-destination "${AP_IP}:80"
iptables -t nat -A POSTROUTING -j MASQUERADE

echo "[ap] Access point ${AP_SSID} running on ${AP_IP}"
