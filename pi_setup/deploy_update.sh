#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash deploy_update.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="/opt/pi-atc"
BACKUP_ROOT="/var/backups/pi-atc"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_ROOT}/${STAMP}"

UNIT_NAMES=(
    pi-atc.service
    pi-atc-setup.service
    pi-atc-heartbeat.service
    pi-atc-heartbeat.timer
    pi-atc-reboot.service
    pi-atc-reboot.timer
)

SETUP_SCRIPTS=(
    boot_check.sh
    start_ap.sh
    stop_ap.sh
    setup_portal.py
)

CALENDAR="$(awk -F= '/^OnCalendar=/ {print $2; exit}' "$SCRIPT_DIR/pi-atc-reboot.timer")"

if [ -z "$CALENDAR" ] || ! systemd-analyze calendar "$CALENDAR" >/dev/null; then
    echo "[deploy] Failed to parse OnCalendar=${CALENDAR:-<empty>} in pi-atc-reboot.timer"
    exit 1
fi

echo "[deploy] Backing up to ${BACKUP_DIR}..."
mkdir -p "$BACKUP_DIR/units"
if [ -d "$INSTALL_DIR" ]; then
    cp -a "$INSTALL_DIR" "$BACKUP_DIR/opt-pi-atc"
fi
for name in "${UNIT_NAMES[@]}"; do
    if [ -f "/lib/systemd/system/${name}" ]; then
        cp -a "/lib/systemd/system/${name}" "$BACKUP_DIR/units/"
    elif [ -f "/etc/systemd/system/${name}" ]; then
        cp -a "/etc/systemd/system/${name}" "$BACKUP_DIR/units/"
    fi
done

SETUP_CHANGED=false
for rel in "${SETUP_SCRIPTS[@]}"; do
    src="${SCRIPT_DIR}/${rel}"
    dst="${INSTALL_DIR}/pi_setup/${rel}"
    if [ ! -f "$dst" ] || ! cmp -s "$src" "$dst"; then
        SETUP_CHANGED=true
    fi
done

echo "[deploy] Installing code to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/pi_setup" /var/lib/pi-atc /var/log/journal /etc/pi-atc
for src in "$REPO_DIR"/*.py; do
    cp "$src" "$INSTALL_DIR/"
done

if [ -f "$REPO_DIR/VERSION" ]; then
    cp "$REPO_DIR/VERSION" "$INSTALL_DIR/VERSION"
elif git -C "$REPO_DIR" rev-parse HEAD >/dev/null 2>&1; then
    git -C "$REPO_DIR" rev-parse HEAD > "$INSTALL_DIR/VERSION"
else
    printf 'unknown\n' > "$INSTALL_DIR/VERSION"
fi

if [ -f "$REPO_DIR/packaging/pi-atc" ]; then
    install -m 755 "$REPO_DIR/packaging/pi-atc" /usr/bin/pi-atc
fi

if [ -f "$INSTALL_DIR/.env" ] && [ ! -f /etc/pi-atc/env ]; then
    python3 "$INSTALL_DIR/pi_atc_config.py" migrate || true
elif [ ! -f /etc/pi-atc/env ]; then
    cat > /etc/pi-atc/env <<EOF
ATIS_RELAY_URL=https://atisrelay.com
STATION_TOKEN=
STATION_TOKENS=
STATIONS=
PI_ID=
EOF
    chmod 600 /etc/pi-atc/env
fi

shopt -s nullglob
for src in "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.py "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/*.timer "$SCRIPT_DIR"/*.conf; do
    cp "$src" "$INSTALL_DIR/pi_setup/"
done
shopt -u nullglob
chmod +x "$INSTALL_DIR/pi_setup/"*.sh

echo "[deploy] Installing units and config..."
for name in "${UNIT_NAMES[@]}"; do
    install -m 644 "$INSTALL_DIR/pi_setup/${name}" "/lib/systemd/system/${name}"
done

mkdir -p /etc/systemd/journald.conf.d /etc/NetworkManager/conf.d
install -m 644 "$INSTALL_DIR/pi_setup/journald-pi-atc.conf" /etc/systemd/journald.conf.d/pi-atc.conf
install -m 644 "$INSTALL_DIR/pi_setup/wifi-powersave-off.conf" /etc/NetworkManager/conf.d/wifi-powersave-off.conf
systemctl restart systemd-journald 2>/dev/null || true
nmcli general reload 2>/dev/null || true

systemctl daemon-reload
systemctl enable pi-atc-setup.service
systemctl enable --now pi-atc-heartbeat.timer
systemctl enable --now pi-atc-reboot.timer
systemctl restart pi-atc.service

if $SETUP_CHANGED; then
    echo "[deploy] Setup scripts changed; restarting pi-atc-setup.service"
    echo "[deploy] Restarting the watchdog runs stop_ap.sh, which is harmless when not in AP mode."
    systemctl restart pi-atc-setup.service
else
    echo "[deploy] Setup scripts unchanged; leaving pi-atc-setup.service running"
fi

echo "[deploy] Done. Backup is at ${BACKUP_DIR}"
echo "[deploy] Left /etc/pi-atc/env and NetworkManager connection profiles untouched."
