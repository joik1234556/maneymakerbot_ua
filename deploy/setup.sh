#!/usr/bin/env bash
# =============================================================================
# deploy/setup.sh — Idempotent setup script for mexcbot on Ubuntu/Debian
#
# Run as root:   sudo bash deploy/setup.sh
#
# What it does:
#   1. Creates a dedicated system user "mexcbot" (no login shell)
#   2. Creates /opt/mexcbot and copies the bot files into it
#   3. Creates a Python virtualenv and installs dependencies
#   4. Creates a .env file placeholder if it does not exist yet
#   5. Installs and enables the systemd service
# =============================================================================
set -euo pipefail

APP_USER="mexcbot"
APP_DIR="/opt/mexcbot"
SERVICE_NAME="mexcbot"
SERVICE_SRC="$(cd "$(dirname "$0")" && pwd)/mexcbot.service"
# Resolve the repo root (one directory above deploy/)
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Checking root privileges..."
if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

# ── 1. Create the dedicated system user ──────────────────────────────────────
if id "$APP_USER" &>/dev/null; then
    echo "==> User '$APP_USER' already exists, skipping creation."
else
    echo "==> Creating system user '$APP_USER'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi

# ── 2. Create app directory and copy bot files ───────────────────────────────
echo "==> Creating app directory $APP_DIR..."
mkdir -p "$APP_DIR"

echo "==> Copying bot files..."
# Copy only the bot source and requirements; never overwrite .env or bot_data.json
for f in mexc_fair_scanner.py requirements.txt; do
    if [[ -f "$REPO_DIR/$f" ]]; then
        cp "$REPO_DIR/$f" "$APP_DIR/$f"
    fi
done

# ── 3. Create / upgrade Python virtualenv ────────────────────────────────────
echo "==> Setting up Python virtualenv..."
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ── 4. Create .env placeholder (only if it does not exist yet) ───────────────
ENV_FILE="$APP_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "==> Creating .env placeholder at $ENV_FILE"
    cat > "$ENV_FILE" <<'EOF'
# Telegram bot token — replace with your real token, then restart the service:
#   sudo systemctl restart mexcbot
BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE

# Base URL of the subscription API (required, no default in the bot code).
# Bot and site are on the same server, so use the loopback address.
# Adjust the port to match your web server (e.g. 8000 for gunicorn, 80 for nginx).
API_BASE_URL=http://127.0.0.1:8000

# Logging level: INFO (default) or DEBUG (very verbose — shows full request/response)
LOG_LEVEL=INFO

# HTTP timeout in seconds for subscription API requests (default: 5)
REQUEST_TIMEOUT=5
EOF
    chmod 600 "$ENV_FILE"
    echo "    *** IMPORTANT: Edit $ENV_FILE and set BOT_TOKEN before starting the bot! ***"
else
    echo "==> .env already exists, not overwriting."
fi

# ── 5. Fix file ownership ─────────────────────────────────────────────────────
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
# Ensure the env file is readable only by the bot user and root
chmod 600 "$ENV_FILE"

# ── 6. Install and enable systemd service ────────────────────────────────────
echo "==> Installing systemd service..."
cp "$SERVICE_SRC" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo ""
echo "============================================================"
echo " Setup complete!"
echo ""
echo " Next steps:"
echo "   1. Edit the bot token:  sudo nano $ENV_FILE"
echo "      Set:  BOT_TOKEN=<your actual token>"
echo ""
echo "   2. Start the bot:       sudo systemctl start $SERVICE_NAME"
echo "   3. Check status:        sudo systemctl status $SERVICE_NAME"
echo "   4. View live logs:      sudo journalctl -u $SERVICE_NAME -f"
echo "============================================================"
