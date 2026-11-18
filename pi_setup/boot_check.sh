#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

WIFI_TIMEOUT=120
WIFI_POLL=10
RETRY_INTERVAL=300
CONNECT_WAIT=45
RESCAN_WAIT=5
STATE_DIR="${PI_ATC_STATE_DIR:-/var/lib/pi-atc}"

AP_ACTIVE=false
PORTAL_PID=""

ensure_state_dir() {
    mkdir -p "$STATE_DIR"
}

set_mode() {
    ensure_state_dir
    printf '%s\n' "$1" > "$STATE_DIR/mode"
}

record_setup_enter() {
    ensure_state_dir
    if [ ! -s "$STATE_DIR/setup_current" ]; then
        date -u +%Y-%m-%dT%H:%M:%SZ > "$STATE_DIR/setup_current"
    fi
    set_mode setup
}

record_setup_leave() {
    ensure_state_dir
    if [ -s "$STATE_DIR/setup_current" ]; then
        START="$(tr -d '[:space:]' < "$STATE_DIR/setup_current")"
        END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '{"start":"%s","end":"%s"}\n' "$START" "$END" >> "$STATE_DIR/setup_episodes.jsonl"
        rm -f "$STATE_DIR/setup_current"
    fi
    set_mode online
}

is_wifi_connected() {
    STATE=$(nmcli -t -f STATE general 2>/dev/null || echo "unknown")
    [ "$STATE" = "connected" ]
}

start_setup_mode() {
    if $AP_ACTIVE; then
        return
    fi

    echo "[watchdog] Entering setup mode"
    systemctl stop pi-atc.service 2>/dev/null || true
    "$SCRIPT_DIR/start_ap.sh"
    python3 "$SCRIPT_DIR/setup_portal.py" &
    PORTAL_PID=$!
    AP_ACTIVE=true
    record_setup_enter
}

stop_setup_mode() {
    if ! $AP_ACTIVE; then
        return
    fi

    echo "[watchdog] Leaving setup mode"

    if [ -n "$PORTAL_PID" ]; then
        kill "$PORTAL_PID" 2>/dev/null || true
        wait "$PORTAL_PID" 2>/dev/null || true
        PORTAL_PID=""
    fi

    "$SCRIPT_DIR/stop_ap.sh"
    AP_ACTIVE=false
    record_setup_leave
}

try_reconnect() {
    echo "[watchdog] Attempting reconnect to known WiFi..."
    stop_setup_mode
    nmcli device wifi rescan ifname wlan0 2>/dev/null || true
    sleep "$RESCAN_WAIT"
    nmcli device connect wlan0 2>/dev/null || true
    sleep "$CONNECT_WAIT"

    if is_wifi_connected; then
        echo "[watchdog] Reconnected"
        return 0
    fi

    echo "[watchdog] Reconnect failed, returning to setup mode"
    start_setup_mode
    return 1
}

cleanup() {
    stop_setup_mode
    exit 0
}
trap cleanup SIGTERM SIGINT

SAVED=$(nmcli -t -f NAME connection show 2>/dev/null | grep -c . || echo "0")
if [ "$SAVED" -eq 0 ]; then
    echo "[watchdog] No saved WiFi connections, entering setup mode"
    start_setup_mode
else
    echo "[watchdog] Waiting up to ${WIFI_TIMEOUT}s for WiFi..."
    ELAPSED=0
    while [ $ELAPSED -lt $WIFI_TIMEOUT ]; do
        if is_wifi_connected; then
            echo "[watchdog] WiFi connected"
            set_mode online
            systemctl start pi-atc.service
            break
        fi
        sleep $WIFI_POLL
        ELAPSED=$((ELAPSED + WIFI_POLL))
    done

    if ! is_wifi_connected; then
        start_setup_mode
    fi
fi

DISCONNECT_START=0
LAST_RETRY=0

while true; do
    sleep $WIFI_POLL

    if $AP_ACTIVE; then
        NOW=$(date +%s)
        if [ "$LAST_RETRY" -eq 0 ] || [ $((NOW - LAST_RETRY)) -ge $RETRY_INTERVAL ]; then
            LAST_RETRY=$NOW
            if try_reconnect; then
                DISCONNECT_START=0
                systemctl start pi-atc.service
            fi
        fi
    else
        if is_wifi_connected; then
            DISCONNECT_START=0
        else
            NOW=$(date +%s)
            if [ "$DISCONNECT_START" -eq 0 ]; then
                DISCONNECT_START=$NOW
                echo "[watchdog] WiFi lost, waiting ${WIFI_TIMEOUT}s before setup mode"
            elif [ $((NOW - DISCONNECT_START)) -ge $WIFI_TIMEOUT ]; then
                start_setup_mode
                LAST_RETRY=$(date +%s)
            fi
        fi
    fi
done
