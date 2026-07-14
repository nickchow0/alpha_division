#!/usr/bin/env bash
# uninstall.sh — Stop and remove the AlphaDivision Watchdog systemd service.
#
# Usage (run as root or with sudo):
#   sudo bash /opt/alphadivision/services/watchdog/uninstall.sh

set -euo pipefail

SERVICE_NAME="alphadivision-watchdog"
SYSTEMD_DIR="/etc/systemd/system"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || error "Run this script as root: sudo bash $0"

if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Stopping $SERVICE_NAME..."
    systemctl stop "$SERVICE_NAME"
else
    info "$SERVICE_NAME is not running — skipping stop."
fi

if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    info "Disabling $SERVICE_NAME..."
    systemctl disable "$SERVICE_NAME"
else
    info "$SERVICE_NAME is not enabled — skipping disable."
fi

if [[ -f "$SYSTEMD_DIR/$SERVICE_NAME.service" ]]; then
    info "Removing service file..."
    rm "$SYSTEMD_DIR/$SERVICE_NAME.service"
    systemctl daemon-reload
else
    info "Service file already absent — skipping."
fi

info "Watchdog uninstalled."
