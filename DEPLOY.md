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

These changes are **not** blocking for the bot to run, but are strongly recommended for a
stable production environment.

### 🚀 Performance / server load

| № | What | Why | How |
|---|------|-----|-----|
| 1 | **Increase `SUBSCRIPTION_CACHE_TTL`** (default 300 s) | Currently every signal loop iteration may hit the subscription API for each subscriber. Raising the TTL to 600 s halves the request rate; 900 s reduces it to one-third — with no visible impact for users. | `echo "SUBSCRIPTION_CACHE_TTL=600" >> /opt/mexcbot/.env` |
| 2 | **Use a UNIX socket for the subscription API** | The bot talks to the website API over `http://127.0.0.1:PORT`. Switching to a UNIX socket (`/run/webapp.sock`) removes TCP overhead and loopback routing. | Set `API_BASE_URL=http+unix://%2Frun%2Fwebapp.sock` — the percent-encoded path represents `/run/webapp.sock` (needs `aiohttp` UNIX connector support on the web app side). |
| 3 | **Reduce `POLL_UPDATES_TIMEOUT`** only if needed | Long-poll timeout of 30 s is fine. Do **not** lower it — shorter values increase Telegram API calls needlessly. | – |
| 4 | **Subscription cache is per-process** | If you ever run two bot instances (not recommended), each will have its own cache and double the API calls. Keep a single process. | One `mexcbot.service` unit only. |

### 🔒 Security

| № | What | Why | How |
|---|------|-----|-----|
| 1 | **Protect `.env` permissions** | Must not be world-readable. | `chmod 600 /opt/mexcbot/.env && chown mexcbot:mexcbot /opt/mexcbot/.env` |
| 2 | **Rotate `BOT_TOKEN` periodically** | If the server is ever compromised, an attacker with the token can impersonate the bot. | Revoke and re-issue via @BotFather every few months; update `.env` and `systemctl restart mexcbot`. |
| 3 | **Firewall the subscription API port** | The site API runs on a local port. If it is bound to `0.0.0.0` it is reachable from the internet. | `ufw deny <API_PORT>` — only allow loopback connections. |
| 4 | **Keep Python and aiohttp up to date** | Security patches arrive frequently. | Add `pip install -U aiohttp` to `deploy/update.sh` (already done in `venv`). |
| 5 | **Rate-limit `/debug_subscription`** | Admin command that makes a live API call. Currently has no throttle; abuse by an admin account could spam the site API. | Add per-user cooldown if needed (low priority, admin-only). |
| 6 | **Do not log full response bodies at INFO** | If the subscription API ever returns PII, it will appear in `journalctl`. Downgrade the `Subscription response body=` log statement from `INFO` to `DEBUG` in the source code; then keep `LOG_LEVEL=INFO` in production so the body is only visible during active debugging sessions. | Change `logger.info("Subscription response body=…")` → `logger.debug(…)` in `mexc_fair_scanner.py`, then use `LOG_LEVEL=INFO` in `.env`. |
| 7 | **Enable `systemd` security hardening** | Restrict what the bot process can do at the OS level. | Add to `mexcbot.service` under `[Service]`: |

```ini
# Filesystem isolation
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/mexcbot

# Privilege restrictions
NoNewPrivileges=true
CapabilityBoundingSet=
PrivateDevices=true

# Network restrictions (bot needs outbound HTTPS + loopback)
RestrictAddressFamilies=AF_INET AF_INET6
```

Reload after editing: `systemctl daemon-reload && systemctl restart mexcbot`
