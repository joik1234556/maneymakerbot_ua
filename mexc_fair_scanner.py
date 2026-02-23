import asyncio
import html as _html
import json
import logging
import math
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp

# =========================
# LOGGING
# =========================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("mexc_bot")

# =========================
# TELEGRAM SETTINGS
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = {235202249, 234350575}  # admin / adminegor

# Куда слать сигналы в форумах (Topics):
DEFAULT_TOPIC_FAIR = 7   # MEXC FAIR -> topic 7
DEFAULT_TOPIC_ARB = 4    # ARB -> topic 4

DATA_FILE = "bot_data.json"

# Website subscription check endpoint.
# API_BASE_URL is REQUIRED — set it in the .env file (e.g. http://127.0.0.1:8000).
# No default is provided intentionally: the bot exits at startup if this is missing.
API_BASE_URL = os.environ.get("API_BASE_URL", "").rstrip("/")
SUBSCRIPTION_API_URL = f"{API_BASE_URL}/api/bot/check-subscription"
LINK_API_URL = f"{API_BASE_URL}/api/bot/link-telegram"
DELETE_ACCOUNT_API_URL = f"{API_BASE_URL}/api/bot/delete-account"
SUBSCRIPTION_SITE_URL = "https://arbitrageinsights.xyz/"

# Number of seconds to wait for the subscription API; overridable via REQUEST_TIMEOUT env var.
SUBSCRIPTION_CHECK_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "5"))
# Timeout for account-linking API calls (same REQUEST_TIMEOUT env var).
LINK_API_TIMEOUT = SUBSCRIPTION_CHECK_TIMEOUT
# Number of times to retry a failed subscription request before giving up.
SUBSCRIPTION_RETRIES = 3
# TTL in seconds for the subscription status cache used by the signal loops.
SUBSCRIPTION_CACHE_TTL = 600  # 10 minutes — halves API call rate vs 300 s

# Required Telegram channel that users must join before receiving signals.
# Set REQUIRED_CHANNEL_ID to the numeric chat_id of the channel (e.g. -1001234567890).
# The bot must be a member of that channel for getChatMember to work.
# Leave unset (or 0) to skip the membership check.
REQUIRED_CHANNEL_ID: Optional[int] = int(os.environ.get("REQUIRED_CHANNEL_ID", "0")) or None
REQUIRED_CHANNEL_URL: str = os.environ.get(
    "REQUIRED_CHANNEL_URL", "https://t.me/+7JTnamdxatc3NTU6"
)

POLL_UPDATES_TIMEOUT = 20
POLL_UPDATES_SLEEP = 1

BTN_LONG = "🟢 LONG"
BTN_SHORT = "🔴 SHORT"

# =========================
# LOOP TIMERS
# =========================
MEXC_FAIR_REFRESH_SEC = 5
MEXC_FAIR_COOLDOWN_SEC = 30

ARB_REFRESH_SEC = 5
ARB_COOLDOWN_SEC = 30

DEFAULT_FUNDING_INTERVAL_HOURS = 8

# ВТОРАЯ ПАРА показывается, если хуже лучшей не больше чем на 0.3%
SECOND_PAIR_MAX_GAP = 0.003  # 0.3% в доле

# =========================
# DEFAULT PER-CHAT SETTINGS
# =========================
DEFAULT_CHAT_SETTINGS = {
    # ARB
    "arb_min_price_spread": 0.03,          # 3%
    "arb_min_volume_24h_usd": 5_000_000.0, # 5m$
    "arb_enabled_exchanges": ["MEXC", "Bybit", "BingX", "Binance", "OKX", "KuCoin", "Gate"],

    # MEXC FAIR
    "fair_short_from": 0.02,               # 2%
    "fair_long_from": 0.03,                # 3%
    "fair_min_volume_24h_usd": 5_000_000.0,# 5m$

    # Topics (если чат форумный)
    "topic_fair": DEFAULT_TOPIC_FAIR,
    "topic_arb": DEFAULT_TOPIC_ARB,

    # Notification language: "ru" | "uk" | "en"
    "lang": "ru",
}

# =========================
# LOCALIZATION
# =========================
SUPPORTED_LANGS = ("ru", "uk", "en")
DEFAULT_LANG = "ru"

LANG_CHOICE_TEXT = "🌐 Виберіть мову / Выберите язык / Choose language:"
LANG_CHOICE_BUTTONS: List[List[Dict[str, str]]] = [
    [
        {"text": "🇷🇺 Русский",    "callback_data": "set_lang:ru"},
        {"text": "🇺🇦 Українська", "callback_data": "set_lang:uk"},
        {"text": "🇬🇧 English",    "callback_data": "set_lang:en"},
    ],
]

STRINGS: Dict[str, Dict[str, str]] = {
    # ─────────────── RUSSIAN ───────────────
    "ru": {
        "lang_set":         "✅ Язык установлен: Русский 🇷🇺",
        "subscribed":       (
            "✅ Подписка включена.\n"
            "Буду присылать:\n"
            "1) MEXC FAIR (topic {topic_fair}, если это форум)\n"
            "2) FUTURES ARB (topic {topic_arb}, если это форум)\n\n"
            "Помощь: /help\n"
            "Показать ID топика: /topic (напиши внутри топика)\n"
            "Сменить язык: /lang\n"
            "Отключить: /stop"
        ),
        "unsubscribed":     "🛑 Подписка отключена.\nВключить снова: /start",
        "topic_id":         "ID этого топика: {thread_id}",
        "no_topic":         "Это не топик (или в чате не включены темы).",
        "no_access":        "Нет доступа.",
        "help": (
            "Команды:\n"
            "/start — подписаться\n"
            "/stop — отписаться\n"
            "/lang — сменить язык уведомлений\n"
            "/topic — показать ID топика (внутри топика)\n\n"
            "Админ/настройки (для этого чата):\n"
            "/topics fair=4 arb=7 — куда слать сигналы (топики, только в forum)\n"
            "/arb_config — настройки арбитража\n"
            "/arb_spread X — порог спреда (%), пример: /arb_spread 3\n"
            "/arb_volume X — объём 24h, пример: /arb_volume 5m\n"
            "/exchanges — список бирж\n"
            "/ex_on NAME — включить биржу\n"
            "/ex_off NAME — выключить биржу\n"
            "/fair_config — настройки MEXC FAIR\n"
            "/fair_short X — порог SHORT (%), пример: /fair_short 2\n"
            "/fair_long X — порог LONG (%), пример: /fair_long 3\n"
            "/fair_volume X — мин. объём 24h, пример: /fair_volume 5m"
        ),
        "topics_updated":   "✅ Topics обновлены: fair={topic_fair} arb={topic_arb}",
        "topics_format":    "Формат: /topics fair=4 arb=7",
        "exchanges_list":   "Биржи для ARB:\nMEXC, Bybit, BingX, Binance, OKX, KuCoin, Gate\n\n✅ Включены сейчас: {enabled}",
        "ex_enabled":       "✅ Включено: {name}",
        "ex_on_hint":       "Пример: /ex_on BingX",
        "ex_disabled":      "🛑 Выключено: {name}",
        "ex_off_hint":      "Пример: /ex_off BingX",
        "arb_config": (
            "⚙️ Настройки ARB (для этого чата):\n"
            "- Порог спреда: {spread}\n"
            "- Мин. объём 24h: {vol}\n"
            "- Биржи: {exchanges}\n"
            "- Refresh: {refresh}s\n"
            "- Cooldown: {cooldown}s\n"
            "- Вторая пара если хуже ≤ {gap}%"
        ),
        "arb_spread_fmt":   "Формат: /arb_spread 3  (это 3%)",
        "arb_spread_bad":   "Слишком странное значение. Пример: 3 или 3%",
        "arb_spread_ok":    "✅ ARB порог спреда: {val}",
        "arb_spread_err":   "Не понял число. Пример: /arb_spread 3",
        "arb_vol_fmt":      "Формат: /arb_volume 5m  или  /arb_volume 5000000",
        "arb_vol_small":    "Слишком маленький объём. Пример: /arb_volume 5m",
        "arb_vol_ok":       "✅ ARB мин. объём 24h: {val}",
        "arb_vol_err":      "Не понял число. Пример: /arb_volume 5m",
        "fair_config": (
            "⚙️ Настройки MEXC FAIR (для этого чата):\n"
            "- SHORT от: {short}\n"
            "- LONG  от: {long}\n"
            "- Мин. объём 24h: {vol}\n"
            "- Refresh: {refresh}s\n"
            "- Cooldown: {cooldown}s"
        ),
        "fair_short_fmt":   "Формат: /fair_short 2  (это 2%)",
        "fair_short_bad":   "Слишком странное значение. Пример: 2 или 2%",
        "fair_short_ok":    "✅ MEXC FAIR SHORT: {val}",
        "fair_short_err":   "Не понял число. Пример: /fair_short 2",
        "fair_long_fmt":    "Формат: /fair_long 3  (это 3%)",
        "fair_long_bad":    "Слишком странное значение. Пример: 3 или 3%",
        "fair_long_ok":     "✅ MEXC FAIR LONG: {val}",
        "fair_long_err":    "Не понял число. Пример: /fair_long 3",
        "fair_vol_fmt":     "Формат: /fair_volume 5m  или  /fair_volume 5000000",
        "fair_vol_small":   "Слишком маленький объём. Пример: /fair_volume 5m",
        "fair_vol_ok":      "✅ MEXC FAIR мин. объём 24h: {val}",
        "fair_vol_err":     "Не понял число. Пример: /fair_volume 5m",
        "fair_alert": (
            "🔔 MEXC FAIR\n"
            "Монета: {sym}\n"
            "Направление: {side}\n"
            "Спред: {spread}\n"
            "Объём торгов 24h: {vol}\n"
            "Последняя цена: {lastp}\n"
            "Fair Price: {fair}\n"
            "Плечо: {lev}"
        ),
        "arb_vol_label":        "Объём 24h",
        "arb_pairs_header":     "Пары (лучшая + вторая, если близко):",
        "arb_below_thresh":     " (ниже порога)",
        "arb_recommendation":   "Рекомендация: {btn_long} на {buy} / {btn_short} на {sell}",
        "sub_inactive": (
            "❌ Подписка не активна.\n"
            "Зарегистрируйтесь на сайте https://arbitrageinsights.xyz/\n"
            "или обратитесь к администратору."
        ),
        "health_report": (
            "🩺 Health check\n"
            "Telegram: {tg_status}\n"
            "Subscription API: {api_url}\n"
            "API status: {api_status}"
        ),
        "debug_sub_report": (
            "🔍 Debug subscription\n"
            "telegram_id: {uid}\n"
            "API URL: {api_url}\n"
            "HTTP status: {http_status}\n"
            "Response: {body}\n"
            "Result: approved={approved}"
        ),
        "link_ok":     "✅ Telegram успешно привязан к аккаунту на сайте!",
        "link_fail":   "❌ Ссылка недействительна или истекла. Получи новую на сайте.",
        "delete_ok":   "🗑 Аккаунт удалён. Все сигналы отключены.",
        "delete_fail": "⚠️ Не удалось удалить аккаунт на сайте, но подписка на бота отключена.",
        "join_required": (
            "📢 Для получения сигналов подпишитесь на наш канал:\n"
            "{channel_url}\n\n"
            "После подписки нажмите кнопку ниже:"
        ),
        "join_check_btn": "✅ Я подписался — проверить",
        "join_still_not_member": "⏳ Вы ещё не подписались на канал. Подпишитесь и нажмите кнопку снова.",
    },

    # ─────────────── UKRAINIAN ───────────────
    "uk": {
        "lang_set":         "✅ Мову встановлено: Українська 🇺🇦",
        "subscribed": (
            "✅ Підписку увімкнено.\n"
            "Буду надсилати:\n"
            "1) MEXC FAIR (topic {topic_fair}, якщо це форум)\n"
            "2) FUTURES ARB (topic {topic_arb}, якщо це форум)\n\n"
            "Допомога: /help\n"
            "Показати ID топіку: /topic (напиши всередині топіку)\n"
            "Змінити мову: /lang\n"
            "Вимкнути: /stop"
        ),
        "unsubscribed":     "🛑 Підписку вимкнено.\nУвімкнути знову: /start",
        "topic_id":         "ID цього топіку: {thread_id}",
        "no_topic":         "Це не топік (або в чаті не увімкнено теми).",
        "no_access":        "Немає доступу.",
        "help": (
            "Команди:\n"
            "/start — підписатися\n"
            "/stop — відписатися\n"
            "/lang — змінити мову сповіщень\n"
            "/topic — показати ID топіку (всередині топіку)\n\n"
            "Адмін/налаштування (для цього чату):\n"
            "/topics fair=4 arb=7 — куди надсилати сигнали (топіки, тільки у forum)\n"
            "/arb_config — налаштування арбітражу\n"
            "/arb_spread X — поріг спреду (%), приклад: /arb_spread 3\n"
            "/arb_volume X — обсяг 24h, приклад: /arb_volume 5m\n"
            "/exchanges — список бірж\n"
            "/ex_on NAME — увімкнути біржу\n"
            "/ex_off NAME — вимкнути біржу\n"
            "/fair_config — налаштування MEXC FAIR\n"
            "/fair_short X — поріг SHORT (%), приклад: /fair_short 2\n"
            "/fair_long X — поріг LONG (%), приклад: /fair_long 3\n"
            "/fair_volume X — мін. обсяг 24h, приклад: /fair_volume 5m"
        ),
        "topics_updated":   "✅ Topics оновлено: fair={topic_fair} arb={topic_arb}",
        "topics_format":    "Формат: /topics fair=4 arb=7",
        "exchanges_list":   "Біржі для ARB:\nMEXC, Bybit, BingX, Binance, OKX, KuCoin, Gate\n\n✅ Увімкнені зараз: {enabled}",
        "ex_enabled":       "✅ Увімкнено: {name}",
        "ex_on_hint":       "Приклад: /ex_on BingX",
        "ex_disabled":      "🛑 Вимкнено: {name}",
        "ex_off_hint":      "Приклад: /ex_off BingX",
        "arb_config": (
            "⚙️ Налаштування ARB (для цього чату):\n"
            "- Поріг спреду: {spread}\n"
            "- Мін. обсяг 24h: {vol}\n"
            "- Біржі: {exchanges}\n"
            "- Refresh: {refresh}s\n"
            "- Cooldown: {cooldown}s\n"
            "- Друга пара якщо гірше ≤ {gap}%"
        ),
        "arb_spread_fmt":   "Формат: /arb_spread 3  (це 3%)",
        "arb_spread_bad":   "Занадто дивне значення. Приклад: 3 або 3%",
        "arb_spread_ok":    "✅ ARB поріг спреду: {val}",
        "arb_spread_err":   "Не зрозумів число. Приклад: /arb_spread 3",
        "arb_vol_fmt":      "Формат: /arb_volume 5m  або  /arb_volume 5000000",
        "arb_vol_small":    "Занадто малий обсяг. Приклад: /arb_volume 5m",
        "arb_vol_ok":       "✅ ARB мін. обсяг 24h: {val}",
        "arb_vol_err":      "Не зрозумів число. Приклад: /arb_volume 5m",
        "fair_config": (
            "⚙️ Налаштування MEXC FAIR (для цього чату):\n"
            "- SHORT від: {short}\n"
            "- LONG  від: {long}\n"
            "- Мін. обсяг 24h: {vol}\n"
            "- Refresh: {refresh}s\n"
            "- Cooldown: {cooldown}s"
        ),
        "fair_short_fmt":   "Формат: /fair_short 2  (це 2%)",
        "fair_short_bad":   "Занадто дивне значення. Приклад: 2 або 2%",
        "fair_short_ok":    "✅ MEXC FAIR SHORT: {val}",
        "fair_short_err":   "Не зрозумів число. Приклад: /fair_short 2",
        "fair_long_fmt":    "Формат: /fair_long 3  (це 3%)",
        "fair_long_bad":    "Занадто дивне значення. Приклад: 3 або 3%",
        "fair_long_ok":     "✅ MEXC FAIR LONG: {val}",
        "fair_long_err":    "Не зрозумів число. Приклад: /fair_long 3",
        "fair_vol_fmt":     "Формат: /fair_volume 5m  або  /fair_volume 5000000",
        "fair_vol_small":   "Занадто малий обсяг. Приклад: /fair_volume 5m",
        "fair_vol_ok":      "✅ MEXC FAIR мін. обсяг 24h: {val}",
        "fair_vol_err":     "Не зрозумів число. Приклад: /fair_volume 5m",
        "fair_alert": (
            "🔔 MEXC FAIR\n"
            "Монета: {sym}\n"
            "Напрямок: {side}\n"
            "Спред: {spread}\n"
            "Обсяг торгів 24h: {vol}\n"
            "Остання ціна: {lastp}\n"
            "Fair Price: {fair}\n"
            "Плече: {lev}"
        ),
        "arb_vol_label":        "Обсяг 24h",
        "arb_pairs_header":     "Пари (найкраща + друга, якщо близько):",
        "arb_below_thresh":     " (нижче порогу)",
        "arb_recommendation":   "Рекомендація: {btn_long} на {buy} / {btn_short} на {sell}",
        "sub_inactive": (
            "❌ Підписка не активна.\n"
            "Зареєструйтесь на сайті https://arbitrageinsights.xyz/\n"
            "або зверніться до адміністратора."
        ),
        "health_report": (
            "🩺 Health check\n"
            "Telegram: {tg_status}\n"
            "Subscription API: {api_url}\n"
            "API status: {api_status}"
        ),
        "debug_sub_report": (
            "🔍 Debug subscription\n"
            "telegram_id: {uid}\n"
            "API URL: {api_url}\n"
            "HTTP status: {http_status}\n"
            "Response: {body}\n"
            "Result: approved={approved}"
        ),
        "link_ok":     "✅ Telegram успішно прив'язано до акаунту на сайті!",
        "link_fail":   "❌ Посилання недійсне або застаріло. Отримай нове на сайті.",
        "delete_ok":   "🗑 Акаунт видалено. Усі сигнали вимкнено.",
        "delete_fail": "⚠️ Не вдалося видалити акаунт на сайті, але підписку бота вимкнено.",
        "join_required": (
            "📢 Для отримання сигналів підпишіться на наш канал:\n"
            "{channel_url}\n\n"
            "Після підписки натисніть кнопку нижче:"
        ),
        "join_check_btn": "✅ Я підписався — перевірити",
        "join_still_not_member": "⏳ Ви ще не підписались на канал. Підпишіться і натисніть кнопку знову.",
    },

    # ─────────────── ENGLISH ───────────────
    "en": {
        "lang_set":         "✅ Language set: English 🇬🇧",
        "subscribed": (
            "✅ Subscription enabled.\n"
            "I will send:\n"
            "1) MEXC FAIR (topic {topic_fair}, if this is a forum)\n"
            "2) FUTURES ARB (topic {topic_arb}, if this is a forum)\n\n"
            "Help: /help\n"
            "Show topic ID: /topic (send inside the topic)\n"
            "Change language: /lang\n"
            "Disable: /stop"
        ),
        "unsubscribed":     "🛑 Subscription disabled.\nEnable again: /start",
        "topic_id":         "This topic ID: {thread_id}",
        "no_topic":         "This is not a topic (or topics are not enabled in this chat).",
        "no_access":        "Access denied.",
        "help": (
            "Commands:\n"
            "/start — subscribe\n"
            "/stop — unsubscribe\n"
            "/lang — change notification language\n"
            "/topic — show topic ID (inside the topic)\n\n"
            "Admin/settings (for this chat):\n"
            "/topics fair=4 arb=7 — where to send signals (topics, forum only)\n"
            "/arb_config — arbitrage settings\n"
            "/arb_spread X — spread threshold (%), example: /arb_spread 3\n"
            "/arb_volume X — 24h volume, example: /arb_volume 5m\n"
            "/exchanges — list of exchanges\n"
            "/ex_on NAME — enable exchange\n"
            "/ex_off NAME — disable exchange\n"
            "/fair_config — MEXC FAIR settings\n"
            "/fair_short X — SHORT threshold (%), example: /fair_short 2\n"
            "/fair_long X — LONG threshold (%), example: /fair_long 3\n"
            "/fair_volume X — min 24h volume, example: /fair_volume 5m"
        ),
        "topics_updated":   "✅ Topics updated: fair={topic_fair} arb={topic_arb}",
        "topics_format":    "Format: /topics fair=4 arb=7",
        "exchanges_list":   "Exchanges for ARB:\nMEXC, Bybit, BingX, Binance, OKX, KuCoin, Gate\n\n✅ Currently enabled: {enabled}",
        "ex_enabled":       "✅ Enabled: {name}",
        "ex_on_hint":       "Example: /ex_on BingX",
        "ex_disabled":      "🛑 Disabled: {name}",
        "ex_off_hint":      "Example: /ex_off BingX",
        "arb_config": (
            "⚙️ ARB settings (for this chat):\n"
            "- Spread threshold: {spread}\n"
            "- Min 24h volume: {vol}\n"
            "- Exchanges: {exchanges}\n"
            "- Refresh: {refresh}s\n"
            "- Cooldown: {cooldown}s\n"
            "- Second pair if worse by ≤ {gap}%"
        ),
        "arb_spread_fmt":   "Format: /arb_spread 3  (means 3%)",
        "arb_spread_bad":   "Value out of range. Example: 3 or 3%",
        "arb_spread_ok":    "✅ ARB spread threshold: {val}",
        "arb_spread_err":   "Could not parse number. Example: /arb_spread 3",
        "arb_vol_fmt":      "Format: /arb_volume 5m  or  /arb_volume 5000000",
        "arb_vol_small":    "Volume too small. Example: /arb_volume 5m",
        "arb_vol_ok":       "✅ ARB min 24h volume: {val}",
        "arb_vol_err":      "Could not parse number. Example: /arb_volume 5m",
        "fair_config": (
            "⚙️ MEXC FAIR settings (for this chat):\n"
            "- SHORT from: {short}\n"
            "- LONG  from: {long}\n"
            "- Min 24h volume: {vol}\n"
            "- Refresh: {refresh}s\n"
            "- Cooldown: {cooldown}s"
        ),
        "fair_short_fmt":   "Format: /fair_short 2  (means 2%)",
        "fair_short_bad":   "Value out of range. Example: 2 or 2%",
        "fair_short_ok":    "✅ MEXC FAIR SHORT: {val}",
        "fair_short_err":   "Could not parse number. Example: /fair_short 2",
        "fair_long_fmt":    "Format: /fair_long 3  (means 3%)",
        "fair_long_bad":    "Value out of range. Example: 3 or 3%",
        "fair_long_ok":     "✅ MEXC FAIR LONG: {val}",
        "fair_long_err":    "Could not parse number. Example: /fair_long 3",
        "fair_vol_fmt":     "Format: /fair_volume 5m  or  /fair_volume 5000000",
        "fair_vol_small":   "Volume too small. Example: /fair_volume 5m",
        "fair_vol_ok":      "✅ MEXC FAIR min 24h volume: {val}",
        "fair_vol_err":     "Could not parse number. Example: /fair_volume 5m",
        "fair_alert": (
            "🔔 MEXC FAIR\n"
            "Coin: {sym}\n"
            "Direction: {side}\n"
            "Spread: {spread}\n"
            "24h Volume: {vol}\n"
            "Last price: {lastp}\n"
            "Fair Price: {fair}\n"
            "Leverage: {lev}"
        ),
        "arb_vol_label":        "24h Volume",
        "arb_pairs_header":     "Pairs (best + second, if close):",
        "arb_below_thresh":     " (below threshold)",
        "arb_recommendation":   "Recommendation: {btn_long} on {buy} / {btn_short} on {sell}",
        "sub_inactive": (
            "❌ Subscription is not active.\n"
            "Register at https://arbitrageinsights.xyz/\n"
            "or contact the administrator."
        ),
        "health_report": (
            "🩺 Health check\n"
            "Telegram: {tg_status}\n"
            "Subscription API: {api_url}\n"
            "API status: {api_status}"
        ),
        "debug_sub_report": (
            "🔍 Debug subscription\n"
            "telegram_id: {uid}\n"
            "API URL: {api_url}\n"
            "HTTP status: {http_status}\n"
            "Response: {body}\n"
            "Result: approved={approved}"
        ),
        "link_ok":     "✅ Telegram successfully linked to your website account!",
        "link_fail":   "❌ The link is invalid or has expired. Get a new one on the website.",
        "delete_ok":   "🗑 Account deleted. All signals disabled.",
        "delete_fail": "⚠️ Could not delete the account on the website, but bot subscription is disabled.",
        "join_required": (
            "📢 To receive signals, please join our channel:\n"
            "{channel_url}\n\n"
            "After joining, press the button below:"
        ),
        "join_check_btn": "✅ I've joined — check now",
        "join_still_not_member": "⏳ You haven't joined the channel yet. Please join and press the button again.",
    },
}

def T(lang: str, key: str, **kwargs) -> str:
    """Return a localized string, falling back to DEFAULT_LANG if the key is missing."""
    s = STRINGS.get(lang, STRINGS[DEFAULT_LANG]).get(key) or STRINGS[DEFAULT_LANG].get(key, key)
    if kwargs:
        try:
            return s.format(**kwargs)
        except (KeyError, IndexError):
            return s
    return s

# =========================
# EXCHANGE API ENDPOINTS
# =========================
# MEXC Futures
MEXC_BASE = "https://contract.mexc.com"
MEXC_TICKERS = f"{MEXC_BASE}/api/v1/contract/ticker"
MEXC_CONTRACT_DETAIL = f"{MEXC_BASE}/api/v1/contract/detail"

# Bybit v5
BYBIT_BASE = "https://api.bybit.com"
BYBIT_TICKERS = f"{BYBIT_BASE}/v5/market/tickers"  # category=linear

# BingX Swap v2 (public)
BINGX_BASE = "https://open-api.bingx.com"
BINGX_CONTRACTS = f"{BINGX_BASE}/openApi/swap/v2/quote/contracts"
BINGX_BOOK_TICKER = f"{BINGX_BASE}/openApi/swap/v2/quote/bookTicker"
BINGX_TICKER_24H = f"{BINGX_BASE}/openApi/swap/v2/quote/ticker"
BINGX_PREMIUM_INDEX = f"{BINGX_BASE}/openApi/swap/v2/quote/premiumIndex"

# Binance USDT-M Futures
BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_TICKER_24H = f"{BINANCE_FAPI}/fapi/v1/ticker/24hr"
BINANCE_PREMIUM_INDEX = f"{BINANCE_FAPI}/fapi/v1/premiumIndex"

# OKX Swap
OKX_BASE = "https://www.okx.com"
OKX_TICKERS = f"{OKX_BASE}/api/v5/market/tickers"      # instType=SWAP
OKX_FUNDING = f"{OKX_BASE}/api/v5/public/funding-rate" # instId=BTC-USDT-SWAP

# KuCoin Futures
KUCOIN_FUT = "https://api-futures.kucoin.com"
KUCOIN_ALL_TICKERS = f"{KUCOIN_FUT}/api/v1/allTickers"

# Gate Futures USDT
GATE_BASE = "https://api.gateio.ws/api/v4"
GATE_TICKERS = f"{GATE_BASE}/futures/usdt/tickers"

# =========================
# TRADE PAGE LINKS
# =========================
def mexc_trade_url(symbol_mexc: str) -> str:
    return f"https://www.mexc.com/futures/{symbol_mexc}"

def bybit_trade_url(symbol_bybit: str) -> str:
    return f"https://www.bybit.com/trade/usdt/{symbol_bybit}"

def bingx_trade_url(symbol_bingx: str) -> str:
    return f"https://bingx.com/en/perpetual/{symbol_bingx}"

def binance_trade_url(symbol: str) -> str:
    return f"https://www.binance.com/en/futures/{symbol}"

def okx_trade_url(inst_id: str) -> str:
    return f"https://www.okx.com/trade-swap/{inst_id.lower()}"

def kucoin_trade_url(symbol: str) -> str:
    return f"https://futures.kucoin.com/trade/{symbol}"

def gate_trade_url(contract: str) -> str:
    return f"https://www.gate.io/futures_trade/USDT/{contract.replace('_', '')}"

# =========================
# HELPERS
# =========================
def to_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return math.nan

def is_pos(x: float) -> bool:
    return math.isfinite(x) and x > 0

def _safe_chat_id(key: str) -> Optional[int]:
    """Extract chat_id from a 'chat_id:symbol' key; returns None on parse error."""
    try:
        return int(key.split(":", 1)[0])
    except (ValueError, IndexError):
        logger.warning("Unexpected last_msg_id key format: %r", key)
        return None

def fmt_price(x: float) -> str:
    if not math.isfinite(x):
        return "N/A"
    if x >= 1:
        return f"{x:,.6f}".rstrip("0").rstrip(".")
    return f"{x:.10f}".rstrip("0").rstrip(".")

def fmt_pct(x: float) -> str:
    if not math.isfinite(x):
        return "N/A"
    return f"{x*100:.3f}%"

def fmt_usd(x: float) -> str:
    if not math.isfinite(x):
        return "N/A"
    if x >= 1e9:
        return f"{x/1e9:.1f}b$"
    if x >= 1e6:
        return f"{x/1e6:.1f}m$"
    if x >= 1e3:
        return f"{x/1e3:.1f}k$"
    return f"{x:.0f}$"

def funding_24h_estimate(rate: float, interval_h: int = DEFAULT_FUNDING_INTERVAL_HOURS) -> float:
    if not math.isfinite(rate):
        return math.nan
    if interval_h <= 0:
        interval_h = DEFAULT_FUNDING_INTERVAL_HOURS
    return rate * (24.0 / interval_h)

def parse_percent_arg(s: str) -> float:
    s = s.strip().lower().replace(" ", "").replace("%", "")
    val = float(s)
    if val > 1:
        return val / 100.0
    return val

def parse_usd_arg(s: str) -> float:
    s = s.strip().lower().replace("$", "").replace(" ", "")
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)([kmb])?", s)
    if not m:
        return float(s)
    num = float(m.group(1))
    suf = m.group(2)
    mult = 1.0
    if suf == "k":
        mult = 1e3
    elif suf == "m":
        mult = 1e6
    elif suf == "b":
        mult = 1e9
    return num * mult

def _as_list(resp: Any) -> List[dict]:
    if isinstance(resp, dict):
        d = resp.get("data")
        if isinstance(d, list):
            return [x for x in d if isinstance(x, dict)]
        if isinstance(d, dict):
            return [d]
    if isinstance(resp, list):
        return [x for x in resp if isinstance(x, dict)]
    return []

def _pick_float(d: dict, keys: List[str]) -> float:
    for k in keys:
        v = to_float(d.get(k))
        if math.isfinite(v):
            return v
    return math.nan

def normalize_symbol_usdt(base: str) -> str:
    base = base.upper()
    if base == "XBT":
        base = "BTC"
    return f"{base}USDT"

# =========================
# PERSISTENCE (per chat)
# =========================
def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return {"subs": {}, "chat_settings": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {"subs": {}, "chat_settings": {}}
        d.setdefault("subs", {})
        d.setdefault("chat_settings", {})
        return d
    except Exception:
        return {"subs": {}, "chat_settings": {}}

def save_data(d: Dict[str, Any]) -> None:
    # Write to a temp file in the same directory, then atomically rename.
    # This prevents bot_data.json corruption if the process is killed mid-write.
    dir_ = os.path.dirname(os.path.abspath(DATA_FILE))
    tmp = os.path.join(dir_, os.path.basename(DATA_FILE) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)

def get_chat_settings(store: Dict[str, Any], chat_id: int) -> Dict[str, Any]:
    cs = store["chat_settings"].get(str(chat_id))
    if not isinstance(cs, dict):
        cs = dict(DEFAULT_CHAT_SETTINGS)
        store["chat_settings"][str(chat_id)] = cs
        save_data(store)
    for k, v in DEFAULT_CHAT_SETTINGS.items():
        if k not in cs:
            cs[k] = v
    return cs

def is_subscribed(store: Dict[str, Any], chat_id: int) -> bool:
    return store["subs"].get(str(chat_id)) is not None

def subscribe(store: Dict[str, Any], chat_id: int, chat_type: str, is_forum: bool) -> None:
    store["subs"][str(chat_id)] = {
        "chat_type": chat_type,   # "private"/"group"/"supergroup"
        "is_forum": bool(is_forum),
    }
    get_chat_settings(store, chat_id)
    save_data(store)

def unsubscribe(store: Dict[str, Any], chat_id: int) -> None:
    store["subs"].pop(str(chat_id), None)
    save_data(store)

def all_subs(store: Dict[str, Any]) -> List[int]:
    out = []
    for k in store["subs"].keys():
        if str(k).lstrip("-").isdigit():
            out.append(int(k))
    return out

def get_sub_meta(store: Dict[str, Any], chat_id: int) -> Dict[str, Any]:
    v = store["subs"].get(str(chat_id))
    return v if isinstance(v, dict) else {}

def should_use_topics(meta: Dict[str, Any]) -> bool:
    return (meta.get("chat_type") in ("group", "supergroup")) and bool(meta.get("is_forum"))

# =========================
# TELEGRAM API
# =========================
def _tg_edit_is_not_modified(result: Dict[str, Any]) -> bool:
    """Returns True when Telegram rejected the edit only because content is unchanged."""
    err = result.get("description", "").lower()
    return result.get("error_code") == 400 and "message is not modified" in err

async def tg_send(session: aiohttp.ClientSession, chat_id: int, text: str,
                  buttons: Optional[List[List[Dict[str, str]]]] = None,
                  thread_id: Optional[int] = None,
                  parse_mode: str = "HTML"):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    async with session.post(url, json=payload, timeout=20) as r:
        return await r.json(content_type=None)

async def tg_edit(session: aiohttp.ClientSession, chat_id: int, message_id: int, text: str,
                  buttons: Optional[List[List[Dict[str, str]]]] = None,
                  thread_id: Optional[int] = None,
                  parse_mode: str = "HTML") -> Dict[str, Any]:
    # Важно: editMessageText НЕ принимает message_thread_id.
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    async with session.post(url, json=payload, timeout=20) as r:
        result = await r.json(content_type=None)
    if not isinstance(result, dict):
        return {}
    if not result.get("ok") and not _tg_edit_is_not_modified(result):
        logger.warning("tg_edit error [%s/%s]: %s", chat_id, message_id,
                       result.get("description", result))
    return result

async def tg_get_updates(session: aiohttp.ClientSession, offset: Optional[int]) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params: Dict[str, Any] = {"timeout": POLL_UPDATES_TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    async with session.get(url, params=params, timeout=POLL_UPDATES_TIMEOUT + 10) as r:
        return await r.json(content_type=None)

async def tg_get_chat_member(session: aiohttp.ClientSession, chat_id: int, user_id: int) -> Dict[str, Any]:
    """Returns the getChatMember object for a user in a chat, or {} on error."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"
    try:
        async with session.get(url, params={"chat_id": chat_id, "user_id": user_id}, timeout=10) as r:
            data = await r.json(content_type=None)
        if isinstance(data, dict) and data.get("ok") and isinstance(data.get("result"), dict):
            return data["result"]
    except Exception as exc:
        logger.warning("tg_get_chat_member error [%s/%s]: %s", chat_id, user_id, exc)
    return {}

async def is_group_admin(session: aiohttp.ClientSession, chat_id: int, user_id: Optional[int],
                         chat_type: str) -> bool:
    """Returns True if the user is an administrator or creator of a group/supergroup."""
    if not user_id:
        return False
    if chat_type not in ("group", "supergroup"):
        return False
    member = await tg_get_chat_member(session, chat_id, user_id)
    return member.get("status") in ("administrator", "creator")

async def is_channel_member(session: aiohttp.ClientSession, user_id: int) -> bool:
    """Returns True if the user is a member of REQUIRED_CHANNEL_ID.

    If REQUIRED_CHANNEL_ID is not configured, always returns True (check skipped).
    The bot must itself be a member of the channel for getChatMember to work.
    """
    if not REQUIRED_CHANNEL_ID or not user_id:
        return True
    member = await tg_get_chat_member(session, REQUIRED_CHANNEL_ID, user_id)
    return member.get("status") in ("creator", "administrator", "member")

async def tg_answer_callback(session: aiohttp.ClientSession, callback_query_id: str,
                              text: str = "") -> None:
    """Answer an inline keyboard callback query to dismiss the loading spinner."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    try:
        async with session.post(url, json={"callback_query_id": callback_query_id, "text": text},
                                timeout=10) as r:
            await r.read()
    except Exception:
        pass

async def check_site_subscription(session: aiohttp.ClientSession, user_id: int) -> bool:
    """Checks the subscription API with retries and strict validation.

    Returns True only when the API responds HTTP 200 with {"approved": true}.
    Returns False (fail-closed) for any error, bad status, bad JSON, or missing key.
    All failures are logged as ERROR so they are visible in journalctl immediately.
    """
    logger.info("Checking subscription for telegram_id=%s", user_id)
    logger.info("Requesting URL: %s?chat_id=%s", SUBSCRIPTION_API_URL, user_id)

    for attempt in range(1, SUBSCRIPTION_RETRIES + 1):
        try:
            t0 = time.time()
            async with session.get(
                SUBSCRIPTION_API_URL,
                params={"chat_id": user_id},
                timeout=SUBSCRIPTION_CHECK_TIMEOUT,
            ) as r:
                status = r.status
                body = await r.text()
            elapsed = time.time() - t0

            logger.info("Subscription response status=%s (%.2fs, attempt %d/%d)",
                        status, elapsed, attempt, SUBSCRIPTION_RETRIES)
            logger.debug("Subscription response body=%s", body)

            # ── Strict: only HTTP 200 is accepted ────────────────────────────
            if status != 200:
                logger.error("Subscription API returned HTTP %s for telegram_id=%s", status, user_id)
                return False

            # ── Parse JSON ───────────────────────────────────────────────────
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                logger.error("Subscription API returned invalid JSON for telegram_id=%s: %r", user_id, body)
                return False

            if not isinstance(data, dict):
                logger.error("Subscription API response is not a JSON object for telegram_id=%s: %r",
                             user_id, body)
                return False

            if "approved" not in data:
                logger.error("Subscription API response missing 'approved' key for telegram_id=%s: %r",
                             user_id, data)
                return False

            result = bool(data["approved"])
            logger.info("Subscription result: approved=%s for telegram_id=%s", result, user_id)
            return result

        except asyncio.TimeoutError:
            logger.error("Subscription API timeout (attempt %d/%d) for telegram_id=%s",
                         attempt, SUBSCRIPTION_RETRIES, user_id)
        except aiohttp.ClientConnectionError as exc:
            logger.error("Subscription API connection error (attempt %d/%d) for telegram_id=%s: %s",
                         attempt, SUBSCRIPTION_RETRIES, user_id, exc)
        except Exception:
            logger.exception("Subscription API unexpected error (attempt %d/%d) for telegram_id=%s",
                             attempt, SUBSCRIPTION_RETRIES, user_id)

        if attempt < SUBSCRIPTION_RETRIES:
            await asyncio.sleep(1)

    logger.error("Subscription check failed after %d attempts for telegram_id=%s",
                 SUBSCRIPTION_RETRIES, user_id)
    return False  # fail-closed: deny if API unreachable after all retries

# In-memory subscription cache used by the signal loops to avoid an API call on every tick.
# Maps user_id → (approved: bool, timestamp: float).
_sub_cache: Dict[int, Tuple[bool, float]] = {}

async def cached_check_subscription(session: aiohttp.ClientSession, user_id: int) -> bool:
    """Return subscription status from cache, refreshing if older than SUBSCRIPTION_CACHE_TTL."""
    now = time.time()
    entry = _sub_cache.get(user_id)
    if entry is not None:
        approved, ts = entry
        if now - ts < SUBSCRIPTION_CACHE_TTL:
            return approved
    approved = await check_site_subscription(session, user_id)
    _sub_cache[user_id] = (approved, now)
    return approved

# =========================
# DATA STRUCTURES
# =========================
@dataclass
class MarketRow:
    exchange: str
    bid: float
    ask: float
    last: float
    vol24_usd: float
    fund_rate: float
    fund24_est: float
    fund_interval_h: int
    url: str
    raw_symbol: str

# =========================
# HTTP helper
# =========================
async def fetch_json(session: aiohttp.ClientSession, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    async with session.get(url, params=params, timeout=25) as r:
        return await r.json(content_type=None)

# =========================
# LOAD MARKETS (7 exchanges)
# =========================
async def load_mexc_marketrows(session: aiohttp.ClientSession) -> Dict[str, MarketRow]:
    out: Dict[str, MarketRow] = {}
    data = await fetch_json(session, MEXC_TICKERS)
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = data if isinstance(data, list) else []
    for it in items:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "")
        if "_" not in sym:
            continue
        base, quote = sym.split("_", 1)
        if quote.upper() != "USDT":
            continue
        norm = normalize_symbol_usdt(base)
        bid = to_float(it.get("bid1"))
        ask = to_float(it.get("ask1"))
        last = to_float(it.get("lastPrice"))
        vol = to_float(it.get("amount24"))
        fund = to_float(it.get("fundingRate"))
        out[norm] = MarketRow(
            exchange="MEXC",
            bid=bid, ask=ask, last=last,
            vol24_usd=vol,
            fund_rate=fund,
            fund24_est=funding_24h_estimate(fund),
            fund_interval_h=DEFAULT_FUNDING_INTERVAL_HOURS,
            url=mexc_trade_url(sym),
            raw_symbol=sym,
        )
    return out

async def load_bybit_marketrows(session: aiohttp.ClientSession) -> Dict[str, MarketRow]:
    out: Dict[str, MarketRow] = {}
    data = await fetch_json(session, BYBIT_TICKERS, params={"category": "linear"})
    lst = None
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, dict):
            lst = result.get("list")
    if not isinstance(lst, list):
        return out
    for it in lst:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").upper()
        if not sym.endswith("USDT"):
            continue
        bid = to_float(it.get("bid1Price") or it.get("bidPrice"))
        ask = to_float(it.get("ask1Price") or it.get("askPrice"))
        last = to_float(it.get("lastPrice"))
        vol = to_float(it.get("turnover24h") or it.get("turnover24H") or it.get("volume24h"))
        fund = to_float(it.get("fundingRate"))
        out[sym] = MarketRow(
            exchange="Bybit",
            bid=bid, ask=ask, last=last,
            vol24_usd=vol,
            fund_rate=fund,
            fund24_est=funding_24h_estimate(fund),
            fund_interval_h=DEFAULT_FUNDING_INTERVAL_HOURS,
            url=bybit_trade_url(sym),
            raw_symbol=sym,
        )
    return out

async def load_bingx_marketrows(session: aiohttp.ClientSession, candidate_norm: Optional[Set[str]] = None) -> Dict[str, MarketRow]:
    out: Dict[str, MarketRow] = {}
    contracts = await fetch_json(session, BINGX_CONTRACTS)
    clist = _as_list(contracts)

    norm_to_raw: Dict[str, str] = {}
    for c in clist:
        raw = str(c.get("symbol") or "")
        if "-" not in raw:
            continue
        base, quote = raw.split("-", 1)
        if quote.upper() != "USDT":
            continue
        norm_to_raw[normalize_symbol_usdt(base)] = raw

    syms = set(norm_to_raw.keys())
    if candidate_norm:
        syms = syms.intersection(candidate_norm)
    if not syms:
        return out

    sem = asyncio.Semaphore(10)

    async def fetch_one(norm_sym: str) -> Optional[Tuple[str, MarketRow]]:
        raw = norm_to_raw.get(norm_sym)
        if not raw:
            return None
        async with sem:
            try:
                book, tick, prem = await asyncio.gather(
                    fetch_json(session, BINGX_BOOK_TICKER, params={"symbol": raw}),
                    fetch_json(session, BINGX_TICKER_24H, params={"symbol": raw}),
                    fetch_json(session, BINGX_PREMIUM_INDEX, params={"symbol": raw}),
                )

                book_list = _as_list(book)
                tick_list = _as_list(tick)
                prem_list = _as_list(prem)

                b0 = (book_list[0] if book_list else {})
                t0 = (tick_list[0] if tick_list else {})
                p0 = (prem_list[0] if prem_list else {})

                bid = _pick_float(b0, ["bidPrice", "bid", "bestBidPrice", "bestBid"])
                ask = _pick_float(b0, ["askPrice", "ask", "bestAskPrice", "bestAsk"])
                last = _pick_float(t0, ["lastPrice", "last", "close", "markPrice", "indexPrice"])

                vol_quote = _pick_float(t0, ["quoteVolume", "turnover", "quoteQty", "turnover24h", "turnover24H"])
                vol_base = _pick_float(t0, ["volume", "baseVolume", "qty", "amount", "vol"])

                vol = vol_quote
                if not is_pos(vol):
                    price_for_vol = last
                    if not is_pos(price_for_vol) and is_pos(bid) and is_pos(ask):
                        price_for_vol = (bid + ask) / 2.0
                    if is_pos(vol_base) and is_pos(price_for_vol):
                        vol = vol_base * price_for_vol

                fund = _pick_float(p0, ["fundingRate", "lastFundingRate", "funding"])

                return norm_sym, MarketRow(
                    exchange="BingX",
                    bid=bid, ask=ask, last=last,
                    vol24_usd=vol,
                    fund_rate=fund,
                    fund24_est=funding_24h_estimate(fund),
                    fund_interval_h=DEFAULT_FUNDING_INTERVAL_HOURS,
                    url=bingx_trade_url(raw),
                    raw_symbol=raw,
                )
            except Exception:
                return None

    res = await asyncio.gather(*[fetch_one(s) for s in syms], return_exceptions=True)
    for r in res:
        if isinstance(r, tuple):
            out[r[0]] = r[1]
    return out

async def load_binance_marketrows(session: aiohttp.ClientSession) -> Dict[str, MarketRow]:
    out: Dict[str, MarketRow] = {}
    tickers = await fetch_json(session, BINANCE_TICKER_24H)
    prem = await fetch_json(session, BINANCE_PREMIUM_INDEX)
    prem_map: Dict[str, dict] = {}
    if isinstance(prem, list):
        for it in prem:
            if isinstance(it, dict) and it.get("symbol"):
                prem_map[str(it["symbol"]).upper()] = it

    if not isinstance(tickers, list):
        return out

    for it in tickers:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").upper()
        if not sym.endswith("USDT"):
            continue
        bid = to_float(it.get("bidPrice"))
        ask = to_float(it.get("askPrice"))
        last = to_float(it.get("lastPrice"))
        vol = to_float(it.get("quoteVolume"))
        p = prem_map.get(sym, {})
        fund = to_float(p.get("lastFundingRate") or p.get("fundingRate"))
        out[sym] = MarketRow(
            exchange="Binance",
            bid=bid, ask=ask, last=last,
            vol24_usd=vol,
            fund_rate=fund,
            fund24_est=funding_24h_estimate(fund),
            fund_interval_h=DEFAULT_FUNDING_INTERVAL_HOURS,
            url=binance_trade_url(sym),
            raw_symbol=sym,
        )
    return out

async def load_okx_marketrows(session: aiohttp.ClientSession) -> Dict[str, MarketRow]:
    out: Dict[str, MarketRow] = {}
    data = await fetch_json(session, OKX_TICKERS, params={"instType": "SWAP"})
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        inst = str(it.get("instId") or "")
        if "-USDT-" not in inst:
            continue
        base = inst.split("-", 1)[0]
        norm = normalize_symbol_usdt(base)
        bid = to_float(it.get("bidPx"))
        ask = to_float(it.get("askPx"))
        last = to_float(it.get("last"))
        vol = to_float(it.get("volCcy24h") or it.get("volCcy24H") or it.get("vol24h"))
        fund = to_float(it.get("fundingRate"))
        out[norm] = MarketRow(
            exchange="OKX",
            bid=bid, ask=ask, last=last,
            vol24_usd=vol,
            fund_rate=fund,
            fund24_est=funding_24h_estimate(fund),
            fund_interval_h=DEFAULT_FUNDING_INTERVAL_HOURS,
            url=okx_trade_url(inst),
            raw_symbol=inst,
        )
    return out

async def okx_fill_funding_for(session: aiohttp.ClientSession, row: MarketRow) -> MarketRow:
    try:
        data = await fetch_json(session, OKX_FUNDING, params={"instId": row.raw_symbol})
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            it = items[0]
            fr = to_float(it.get("fundingRate"))
            row.fund_rate = fr
            row.fund24_est = funding_24h_estimate(fr)
    except Exception:
        pass
    return row

async def load_kucoin_marketrows(session: aiohttp.ClientSession) -> Dict[str, MarketRow]:
    out: Dict[str, MarketRow] = {}
    data = await fetch_json(session, KUCOIN_ALL_TICKERS)
    tick = None
    if isinstance(data, dict):
        d = data.get("data")
        if isinstance(d, dict):
            tick = d.get("ticker") or d.get("tickers")
    if not isinstance(tick, list):
        return out
    for it in tick:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "")
        if not sym.upper().endswith("USDTM"):
            continue
        base = sym[:-5]
        norm = normalize_symbol_usdt(base)
        bid = to_float(it.get("bestBidPrice") or it.get("bid"))
        ask = to_float(it.get("bestAskPrice") or it.get("ask"))
        last = to_float(it.get("lastTradePrice") or it.get("last"))
        vol = to_float(it.get("turnover") or it.get("volValue") or it.get("volumeValue"))
        out[norm] = MarketRow(
            exchange="KuCoin",
            bid=bid, ask=ask, last=last,
            vol24_usd=vol,
            fund_rate=math.nan,
            fund24_est=math.nan,
            fund_interval_h=DEFAULT_FUNDING_INTERVAL_HOURS,
            url=kucoin_trade_url(sym),
            raw_symbol=sym,
        )
    return out

async def load_gate_marketrows(session: aiohttp.ClientSession) -> Dict[str, MarketRow]:
    out: Dict[str, MarketRow] = {}
    data = await fetch_json(session, GATE_TICKERS)
    if not isinstance(data, list):
        return out
    for it in data:
        if not isinstance(it, dict):
            continue
        contract = str(it.get("contract") or "")
        if not contract.upper().endswith("_USDT"):
            continue
        base = contract.split("_", 1)[0]
        norm = normalize_symbol_usdt(base)
        bid = to_float(it.get("bid"))
        ask = to_float(it.get("ask"))
        last = to_float(it.get("last"))
        vol = to_float(it.get("volume_24h_quote") or it.get("volume_24h") or it.get("quote_volume"))
        fund = to_float(it.get("funding_rate"))
        out[norm] = MarketRow(
            exchange="Gate",
            bid=bid, ask=ask, last=last,
            vol24_usd=vol,
            fund_rate=fund,
            fund24_est=funding_24h_estimate(fund),
            fund_interval_h=DEFAULT_FUNDING_INTERVAL_HOURS,
            url=gate_trade_url(contract),
            raw_symbol=contract,
        )
    return out

# =========================
# MEXC FAIR + leverage
# =========================
async def load_mexc_leverage_map(session: aiohttp.ClientSession) -> Dict[str, str]:
    out: Dict[str, str] = {}
    data = await fetch_json(session, MEXC_CONTRACT_DETAIL)
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return out
    for c in items:
        if not isinstance(c, dict):
            continue
        sym = str(c.get("symbol") or "")
        if not sym:
            continue
        max_lev = to_float(c.get("maxLeverage"))
        lev_txt = "N/A"
        if is_pos(max_lev):
            lev_txt = f"{max_lev:.0f}x"
        rl = c.get("riskLimitCustom") or c.get("riskLimits") or c.get("riskLimit")
        if isinstance(rl, list) and rl:
            t1 = rl[0]
            if isinstance(t1, dict):
                tlev = to_float(t1.get("maxLeverage"))
                if is_pos(tlev):
                    lev_txt = f"{tlev:.0f}x"
        out[sym] = lev_txt
    return out

# =========================
# ARB helpers
# =========================
def valid_row_for_vol(r: MarketRow, min_vol: float) -> bool:
    return math.isfinite(r.vol24_usd) and r.vol24_usd >= min_vol

def mid_price(r: MarketRow) -> float:
    if is_pos(r.last):
        return r.last
    if is_pos(r.bid) and is_pos(r.ask):
        return (r.bid + r.ask) / 2.0
    return math.nan

def exec_spread(buy: MarketRow, sell: MarketRow) -> float:
    if not (is_pos(buy.ask) and is_pos(sell.bid)):
        return math.nan
    return (sell.bid - buy.ask) / buy.ask

def indicative_spread(buy: MarketRow, sell: MarketRow) -> float:
    pb = mid_price(buy)
    ps = mid_price(sell)
    if not (is_pos(pb) and is_pos(ps)):
        return math.nan
    return (ps - pb) / pb

@dataclass
class PairView:
    buy: MarketRow
    sell: MarketRow
    spread_best: float
    best_is_exec: bool

def best_direction_for_pair(a: MarketRow, b: MarketRow) -> Optional[PairView]:
    s1e = exec_spread(a, b)
    s1i = indicative_spread(a, b)
    if math.isfinite(s1e):
        s1best, s1exec = s1e, True
    else:
        s1best, s1exec = s1i, False

    s2e = exec_spread(b, a)
    s2i = indicative_spread(b, a)
    if math.isfinite(s2e):
        s2best, s2exec = s2e, True
    else:
        s2best, s2exec = s2i, False

    if not math.isfinite(s1best) and not math.isfinite(s2best):
        return None

    if (not math.isfinite(s2best)) or (math.isfinite(s1best) and s1best >= s2best):
        return PairView(buy=a, sell=b, spread_best=s1best, best_is_exec=s1exec)
    return PairView(buy=b, sell=a, spread_best=s2best, best_is_exec=s2exec)

def pair_spread_text(p: PairView) -> str:
    if not math.isfinite(p.spread_best):
        return "N/A"
    txt = f"{p.spread_best*100:.2f}%"
    return txt if p.best_is_exec else f"≈{txt}"

def pick_second_if_close(pairs_sorted: List[PairView], best: PairView) -> Optional[PairView]:
    best_key = (best.buy.exchange, best.sell.exchange)
    for cand in pairs_sorted[1:]:
        if not math.isfinite(cand.spread_best):
            continue
        cand_key = (cand.buy.exchange, cand.sell.exchange)
        if cand_key == best_key:
            continue
        gap = best.spread_best - cand.spread_best
        if gap <= SECOND_PAIR_MAX_GAP:
            return cand
        return None
    return None

def make_buttons(best: PairView, second: Optional[PairView]) -> List[List[Dict[str, str]]]:
    kb: List[List[Dict[str, str]]] = [[
        {"text": f"{BTN_LONG} {best.buy.exchange}", "url": best.buy.url},
        {"text": f"{BTN_SHORT} {best.sell.exchange}", "url": best.sell.url},
    ]]
    if second is not None and math.isfinite(second.spread_best):
        kb.append([
            {"text": f"{BTN_LONG} {second.buy.exchange}", "url": second.buy.url},
            {"text": f"{BTN_SHORT} {second.sell.exchange}", "url": second.sell.url},
        ])
    return kb

def make_arb_message(symbol: str, rows_all: List[MarketRow],
                     best: PairView, second: Optional[PairView],
                     min_spread: float, lang: str = DEFAULT_LANG) -> str:
    fund_spread = abs(to_float(best.sell.fund_rate) - to_float(best.buy.fund_rate))
    fund24_spread = abs(to_float(best.sell.fund24_est) - to_float(best.buy.fund24_est))

    table: List[str] = []
    table.append(f"{'Exchange':<8}  {'Price':<10} {'Fund':<10} {'Fund24':<10} Time")
    table.append("-" * 52)
    for r in sorted(rows_all, key=lambda x: x.exchange):
        table.append(
            f"{r.exchange:<8}  "
            f"{fmt_price(mid_price(r)):<10} "
            f"{fmt_pct(r.fund_rate):<10} "
            f"{fmt_pct(r.fund24_est):<10} "
            f"{r.fund_interval_h}h"
        )

    vol_lines: List[str] = []
    for r in sorted(rows_all, key=lambda x: x.exchange):
        vol_lines.append(f"{r.exchange:<8}  {T(lang, 'arb_vol_label')}: {fmt_usd(r.vol24_usd)}")

    lines: List[str] = []
    lines.append(f"FUTURES ({best.spread_best*100:.2f}%)  {_html.escape(symbol)}")
    lines.append("")
    lines.append(f"FSpread (funding): {fund_spread*100:.3f}% | 24h≈ {fund24_spread*100:.3f}%")
    lines.append("")
    lines.append(f"<pre>{_html.escape('\n'.join(table))}</pre>")
    lines.append(f"<pre>{_html.escape('\n'.join(vol_lines))}</pre>")
    lines.append("")
    lines.append(T(lang, "arb_pairs_header"))
    lines.append("-" * 50)
    lines.append(
        f"✅ {_html.escape(best.buy.exchange)} → {_html.escape(best.sell.exchange)}: {pair_spread_text(best)}  |  "
        f"{BTN_LONG} {_html.escape(best.buy.exchange)} / {BTN_SHORT} {_html.escape(best.sell.exchange)}"
    )
    if second is not None and math.isfinite(second.spread_best):
        note = "" if second.spread_best >= min_spread else T(lang, "arb_below_thresh")
        lines.append(
            f"ℹ️ {_html.escape(second.buy.exchange)} → {_html.escape(second.sell.exchange)}: {pair_spread_text(second)}{_html.escape(note)}  |  "
            f"{BTN_LONG} {_html.escape(second.buy.exchange)} / {BTN_SHORT} {_html.escape(second.sell.exchange)}"
        )

    lines.append("")
    lines.append(T(lang, "arb_recommendation",
                   btn_long=BTN_LONG, buy=_html.escape(best.buy.exchange),
                   btn_short=BTN_SHORT, sell=_html.escape(best.sell.exchange)))
    return "\n".join(lines)

# =========================
# LOOPS
# =========================
async def mexc_fair_loop(session: aiohttp.ClientSession, store: Dict[str, Any], settings_lock: asyncio.Lock):
    logger.info("MEXC FAIR loop started")
    lev_map = await load_mexc_leverage_map(session)
    last_alert: Dict[str, float] = {}  # key = f"{chat_id}:{symbol}"

    while True:
        t0 = time.time()
        try:
            data = await fetch_json(session, MEXC_TICKERS)
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                items = data if isinstance(data, list) else []

            now = time.time()
            subs = all_subs(store)

            # Prune stale entries to prevent unbounded memory growth.
            # Keep 3× cooldown as a safety margin so a slow cycle never evicts
            # an entry that is still within its cooldown window.
            _prune_ts_fair = now - MEXC_FAIR_COOLDOWN_SEC * 3
            last_alert = {k: ts for k, ts in last_alert.items() if ts > _prune_ts_fair}

            for chat_id in subs:
                cs = get_chat_settings(store, chat_id)
                meta = get_sub_meta(store, chat_id)
                thread_id = cs.get("topic_fair") if should_use_topics(meta) else None
                lang = cs.get("lang", DEFAULT_LANG)

                # Skip private chats whose site subscription has lapsed
                if meta.get("chat_type") == "private" and chat_id not in ADMIN_IDS:
                    if not await cached_check_subscription(session, chat_id):
                        continue

                fair_short_from = float(cs.get("fair_short_from", DEFAULT_CHAT_SETTINGS["fair_short_from"]))
                fair_long_from = float(cs.get("fair_long_from", DEFAULT_CHAT_SETTINGS["fair_long_from"]))
                fair_min_vol = float(cs.get("fair_min_volume_24h_usd", DEFAULT_CHAT_SETTINGS["fair_min_volume_24h_usd"]))

                for it in items:
                    if not isinstance(it, dict):
                        continue
                    sym = str(it.get("symbol") or "")
                    if not sym:
                        continue

                    fair = to_float(it.get("fairPrice"))
                    lastp = to_float(it.get("lastPrice"))
                    vol = to_float(it.get("amount24"))

                    if not (is_pos(fair) and is_pos(lastp)):
                        continue
                    if not (math.isfinite(vol) and vol >= fair_min_vol):
                        continue

                    side = None
                    min_spread = None
                    if lastp >= fair * (1 + fair_short_from):
                        side = "SHORT"
                        min_spread = fair_short_from
                    elif lastp <= fair * (1 - fair_long_from):
                        side = "LONG"
                        min_spread = fair_long_from
                    else:
                        continue

                    spread = abs(lastp - fair) / fair
                    if spread < (min_spread or 0):
                        continue

                    key = f"{chat_id}:{sym}"
                    prev = last_alert.get(key, 0.0)
                    if now - prev < MEXC_FAIR_COOLDOWN_SEC:
                        continue
                    last_alert[key] = now

                    lev_txt = lev_map.get(sym, "N/A")
                    url = mexc_trade_url(sym)

                    text = T(lang, "fair_alert",
                             sym=sym, side=side,
                             spread=f"{spread*100:.2f}%",
                             vol=fmt_usd(vol),
                             lastp=fmt_price(lastp),
                             fair=fmt_price(fair),
                             lev=lev_txt)
                    buttons = [[{"text": f"{BTN_LONG if side=='LONG' else BTN_SHORT} MEXC", "url": url}]]
                    await tg_send(session, chat_id, text, buttons=buttons, thread_id=thread_id)

        except Exception as e:
            logger.exception("MEXC FAIR error")

        elapsed = time.time() - t0
        await asyncio.sleep(max(0.5, MEXC_FAIR_REFRESH_SEC - elapsed))

async def arb_loop(session: aiohttp.ClientSession, store: Dict[str, Any], settings_lock: asyncio.Lock):
    logger.info("ARB loop started (7 exchanges)")
    last_alert_ts: Dict[str, float] = {}        # key = f"{chat}:{sym}:{buy}:{sell}"
    last_msg_id: Dict[str, int] = {}            # key = f"{chat}:{sym}" -> message_id (для edit)

    while True:
        t0 = time.time()
        try:
            # грузим рынки один раз за цикл
            mexc_task = asyncio.create_task(load_mexc_marketrows(session))
            bybit_task = asyncio.create_task(load_bybit_marketrows(session))
            binance_task = asyncio.create_task(load_binance_marketrows(session))
            okx_task = asyncio.create_task(load_okx_marketrows(session))
            kucoin_task = asyncio.create_task(load_kucoin_marketrows(session))
            gate_task = asyncio.create_task(load_gate_marketrows(session))

            mexc = await mexc_task
            bybit = await bybit_task
            binance = await binance_task
            okx = await okx_task
            kucoin = await kucoin_task
            gate = await gate_task

            candidate: Set[str] = set()
            candidate |= set(mexc.keys()) | set(bybit.keys()) | set(binance.keys()) | set(okx.keys()) | set(kucoin.keys()) | set(gate.keys())
            bingx = await load_bingx_marketrows(session, candidate_norm=candidate)

            all_syms: Set[str] = set()
            for m in (mexc, bybit, bingx, binance, okx, kucoin, gate):
                all_syms |= set(m.keys())

            now = time.time()
            subs = all_subs(store)

            # Prune stale entries to prevent unbounded memory growth.
            # 3× cooldown gives a generous safety margin for slow cycles.
            _prune_ts = now - ARB_COOLDOWN_SEC * 3
            last_alert_ts = {k: ts for k, ts in last_alert_ts.items() if ts > _prune_ts}
            _active_subs = set(subs)
            last_msg_id = {
                k: mid for k, mid in last_msg_id.items()
                if _safe_chat_id(k) in _active_subs
            }

            for chat_id in subs:
                cs = get_chat_settings(store, chat_id)
                meta = get_sub_meta(store, chat_id)
                thread_id = cs.get("topic_arb") if should_use_topics(meta) else None
                lang = cs.get("lang", DEFAULT_LANG)

                # Skip private chats whose site subscription has lapsed
                if meta.get("chat_type") == "private" and chat_id not in ADMIN_IDS:
                    if not await cached_check_subscription(session, chat_id):
                        continue

                min_spread = float(cs.get("arb_min_price_spread", DEFAULT_CHAT_SETTINGS["arb_min_price_spread"]))
                min_vol = float(cs.get("arb_min_volume_24h_usd", DEFAULT_CHAT_SETTINGS["arb_min_volume_24h_usd"]))
                enabled = set(cs.get("arb_enabled_exchanges") or DEFAULT_CHAT_SETTINGS["arb_enabled_exchanges"])

                for sym in all_syms:
                    rows_all: List[MarketRow] = []
                    if "MEXC" in enabled and sym in mexc: rows_all.append(mexc[sym])
                    if "Bybit" in enabled and sym in bybit: rows_all.append(bybit[sym])
                    if "BingX" in enabled and sym in bingx: rows_all.append(bingx[sym])
                    if "Binance" in enabled and sym in binance: rows_all.append(binance[sym])
                    if "OKX" in enabled and sym in okx: rows_all.append(okx[sym])
                    if "KuCoin" in enabled and sym in kucoin: rows_all.append(kucoin[sym])
                    if "Gate" in enabled and sym in gate: rows_all.append(gate[sym])

                    if len(rows_all) < 2:
                        continue

                    rows_for_pairs = [r for r in rows_all if valid_row_for_vol(r, min_vol)]
                    if len(rows_for_pairs) < 2:
                        continue

                    pairs: List[PairView] = []
                    rows_sorted = sorted(rows_for_pairs, key=lambda x: x.exchange)
                    for i in range(len(rows_sorted)):
                        for j in range(i + 1, len(rows_sorted)):
                            pv = best_direction_for_pair(rows_sorted[i], rows_sorted[j])
                            if pv is not None and math.isfinite(pv.spread_best):
                                pairs.append(pv)
                    if not pairs:
                        continue

                    pairs.sort(key=lambda x: x.spread_best, reverse=True)
                    best = pairs[0]
                    if best.spread_best < min_spread:
                        continue

                    # подтягиваем OKX funding только для участвующих (только если не взято из тикеров)
                    if best.buy.exchange == "OKX" and not math.isfinite(best.buy.fund_rate):
                        best.buy = await okx_fill_funding_for(session, best.buy)
                    if best.sell.exchange == "OKX" and not math.isfinite(best.sell.fund_rate):
                        best.sell = await okx_fill_funding_for(session, best.sell)

                    second = pick_second_if_close(pairs, best)
                    if second is not None:
                        if second.buy.exchange == "OKX" and not math.isfinite(second.buy.fund_rate):
                            second.buy = await okx_fill_funding_for(session, second.buy)
                        if second.sell.exchange == "OKX" and not math.isfinite(second.sell.fund_rate):
                            second.sell = await okx_fill_funding_for(session, second.sell)

                    key_cd = f"{chat_id}:{sym}:{best.buy.exchange}:{best.sell.exchange}"
                    prev = last_alert_ts.get(key_cd, 0.0)

                    text = make_arb_message(sym, rows_all, best, second, min_spread=min_spread, lang=lang)
                    buttons = make_buttons(best, second)

                    msg_key = f"{chat_id}:{sym}"

                    if now - prev < ARB_COOLDOWN_SEC:
                        # если есть сообщение — обновим
                        if msg_key in last_msg_id:
                            await tg_edit(session, chat_id, last_msg_id[msg_key], text, buttons=buttons)
                        continue

                    last_alert_ts[key_cd] = now

                    # если уже есть сообщение — редактируем, иначе отправляем новое
                    if msg_key in last_msg_id:
                        edit_resp = await tg_edit(session, chat_id, last_msg_id[msg_key], text, buttons=buttons)
                        if not edit_resp.get("ok") and not _tg_edit_is_not_modified(edit_resp):
                            # сообщение удалено или недоступно — удаляем устаревший ID и шлём новое
                            del last_msg_id[msg_key]
                            resp = await tg_send(session, chat_id, text, buttons=buttons, thread_id=thread_id)
                            try:
                                if isinstance(resp, dict) and resp.get("ok") and isinstance(resp.get("result"), dict):
                                    last_msg_id[msg_key] = int(resp["result"]["message_id"])
                            except Exception as exc:
                                logger.warning("ARB: failed to store message_id for %s: %s", msg_key, exc)
                    else:
                        resp = await tg_send(session, chat_id, text, buttons=buttons, thread_id=thread_id)
                        try:
                            if isinstance(resp, dict) and resp.get("ok") and isinstance(resp.get("result"), dict):
                                mid = int(resp["result"]["message_id"])
                                last_msg_id[msg_key] = mid
                        except Exception as exc:
                            logger.warning("ARB: failed to store message_id for %s: %s", msg_key, exc)

        except Exception as e:
            logger.exception("ARB error")

        elapsed = time.time() - t0
        await asyncio.sleep(max(0.5, ARB_REFRESH_SEC - elapsed))

# =========================
# TELEGRAM LOOP (commands)
# =========================
async def telegram_loop(session: aiohttp.ClientSession, store: Dict[str, Any], settings_lock: asyncio.Lock):
    logger.info("Telegram loop started")
    offset = None

    while True:
        try:
            data = await tg_get_updates(session, offset)
            if not data.get("ok"):
                await asyncio.sleep(POLL_UPDATES_SLEEP)
                continue

            for upd in data.get("result", []):
                try:
                    offset = int(upd.get("update_id")) + 1
                except Exception:
                    continue

                # ── Handle inline keyboard callbacks (language selection + account deletion) ──
                cbq = upd.get("callback_query")
                if isinstance(cbq, dict):
                    cbq_id = str(cbq.get("id", ""))
                    cbq_data = str(cbq.get("data", ""))
                    cbq_msg = cbq.get("message") or {}
                    cbq_chat = cbq_msg.get("chat") or {}
                    cbq_user = cbq.get("from") or {}
                    try:
                        cbq_chat_id = int(cbq_chat.get("id"))
                    except Exception:
                        await tg_answer_callback(session, cbq_id)
                        continue
                    cbq_user_id = cbq_user.get("id")
                    cbq_cs = get_chat_settings(store, cbq_chat_id)
                    cbq_lang = cbq_cs.get("lang", DEFAULT_LANG)

                    if cbq_data.startswith("set_lang:"):
                        chosen = cbq_data.split(":", 1)[1]
                        if chosen in SUPPORTED_LANGS:
                            async with settings_lock:
                                cbq_cs["lang"] = chosen
                                save_data(store)
                            await tg_answer_callback(session, cbq_id, T(chosen, "lang_set"))
                            await tg_send(session, cbq_chat_id,
                                          T(chosen, "subscribed",
                                            topic_fair=cbq_cs.get("topic_fair"),
                                            topic_arb=cbq_cs.get("topic_arb")))
                        else:
                            await tg_answer_callback(session, cbq_id)

                    elif cbq_data == "del_account":
                        # Delete account uses the *user* id (the person who clicked the button).
                        # cbq_user_id is always present in callback_query.from; if somehow absent,
                        # fall back to cbq_chat_id only for private chats (where they are equal).
                        del_target_id = cbq_user_id or cbq_chat_id
                        logger.info("Delete account request from user_id=%s chat_id=%s",
                                    cbq_user_id, cbq_chat_id)
                        api_ok = False
                        try:
                            async with session.post(
                                DELETE_ACCOUNT_API_URL,
                                json={"chat_id": del_target_id},
                                timeout=SUBSCRIPTION_CHECK_TIMEOUT,
                            ) as r:
                                del_status = r.status
                                del_body = await r.text()
                            logger.info("Delete account response status=%s body=%s user_id=%s",
                                        del_status, del_body, del_target_id)
                            try:
                                api_ok = bool(json.loads(del_body).get("ok", False))
                            except Exception:
                                # Accept HTTP 200/201/204 if JSON is unparseable
                                api_ok = del_status in (200, 201, 204)
                                if api_ok:
                                    logger.warning("Delete account API returned non-JSON HTTP %s "
                                                   "for user_id=%s: %r",
                                                   del_status, del_target_id, del_body)
                        except Exception:
                            logger.exception("Delete account API error for user_id=%s", del_target_id)
                        # Always remove the chat from the bot subscriber list using the
                        # same identifier sent to the API, then also cbq_chat_id for safety.
                        async with settings_lock:
                            unsubscribe(store, cbq_chat_id)
                            if del_target_id != cbq_chat_id:
                                unsubscribe(store, del_target_id)
                        # Clear subscription cache for both IDs
                        _sub_cache.pop(cbq_chat_id, None)
                        _sub_cache.pop(del_target_id, None)
                        reply_key = "delete_ok" if api_ok else "delete_fail"
                        await tg_answer_callback(session, cbq_id, T(cbq_lang, reply_key))
                        await tg_send(session, cbq_chat_id, T(cbq_lang, reply_key))

                    elif cbq_data == "check_join":
                        # User claims to have joined the required channel — re-check.
                        cbq_user_for_join = cbq_user_id or cbq_chat_id
                        logger.info("check_join callback from user_id=%s chat_id=%s",
                                    cbq_user_id, cbq_chat_id)
                        if await is_channel_member(session, cbq_user_for_join):
                            logger.info("user_id=%s is now a channel member", cbq_user_for_join)
                            await tg_answer_callback(session, cbq_id, T(cbq_lang, "join_check_btn"))
                            await tg_send(session, cbq_chat_id, LANG_CHOICE_TEXT,
                                          buttons=LANG_CHOICE_BUTTONS)
                        else:
                            logger.info("user_id=%s still not a channel member", cbq_user_for_join)
                            await tg_answer_callback(session, cbq_id,
                                                     T(cbq_lang, "join_still_not_member"))
                            await tg_send(
                                session, cbq_chat_id,
                                T(cbq_lang, "join_required", channel_url=REQUIRED_CHANNEL_URL),
                                buttons=[[{"text": T(cbq_lang, "join_check_btn"),
                                           "callback_data": "check_join"}]],
                            )

                    else:
                        await tg_answer_callback(session, cbq_id)
                    continue  # skip regular-message processing for this update

                # ── Handle regular messages ──
                msg = upd.get("message") or upd.get("edited_message")
                if not isinstance(msg, dict):
                    continue

                chat = msg.get("chat", {})
                text = (msg.get("text") or "").strip()
                if not text:
                    continue

                try:
                    chat_id = int(chat.get("id"))
                except Exception:
                    continue

                user = msg.get("from", {})
                user_id = user.get("id")
                username = user.get("username") or user.get("first_name") or "unknown"
                chat_type = str(chat.get("type") or "")

                logger.info("Incoming message from user_id=%s, username=%s, chat_type=%s, text=%s",
                            user_id, username, chat_type, text)

                # Bot-owner IDs always have admin rights; group/supergroup Telegram admins also qualify
                is_admin = (user_id in ADMIN_IDS) or await is_group_admin(session, chat_id, user_id, chat_type)

                cs = get_chat_settings(store, chat_id)
                lang = cs.get("lang", DEFAULT_LANG)

                # ── For group/supergroup chats, ALL commands require admin rights ──
                if chat_type in ("group", "supergroup") and not is_admin:
                    logger.info("Blocked non-admin command in group: user_id=%s chat_id=%s text=%s",
                                user_id, chat_id, text)
                    await tg_send(session, chat_id, T(lang, "no_access"))
                    continue

                # ── /start: always available in all chat types ──
                if text.startswith("/start"):
                    # ── Deep-link: /start link_<code> — link Telegram to website account ──
                    parts = text.split(maxsplit=1)
                    if len(parts) > 1 and parts[1].startswith("link_"):
                        code = parts[1][5:]  # strip "link_" prefix
                        # Basic validation: alphanumeric/hyphen/underscore, 4–128 chars
                        if not code or not re.match(r'^[A-Za-z0-9_-]{4,128}$', code):
                            logger.warning("Invalid link code from user_id=%s: %r", user_id, code)
                            await tg_send(session, chat_id, T(lang, "link_fail"))
                            continue
                        logger.info("Link request from user_id=%s code=%s", user_id, code)
                        link_ok = False
                        try:
                            async with session.post(
                                LINK_API_URL,
                                json={"code": code, "chat_id": user_id},
                                timeout=LINK_API_TIMEOUT,
                            ) as r:
                                link_status = r.status
                                link_body = await r.text()
                            logger.info("Link response status=%s body=%s for user_id=%s",
                                        link_status, link_body, user_id)
                            try:
                                link_ok = bool(json.loads(link_body).get("ok", False))
                            except Exception:
                                link_ok = False
                        except Exception:
                            logger.exception("Link API error for user_id=%s", user_id)
                        await tg_send(session, chat_id,
                                      T(lang, "link_ok" if link_ok else "link_fail"))
                        continue

                    is_forum = bool(chat.get("is_forum"))
                    subscribe(store, chat_id, chat_type=chat_type, is_forum=is_forum)
                    if chat_type == "private":
                        # Check website subscription for private chats
                        site_ok = await check_site_subscription(session, user_id or chat_id)
                        if not site_ok:
                            await tg_send(session, chat_id, T(lang, "sub_inactive"))
                            continue
                        # Check required channel membership (skip for ADMIN_IDS)
                        if user_id not in ADMIN_IDS and not await is_channel_member(session, user_id or chat_id):
                            logger.info("User user_id=%s not yet a channel member, prompting join", user_id)
                            await tg_send(
                                session, chat_id,
                                T(lang, "join_required", channel_url=REQUIRED_CHANNEL_URL),
                                buttons=[[{"text": T(lang, "join_check_btn"), "callback_data": "check_join"}]],
                            )
                            continue
                    # Subscription active + channel joined (or group chat) → show language picker
                    await tg_send(session, chat_id, LANG_CHOICE_TEXT, buttons=LANG_CHOICE_BUTTONS)
                    continue

                # ── For private chats, all other commands require active website subscription
                #    AND membership of the required channel ──
                if chat_type == "private" and user_id not in ADMIN_IDS:
                    site_ok = await check_site_subscription(session, user_id or chat_id)
                    if not site_ok:
                        await tg_send(session, chat_id, T(lang, "sub_inactive"))
                        continue
                    # Website subscription confirmed — now check channel membership
                    if not await is_channel_member(session, user_id or chat_id):
                        logger.info("Blocked command (not channel member) user_id=%s text=%r",
                                    user_id, text)
                        await tg_send(
                            session, chat_id,
                            T(lang, "join_required", channel_url=REQUIRED_CHANNEL_URL),
                            buttons=[[{"text": T(lang, "join_check_btn"),
                                       "callback_data": "check_join"}]],
                        )
                        continue
                    # Subscribed + channel member → full access
                    is_admin = True

                if text.startswith("/lang"):
                    await tg_send(session, chat_id, LANG_CHOICE_TEXT, buttons=LANG_CHOICE_BUTTONS)

                elif text.startswith("/health"):
                    # Check Telegram connectivity
                    tg_status = "✅ OK"
                    try:
                        tg_resp = await tg_get_updates(session, offset)
                        if not isinstance(tg_resp, dict):
                            tg_status = "⚠️ unexpected response"
                    except Exception as exc:
                        tg_status = f"❌ error: {exc}"

                    # Check subscription API (with response body validation)
                    api_status = "✅ OK"
                    try:
                        async with session.get(SUBSCRIPTION_API_URL,
                                               params={"chat_id": 0},
                                               timeout=SUBSCRIPTION_CHECK_TIMEOUT) as r:
                            api_http = r.status
                            api_body = await r.text()
                        if api_http != 200:
                            api_status = f"⚠️ HTTP {api_http}"
                        else:
                            try:
                                parsed = json.loads(api_body)
                                if isinstance(parsed, dict) and "approved" in parsed:
                                    api_status = f"✅ HTTP {api_http} (valid JSON)"
                                else:
                                    api_status = f"⚠️ HTTP {api_http} (missing 'approved' key)"
                            except Exception:
                                api_status = f"⚠️ HTTP {api_http} (invalid JSON)"
                    except Exception as exc:
                        api_status = f"❌ error: {exc}"

                    logger.info("Health check requested by user_id=%s: tg=%s api=%s",
                                user_id, tg_status, api_status)
                    await tg_send(session, chat_id,
                                  T(lang, "health_report",
                                    tg_status=tg_status,
                                    api_url=SUBSCRIPTION_API_URL,
                                    api_status=api_status))

                elif text.startswith("/debug_subscription"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        target_id = user_id or chat_id
                        debug_http = "N/A"
                        debug_body = "N/A"
                        debug_approved = "error"
                        try:
                            async with session.get(
                                SUBSCRIPTION_API_URL,
                                params={"chat_id": target_id},
                                timeout=SUBSCRIPTION_CHECK_TIMEOUT,
                            ) as r:
                                debug_http = str(r.status)
                                debug_body = await r.text()
                            if debug_http == "200":
                                try:
                                    d = json.loads(debug_body)
                                    debug_approved = str(bool(d.get("approved", False))) if isinstance(d, dict) else "bad JSON"
                                except Exception:
                                    debug_approved = "JSON parse error"
                            else:
                                debug_approved = f"HTTP {debug_http}"
                        except asyncio.TimeoutError:
                            debug_body = "timeout"
                            debug_approved = "timeout"
                        except Exception as exc:
                            debug_body = str(exc)
                            debug_approved = "connection error"

                        logger.info("Debug subscription for user_id=%s: http=%s approved=%s",
                                    target_id, debug_http, debug_approved)
                        await tg_send(session, chat_id,
                                      T(lang, "debug_sub_report",
                                        uid=target_id,
                                        api_url=SUBSCRIPTION_API_URL,
                                        http_status=debug_http,
                                        body=debug_body[:500],
                                        approved=debug_approved))

                elif text.startswith("/stop"):
                    unsubscribe(store, chat_id)
                    await tg_send(session, chat_id, T(lang, "unsubscribed"))

                # NOTE: /topics must come before /topic to avoid the prefix being eaten
                elif text.startswith("/topics"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        try:
                            parts = text.split()
                            kv = {}
                            for p in parts[1:]:
                                if "=" in p:
                                    k, v = p.split("=", 1)
                                    kv[k.strip().lower()] = int(v.strip())
                            async with settings_lock:
                                if "fair" in kv:
                                    cs["topic_fair"] = kv["fair"]
                                if "arb" in kv:
                                    cs["topic_arb"] = kv["arb"]
                                save_data(store)
                            await tg_send(session, chat_id,
                                          T(lang, "topics_updated",
                                            topic_fair=cs["topic_fair"], topic_arb=cs["topic_arb"]))
                        except Exception:
                            await tg_send(session, chat_id, T(lang, "topics_format"))

                elif text.startswith("/topic"):
                    thread_id = msg.get("message_thread_id")
                    if thread_id:
                        await tg_send(session, chat_id, T(lang, "topic_id", thread_id=thread_id))
                    else:
                        await tg_send(session, chat_id, T(lang, "no_topic"))

                elif text.startswith("/help"):
                    await tg_send(session, chat_id, T(lang, "help"))

                elif text.startswith("/exchanges"):
                    enabled = cs.get("arb_enabled_exchanges", DEFAULT_CHAT_SETTINGS["arb_enabled_exchanges"])
                    await tg_send(session, chat_id,
                                  T(lang, "exchanges_list", enabled=", ".join(enabled)))

                elif text.startswith("/ex_on"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        name = text.replace("/ex_on", "", 1).strip()
                        if not name:
                            await tg_send(session, chat_id, T(lang, "ex_on_hint"))
                            continue
                        async with settings_lock:
                            enabled = set(cs.get("arb_enabled_exchanges") or [])
                            enabled.add(name)
                            cs["arb_enabled_exchanges"] = sorted(enabled)
                            save_data(store)
                        await tg_send(session, chat_id, T(lang, "ex_enabled", name=name))

                elif text.startswith("/ex_off"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        name = text.replace("/ex_off", "", 1).strip()
                        if not name:
                            await tg_send(session, chat_id, T(lang, "ex_off_hint"))
                            continue
                        async with settings_lock:
                            enabled = set(cs.get("arb_enabled_exchanges") or [])
                            enabled.discard(name)
                            cs["arb_enabled_exchanges"] = sorted(enabled)
                            save_data(store)
                        await tg_send(session, chat_id, T(lang, "ex_disabled", name=name))

                elif text.startswith("/arb_config"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        await tg_send(session, chat_id,
                                      T(lang, "arb_config",
                                        spread=f"{float(cs['arb_min_price_spread'])*100:.2f}%",
                                        vol=fmt_usd(float(cs['arb_min_volume_24h_usd'])),
                                        exchanges=", ".join(cs.get("arb_enabled_exchanges", [])),
                                        refresh=ARB_REFRESH_SEC,
                                        cooldown=ARB_COOLDOWN_SEC,
                                        gap=f"{SECOND_PAIR_MAX_GAP*100:.1f}"))

                elif text.startswith("/arb_spread"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, T(lang, "arb_spread_fmt"))
                            continue
                        try:
                            new_spread = parse_percent_arg(parts[1])
                            if new_spread <= 0 or new_spread >= 0.5:
                                await tg_send(session, chat_id, T(lang, "arb_spread_bad"))
                                continue
                            async with settings_lock:
                                cs["arb_min_price_spread"] = new_spread
                                save_data(store)
                            await tg_send(session, chat_id,
                                          T(lang, "arb_spread_ok", val=f"{new_spread*100:.2f}%"))
                        except Exception:
                            await tg_send(session, chat_id, T(lang, "arb_spread_err"))

                elif text.startswith("/arb_volume"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, T(lang, "arb_vol_fmt"))
                            continue
                        try:
                            new_vol = parse_usd_arg(parts[1])
                            if new_vol < 100_000:
                                await tg_send(session, chat_id, T(lang, "arb_vol_small"))
                                continue
                            async with settings_lock:
                                cs["arb_min_volume_24h_usd"] = float(new_vol)
                                save_data(store)
                            await tg_send(session, chat_id,
                                          T(lang, "arb_vol_ok", val=fmt_usd(float(new_vol))))
                        except Exception:
                            await tg_send(session, chat_id, T(lang, "arb_vol_err"))

                elif text.startswith("/fair_config"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        await tg_send(session, chat_id,
                                      T(lang, "fair_config",
                                        short=f"{float(cs['fair_short_from'])*100:.2f}%",
                                        long=f"{float(cs['fair_long_from'])*100:.2f}%",
                                        vol=fmt_usd(float(cs['fair_min_volume_24h_usd'])),
                                        refresh=MEXC_FAIR_REFRESH_SEC,
                                        cooldown=MEXC_FAIR_COOLDOWN_SEC))

                elif text.startswith("/fair_short"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, T(lang, "fair_short_fmt"))
                            continue
                        try:
                            v = parse_percent_arg(parts[1])
                            if v <= 0 or v >= 0.5:
                                await tg_send(session, chat_id, T(lang, "fair_short_bad"))
                                continue
                            async with settings_lock:
                                cs["fair_short_from"] = v
                                save_data(store)
                            await tg_send(session, chat_id,
                                          T(lang, "fair_short_ok", val=f"{v*100:.2f}%"))
                        except Exception:
                            await tg_send(session, chat_id, T(lang, "fair_short_err"))

                elif text.startswith("/fair_long"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, T(lang, "fair_long_fmt"))
                            continue
                        try:
                            v = parse_percent_arg(parts[1])
                            if v <= 0 or v >= 0.5:
                                await tg_send(session, chat_id, T(lang, "fair_long_bad"))
                                continue
                            async with settings_lock:
                                cs["fair_long_from"] = v
                                save_data(store)
                            await tg_send(session, chat_id,
                                          T(lang, "fair_long_ok", val=f"{v*100:.2f}%"))
                        except Exception:
                            await tg_send(session, chat_id, T(lang, "fair_long_err"))

                elif text.startswith("/fair_volume"):
                    if not is_admin:
                        await tg_send(session, chat_id, T(lang, "no_access"))
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, T(lang, "fair_vol_fmt"))
                            continue
                        try:
                            new_vol = parse_usd_arg(parts[1])
                            if new_vol < 100_000:
                                await tg_send(session, chat_id, T(lang, "fair_vol_small"))
                                continue
                            async with settings_lock:
                                cs["fair_min_volume_24h_usd"] = float(new_vol)
                                save_data(store)
                            await tg_send(session, chat_id,
                                          T(lang, "fair_vol_ok", val=fmt_usd(float(new_vol))))
                        except Exception:
                            await tg_send(session, chat_id, T(lang, "fair_vol_err"))

        except Exception as e:
            logger.exception("Telegram loop error")
            await asyncio.sleep(POLL_UPDATES_SLEEP)

# =========================
# MAIN
# =========================
async def main():
    # ── Fail-fast validation ──────────────────────────────────────────────────
    if not BOT_TOKEN or "PASTE_" in BOT_TOKEN:
        logger.error("Required environment variable missing: BOT_TOKEN")
        sys.exit(1)

    if not API_BASE_URL:
        logger.error("Required environment variable missing: API_BASE_URL")
        sys.exit(1)

    logger.info("Bot starting...")
    logger.info("API_BASE_URL=%s", API_BASE_URL)
    logger.info("SUBSCRIPTION_API_URL=%s", SUBSCRIPTION_API_URL)
    logger.info("LOG_LEVEL=%s", LOG_LEVEL)
    logger.info("REQUEST_TIMEOUT=%s", SUBSCRIPTION_CHECK_TIMEOUT)

    store = load_data()
    logger.info("Loaded subscribers: %d", len(all_subs(store)))

    # ── SIGTERM handler: graceful shutdown ────────────────────────────────────
    loop = asyncio.get_running_loop()

    def _on_sigterm():
        logger.info("SIGTERM received — shutting down gracefully")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGTERM, _on_sigterm)

    settings_lock = asyncio.Lock()

    async with aiohttp.ClientSession() as session:
        try:
            await asyncio.gather(
                telegram_loop(session, store, settings_lock),
                mexc_fair_loop(session, store, settings_lock),
                arb_loop(session, store, settings_lock),
            )
        except asyncio.CancelledError:
            logger.info("Bot stopped")

if __name__ == "__main__":
    asyncio.run(main())
