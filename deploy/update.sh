#!/usr/bin/env bash
# =============================================================================
# deploy/update.sh — Update the bot files and restart the service (no-downtime)
#
# Run as root:   sudo bash deploy/update.sh
# =============================================================================
set -euo pipefail

APP_USER="mexcbot"
APP_DIR="/opt/mexcbot"
SERVICE_NAME="mexcbot"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

echo "==> Copying updated bot files..."
for f in mexc_fair_scanner.py requirements.txt; do
    if [[ -f "$REPO_DIR/$f" ]]; then
        cp "$REPO_DIR/$f" "$APP_DIR/$f"
    fi
done
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Upgrading Python dependencies..."
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> Restarting $SERVICE_NAME..."
systemctl restart "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager

echo ""
echo "==> Done. View logs with:  sudo journalctl -u $SERVICE_NAME -f"
