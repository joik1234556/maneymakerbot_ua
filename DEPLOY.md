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

Replace the placeholder line:
```
BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE
```
with your real token, e.g.:
```
BOT_TOKEN=8492744850:AAH9hLd4SNXQL8zedZQatuKRlYyLztcSv_k
```

Save the file (`Ctrl+O`, `Enter`, `Ctrl+X`). The file is owned by `mexcbot` and has `chmod 600` — it is not readable by other users or the web server.

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

## Why a separate user?

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
