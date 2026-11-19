#!/usr/bin/env bash
set -euo pipefail

echo "[install] Factory image script. For a desktop Pi, install the .deb from GitHub Releases instead."

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash install.sh"
    exit 1
fi

INSTALL_DIR="/opt/pi-atc"
ENV_DIR="/etc/pi-atc"
ENV_FILE="${ENV_DIR}/env"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
UNIT_DIR="/lib/systemd/system"

echo "[install] Setting WiFi country code..."
raspi-config nonint do_wifi_country US
systemctl restart NetworkManager 2>/dev/null || true
sleep 3

STATE=$(nmcli -t -f STATE general 2>/dev/null || echo "unknown")
if [ "$STATE" != "connected" ]; then
    echo ""
    echo "No internet connection. Enter WiFi credentials to connect."
    echo ""
    read -rp "WiFi network name: " WIFI_SSID
    read -rsp "WiFi password: " WIFI_PSK
    echo ""

    rfkill unblock wifi 2>/dev/null || true
    ip link set wlan0 up 2>/dev/null || true
    sleep 2
    echo "[install] Scanning for networks..."
    nmcli device wifi rescan ifname wlan0 2>/dev/null || true
    sleep 5
    echo "[install] Connecting to ${WIFI_SSID}..."
    nmcli device wifi connect "$WIFI_SSID" password "$WIFI_PSK" ifname wlan0
    sleep 3
fi

echo "[install] Installing system packages..."
apt-get update --allow-releaseinfo-change -y -qq
apt-get install -y -qq \
    hostapd \
    dnsmasq \
    python3-flask \
    python3-dotenv \
    python3-numpy \
    python3-requests \
    rtl-sdr \
    ffmpeg \
    iptables

systemctl stop hostapd 2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true
systemctl unmask hostapd 2>/dev/null || true

echo "[install] Copying project to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR" /var/lib/pi-atc /var/log/journal "$ENV_DIR"
for src in "$REPO_DIR"/*.py; do
    cp "$src" "$INSTALL_DIR/"
done
rm -rf "$INSTALL_DIR/pi_setup"
cp -r "$SCRIPT_DIR" "$INSTALL_DIR/pi_setup"
chmod +x "$INSTALL_DIR/pi_setup/"*.sh

if [ -f "$REPO_DIR/VERSION" ]; then
    cp "$REPO_DIR/VERSION" "$INSTALL_DIR/VERSION"
elif git -C "$REPO_DIR" rev-parse HEAD >/dev/null 2>&1; then
    git -C "$REPO_DIR" rev-parse HEAD > "$INSTALL_DIR/VERSION"
else
    printf 'unknown\n' > "$INSTALL_DIR/VERSION"
fi

if [ -f "$REPO_DIR/packaging/pi-atc" ]; then
    install -m 755 "$REPO_DIR/packaging/pi-atc" /usr/bin/pi-atc
else
    cat > /usr/bin/pi-atc <<'EOF'
#!/usr/bin/python3
import sys
sys.path.insert(0, "/opt/pi-atc")
from pi_atc_cli import main
sys.exit(main())
EOF
    chmod 755 /usr/bin/pi-atc
fi

if [ -f "$REPO_DIR/.env" ] && [ ! -f "$ENV_FILE" ]; then
    mkdir -p "$ENV_DIR"
    cp "$REPO_DIR/.env" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
elif [ -f "$INSTALL_DIR/.env" ] && [ ! -f "$ENV_FILE" ]; then
    python3 "$INSTALL_DIR/pi_atc_config.py" migrate || true
elif [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
ATIS_RELAY_URL=https://atisrelay.com
STATION_TOKEN=
STATION_TOKENS=
STATIONS=
PI_ID=
EOF
    chmod 600 "$ENV_FILE"
    echo "[install] Created skeleton ${ENV_FILE}; set a station key with: sudo pi-atc set-key"
fi

echo "[install] Removing install-time WiFi connection..."
nmcli --fields NAME connection show | tail -n +2 | while read -r NAME; do
    NAME=$(echo "$NAME" | xargs)
    if [ -n "$NAME" ]; then
        nmcli connection delete "$NAME" 2>/dev/null || true
    fi
done

echo "[install] Installing systemd services..."
install -m 644 "$INSTALL_DIR/pi_setup/pi-atc-setup.service" "${UNIT_DIR}/pi-atc-setup.service"
install -m 644 "$INSTALL_DIR/pi_setup/pi-atc.service" "${UNIT_DIR}/pi-atc.service"
install -m 644 "$INSTALL_DIR/pi_setup/pi-atc-heartbeat.service" "${UNIT_DIR}/pi-atc-heartbeat.service"
install -m 644 "$INSTALL_DIR/pi_setup/pi-atc-heartbeat.timer" "${UNIT_DIR}/pi-atc-heartbeat.timer"
install -m 644 "$INSTALL_DIR/pi_setup/pi-atc-reboot.service" "${UNIT_DIR}/pi-atc-reboot.service"
install -m 644 "$INSTALL_DIR/pi_setup/pi-atc-reboot.timer" "${UNIT_DIR}/pi-atc-reboot.timer"

echo "[install] Installing journal and NetworkManager config..."
mkdir -p /etc/systemd/journald.conf.d /etc/NetworkManager/conf.d
install -m 644 "$INSTALL_DIR/pi_setup/journald-pi-atc.conf" /etc/systemd/journald.conf.d/pi-atc.conf
install -m 644 "$INSTALL_DIR/pi_setup/wifi-powersave-off.conf" /etc/NetworkManager/conf.d/wifi-powersave-off.conf
systemctl restart systemd-journald 2>/dev/null || true
nmcli general reload 2>/dev/null || true

systemctl daemon-reload
systemctl enable pi-atc.service
systemctl enable pi-atc-setup.service
systemctl enable pi-atc-heartbeat.timer
systemctl enable pi-atc-reboot.timer

echo "[install] Done. On next boot the Pi will:"
echo "  1. Wait up to 2 min for WiFi"
echo "  2. If no WiFi: start AP 'ATIS Relay Setup' with captive portal"
echo "  3. If WiFi OK: start audio uploader"
echo "  4. If WiFi drops for 2 min: switch to AP mode, retry known WiFi every 5 min (45s connect wait)"
echo "  5. Check in to ATIS Relay every 5 min and reboot nightly at 03:00 America/Los_Angeles"
echo ""
echo "Code lives in ${INSTALL_DIR}. Config is ${ENV_FILE}. State is /var/lib/pi-atc."
echo "Set a station key with: sudo pi-atc set-key"
echo "This script is for baking factory images. End users should install the .deb package."
