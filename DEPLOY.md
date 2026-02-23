# Deployment Guide — MEXC Fair Scanner Bot

> This guide explains how to run the bot on a Linux server under a **dedicated non-root user** (`mexcbot`), completely isolated from any other service (e.g. the website) running on the same machine.

---

## Prerequisites

- Ubuntu 20.04 / 22.04 / Debian 11+ (or any systemd-based distro)
- Python 3.10 or newer: `python3 --version`
- Root / sudo access on the server

---

## First-time setup

### 1. Clone the repository onto the server

```bash
# as root or any user with git access
cd /tmp
git clone https://github.com/joik1234556/maneymakerbot_ua.git
cd maneymakerbot_ua
```

### 2. Run the setup script

```bash
sudo bash deploy/setup.sh
```

The script will:
- Create a system user **`mexcbot`** with no login shell
- Create `/opt/mexcbot/` and copy the bot files there
- Create a Python virtualenv at `/opt/mexcbot/venv/` and install dependencies
- Create an **`.env`** placeholder file at `/opt/mexcbot/.env`
- Install and enable the **`mexcbot`** systemd service (auto-start on reboot)

### 3. Set the bot token

```bash
sudo nano /opt/mexcbot/.env
```

Set **both** required variables plus any optional ones you want to customize:
```
# Required
BOT_TOKEN=8492744850:AAH9hLd4SNXQL8zedZQatuKRlYyLztcSv_k
API_BASE_URL=http://127.0.0.1:8000   # loopback — bot and site are on the same server

# Optional
LOG_LEVEL=INFO        # set to DEBUG for full request/response logging
REQUEST_TIMEOUT=5     # subscription API timeout in seconds
```

> **Why `http://127.0.0.1:8000`?**  
> Bot and website run on the same server. Using the loopback address avoids DNS, is faster, and keeps traffic off the public network. Adjust the port to match your web server (e.g. `80` for nginx, `8000` for gunicorn).

Save the file (`Ctrl+O`, `Enter`, `Ctrl+X`). The file has `chmod 600` — not readable by other users.

> **Fail-fast**: if `BOT_TOKEN` or `API_BASE_URL` are missing, the bot exits immediately with an `ERROR` log entry visible in `journalctl`.

### 4. Start the bot

```bash
sudo systemctl start mexcbot
```

### 5. Verify it is running

```bash
sudo systemctl status mexcbot

# Live logs:
sudo journalctl -u mexcbot -f
```

---

## Updating the bot

When you push new code to the repository and want to deploy it:

```bash
# Pull the latest changes
cd /tmp/maneymakerbot_ua   # or wherever you cloned it
git pull

# Run the update script — copies files, upgrades dependencies, restarts service
sudo bash deploy/update.sh
```

The update script **never overwrites** `/opt/mexcbot/.env` or `bot_data.json`, so your token and subscriber data are safe.

---

## Useful commands

| Command | Description |
|---------|-------------|
| `sudo systemctl status mexcbot` | Show current status |
| `sudo systemctl start mexcbot` | Start the bot |
| `sudo systemctl stop mexcbot` | Stop the bot |
| `sudo systemctl restart mexcbot` | Restart the bot |
| `sudo journalctl -u mexcbot -f` | Follow live logs |
| `sudo journalctl -u mexcbot -n 100` | Last 100 log lines |

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | ✅ Yes | — | Telegram bot token |
| `API_BASE_URL` | ✅ Yes | — | Base URL of the subscription API (e.g. `http://127.0.0.1:8000`) |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (`DEBUG` for full request/response detail) |
| `REQUEST_TIMEOUT` | No | `5` | Subscription API HTTP timeout (seconds) |

The bot performs **fail-fast validation**: if `BOT_TOKEN` or `API_BASE_URL` are missing at startup, it immediately logs an `ERROR` and exits with code 1. You will see the reason clearly in `journalctl`.

---

| Risk | Protection |
|------|------------|
| Bug in bot crashes the process | Only `mexcbot` process is affected; website stays up |
| Bot file compromised | Cannot read web server config, database credentials, or other users' files |
| `git pull` / `pip install` on update | Only touches `/opt/mexcbot/`; web server files are untouched |
| Accidental `rm -rf` in bot dir | Scoped to `/opt/mexcbot/`; no other services affected |

The bot user `mexcbot`:
- Has **no login shell** (`/usr/sbin/nologin`) — cannot be used interactively
- Owns only `/opt/mexcbot/` — cannot write to the web server directory
- Has its own environment variables — the website cannot accidentally read `BOT_TOKEN`

---

## File layout on the server

```
/opt/mexcbot/
├── mexc_fair_scanner.py   ← bot source (updated by deploy/update.sh)
├── requirements.txt        ← Python deps   (updated by deploy/update.sh)
├── venv/                   ← Python virtualenv (owned by mexcbot)
├── .env                    ← BOT_TOKEN (chmod 600, never overwritten)
└── bot_data.json           ← subscribers & settings (never overwritten)

/etc/systemd/system/
└── mexcbot.service         ← systemd unit
```

---

## Performance, Server-load & Security recommendations

All items below have been **implemented** in the current codebase and deployment config.

### 🚀 Performance / server load

| № | What | Status | Notes |
|---|------|--------|-------|
| 1 | **`SUBSCRIPTION_CACHE_TTL = 600` s** | ✅ Done | Changed from 300 → 600 s in source — halves subscription API call rate with no user-visible impact |
| 2 | **Single process only** | ✅ Done | `mexcbot.service` runs one instance; cache is per-process — two instances would double API calls |
| 3 | **Long-poll timeout 30 s** | ✅ Done | `POLL_UPDATES_TIMEOUT=30` — do not lower this; shorter values increase Telegram API traffic |

### 🔒 Security

| № | What | Status | Notes |
|---|------|--------|-------|
| 1 | **`chmod 600 /opt/mexcbot/.env`** | ✅ Done | `deploy/setup.sh` sets this automatically; verify with `ls -la /opt/mexcbot/.env` |
| 2 | **Rotate `BOT_TOKEN`** | ⚠️ Manual | Revoke & re-issue via @BotFather periodically; update `.env` then `systemctl restart mexcbot` |
| 3 | **Firewall the API port** | ⚠️ Manual | Run `ufw deny <API_PORT>` so the site API is not reachable from the internet |
| 4 | **`systemd` security hardening** | ✅ Done | Added to `deploy/mexcbot.service`: `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, `ReadWritePaths`, `NoNewPrivileges`, `CapabilityBoundingSet=`, `PrivateDevices`, `RestrictAddressFamilies` |
| 5 | **Subscription response body at DEBUG only** | ✅ Done | `logger.info(status …)` stays; `logger.debug(body …)` — body only appears when `LOG_LEVEL=DEBUG`, so PII never appears in `journalctl` at default `INFO` level |

To apply the systemd hardening to a running server:

```bash
sudo cp /tmp/maneymakerbot_ua/deploy/mexcbot.service /etc/systemd/system/mexcbot.service
sudo systemctl daemon-reload
sudo systemctl restart mexcbot
sudo systemctl status mexcbot   # verify it started cleanly
```
