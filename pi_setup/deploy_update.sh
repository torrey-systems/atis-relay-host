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
    if [ -f "/etc/systemd/system/${name}" ]; then
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
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/pi_setup" /var/lib/pi-atc /var/log/journal
cp "$REPO_DIR/audio_uploader.py" "$INSTALL_DIR/"
cp "$REPO_DIR/heartbeat.py" "$INSTALL_DIR/"
cp "$REPO_DIR/pi_atc_state.py" "$INSTALL_DIR/"

if git -C "$REPO_DIR" rev-parse HEAD >/dev/null 2>&1; then
    git -C "$REPO_DIR" rev-parse HEAD > "$INSTALL_DIR/VERSION"
else
    printf 'unknown\n' > "$INSTALL_DIR/VERSION"
fi

shopt -s nullglob
for src in "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.py "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/*.timer "$SCRIPT_DIR"/*.conf; do
    cp "$src" "$INSTALL_DIR/pi_setup/"
done
shopt -u nullglob
chmod +x "$INSTALL_DIR/pi_setup/"*.sh

echo "[deploy] Installing units and config..."
for name in "${UNIT_NAMES[@]}"; do
    install -m 644 "$INSTALL_DIR/pi_setup/${name}" "/etc/systemd/system/${name}"
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
echo "[deploy] Left /opt/pi-atc/.env and NetworkManager connection profiles untouched."
