import asyncio
import json
import math
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp

# =========================
# TELEGRAM SETTINGS
# =========================
BOT_TOKEN = "8492744850:AAH9hLd4SNXQL8zedZQatuKRlYyLztcSv_k"
ADMIN_IDS = {235202249}  # добавляй админов сюда

# Куда слать сигналы в форумах (Topics):
DEFAULT_TOPIC_FAIR = 7   # MEXC FAIR -> topic 7
DEFAULT_TOPIC_ARB = 4    # ARB -> topic 4

DATA_FILE = "bot_data.json"

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
}

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
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

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
async def tg_send(session: aiohttp.ClientSession, chat_id: int, text: str,
                  buttons: Optional[List[List[Dict[str, str]]]] = None,
                  thread_id: Optional[int] = None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
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
                  thread_id: Optional[int] = None):
    # Важно: editMessageText НЕ принимает message_thread_id.
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": False,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    async with session.post(url, json=payload, timeout=20) as r:
        return await r.json(content_type=None)

async def tg_get_updates(session: aiohttp.ClientSession, offset: Optional[int]) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params: Dict[str, Any] = {"timeout": POLL_UPDATES_TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    async with session.get(url, params=params, timeout=POLL_UPDATES_TIMEOUT + 10) as r:
        return await r.json(content_type=None)

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
                book = await fetch_json(session, BINGX_BOOK_TICKER, params={"symbol": raw})
                tick = await fetch_json(session, BINGX_TICKER_24H, params={"symbol": raw})
                prem = await fetch_json(session, BINGX_PREMIUM_INDEX, params={"symbol": raw})

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
        out[norm] = MarketRow(
            exchange="OKX",
            bid=bid, ask=ask, last=last,
            vol24_usd=vol,
            fund_rate=math.nan,
            fund24_est=math.nan,
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
                     min_spread: float) -> str:
    fund_spread = abs(to_float(best.sell.fund_rate) - to_float(best.buy.fund_rate))
    fund24_spread = abs(to_float(best.sell.fund24_est) - to_float(best.buy.fund24_est))

    lines: List[str] = []
    lines.append(f"FUTURES ({best.spread_best*100:.2f}%)  {symbol}")
    lines.append("")
    lines.append(f"FSpread (funding): {fund_spread*100:.3f}% | 24h≈ {fund24_spread*100:.3f}%")
    lines.append("")
    lines.append("Exchange  Price      Fund       Fund24      Time")
    lines.append("--------------------------------------------------")
    for r in sorted(rows_all, key=lambda x: x.exchange):
        lines.append(
            f"{r.exchange:<8} "
            f"{fmt_price(mid_price(r)):<10} "
            f"{fmt_pct(r.fund_rate):<10} "
            f"{fmt_pct(r.fund24_est):<10} "
            f"{r.fund_interval_h}h"
        )
    lines.append("")
    for r in sorted(rows_all, key=lambda x: x.exchange):
        lines.append(f"{r.exchange} Объём 24h: {fmt_usd(r.vol24_usd)}")

    lines.append("")
    lines.append("Пары (лучшая + вторая, если близко):")
    lines.append("--------------------------------------------------")
    lines.append(
        f"✅ {best.buy.exchange} → {best.sell.exchange}: {pair_spread_text(best)}  |  "
        f"{BTN_LONG} {best.buy.exchange} / {BTN_SHORT} {best.sell.exchange}"
    )
    if second is not None and math.isfinite(second.spread_best):
        note = "" if second.spread_best >= min_spread else " (ниже порога)"
        lines.append(
            f"ℹ️ {second.buy.exchange} → {second.sell.exchange}: {pair_spread_text(second)}{note}  |  "
            f"{BTN_LONG} {second.buy.exchange} / {BTN_SHORT} {second.sell.exchange}"
        )

    lines.append("")
    lines.append(f"Рекомендация: {BTN_LONG} на {best.buy.exchange} / {BTN_SHORT} на {best.sell.exchange}")
    return "\n".join(lines)

# =========================
# LOOPS
# =========================
async def mexc_fair_loop(session: aiohttp.ClientSession, store: Dict[str, Any], settings_lock: asyncio.Lock):
    print("✅ MEXC FAIR loop started")
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

            for chat_id in subs:
                cs = get_chat_settings(store, chat_id)
                meta = get_sub_meta(store, chat_id)
                thread_id = cs.get("topic_fair") if should_use_topics(meta) else None

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

                    text = (
                        f"🔔 MEXC FAIR\n"
                        f"Монета: {sym}\n"
                        f"Направление: {side}\n"
                        f"Спред: {spread*100:.2f}%\n"
                        f"Объём торгов 24h: {fmt_usd(vol)}\n"
                        f"Последняя цена: {fmt_price(lastp)}\n"
                        f"Fair Price: {fmt_price(fair)}\n"
                        f"Плечо: {lev_txt}"
                    )
                    buttons = [[{"text": f"{BTN_LONG if side=='LONG' else BTN_SHORT} MEXC", "url": url}]]
                    await tg_send(session, chat_id, text, buttons=buttons, thread_id=thread_id)

        except Exception as e:
            print(f"MEXC FAIR error: {type(e).__name__}: {e}")

        elapsed = time.time() - t0
        await asyncio.sleep(max(0.5, MEXC_FAIR_REFRESH_SEC - elapsed))

async def arb_loop(session: aiohttp.ClientSession, store: Dict[str, Any], settings_lock: asyncio.Lock):
    print("✅ ARB loop started (7 exchanges)")
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

            for chat_id in subs:
                cs = get_chat_settings(store, chat_id)
                meta = get_sub_meta(store, chat_id)
                thread_id = cs.get("topic_arb") if should_use_topics(meta) else None

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

                    # подтягиваем OKX funding только для участвующих
                    if best.buy.exchange == "OKX":
                        best.buy = await okx_fill_funding_for(session, best.buy)
                    if best.sell.exchange == "OKX":
                        best.sell = await okx_fill_funding_for(session, best.sell)

                    second = pick_second_if_close(pairs, best)
                    if second is not None:
                        if second.buy.exchange == "OKX":
                            second.buy = await okx_fill_funding_for(session, second.buy)
                        if second.sell.exchange == "OKX":
                            second.sell = await okx_fill_funding_for(session, second.sell)

                    key_cd = f"{chat_id}:{sym}:{best.buy.exchange}:{best.sell.exchange}"
                    prev = last_alert_ts.get(key_cd, 0.0)

                    text = make_arb_message(sym, rows_all, best, second, min_spread=min_spread)
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
                        await tg_edit(session, chat_id, last_msg_id[msg_key], text, buttons=buttons)
                    else:
                        resp = await tg_send(session, chat_id, text, buttons=buttons, thread_id=thread_id)
                        try:
                            if isinstance(resp, dict) and resp.get("ok") and isinstance(resp.get("result"), dict):
                                mid = int(resp["result"]["message_id"])
                                last_msg_id[msg_key] = mid
                        except Exception:
                            pass

        except Exception as e:
            print(f"ARB error: {type(e).__name__}: {e}")

        elapsed = time.time() - t0
        await asyncio.sleep(max(0.5, ARB_REFRESH_SEC - elapsed))

# =========================
# TELEGRAM LOOP (commands)
# =========================
async def telegram_loop(session: aiohttp.ClientSession, store: Dict[str, Any], settings_lock: asyncio.Lock):
    print("✅ Telegram loop started")
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
                is_admin = user_id in ADMIN_IDS

                cs = get_chat_settings(store, chat_id)

                if text.startswith("/start"):
                    chat_type = str(chat.get("type") or "")
                    is_forum = bool(chat.get("is_forum"))
                    subscribe(store, chat_id, chat_type=chat_type, is_forum=is_forum)

                    await tg_send(
                        session, chat_id,
                        "✅ Подписка включена.\n"
                        "Буду присылать:\n"
                        f"1) MEXC FAIR (topic {cs.get('topic_fair')}, если это форум)\n"
                        f"2) FUTURES ARB (topic {cs.get('topic_arb')}, если это форум)\n\n"
                        "Помощь: /help\n"
                        "Показать ID топика: /topic (напиши внутри топика)\n"
                        "Отключить: /stop"
                    )

                elif text.startswith("/stop"):
                    unsubscribe(store, chat_id)
                    await tg_send(session, chat_id, "🛑 Подписка отключена.\nВключить снова: /start")

                elif text.startswith("/topic"):
                    thread_id = msg.get("message_thread_id")
                    if thread_id:
                        await tg_send(session, chat_id, f"ID этого топика: {thread_id}")
                    else:
                        await tg_send(session, chat_id, "Это не топик (или в чате не включены темы).")

                elif text.startswith("/help"):
                    await tg_send(session, chat_id,
                                  "Команды:\n"
                                  "/start — подписаться\n"
                                  "/stop — отписаться\n"
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
                                  "/fair_volume X — мин. объём 24h, пример: /fair_volume 5m")

                elif text.startswith("/topics"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
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
                            await tg_send(session, chat_id, f"✅ Topics обновлены: fair={cs['topic_fair']} arb={cs['topic_arb']}")
                        except Exception:
                            await tg_send(session, chat_id, "Формат: /topics fair=4 arb=7")

                elif text.startswith("/exchanges"):
                    enabled = cs.get("arb_enabled_exchanges", DEFAULT_CHAT_SETTINGS["arb_enabled_exchanges"])
                    await tg_send(session, chat_id,
                                  "Биржи для ARB:\n"
                                  "MEXC, Bybit, BingX, Binance, OKX, KuCoin, Gate\n\n"
                                  f"✅ Включены сейчас: {', '.join(enabled)}")

                elif text.startswith("/ex_on"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
                    else:
                        name = text.replace("/ex_on", "", 1).strip()
                        if not name:
                            await tg_send(session, chat_id, "Пример: /ex_on BingX")
                            continue
                        async with settings_lock:
                            enabled = set(cs.get("arb_enabled_exchanges") or [])
                            enabled.add(name)
                            cs["arb_enabled_exchanges"] = sorted(enabled)
                            save_data(store)
                        await tg_send(session, chat_id, f"✅ Включено: {name}")

                elif text.startswith("/ex_off"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
                    else:
                        name = text.replace("/ex_off", "", 1).strip()
                        if not name:
                            await tg_send(session, chat_id, "Пример: /ex_off BingX")
                            continue
                        async with settings_lock:
                            enabled = set(cs.get("arb_enabled_exchanges") or [])
                            enabled.discard(name)
                            cs["arb_enabled_exchanges"] = sorted(enabled)
                            save_data(store)
                        await tg_send(session, chat_id, f"🛑 Выключено: {name}")

                elif text.startswith("/arb_config"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
                    else:
                        await tg_send(session, chat_id,
                                      "⚙️ Настройки ARB (для этого чата):\n"
                                      f"- Порог спреда: {float(cs['arb_min_price_spread'])*100:.2f}%\n"
                                      f"- Мин. объём 24h: {fmt_usd(float(cs['arb_min_volume_24h_usd']))}\n"
                                      f"- Биржи: {', '.join(cs.get('arb_enabled_exchanges', []))}\n"
                                      f"- Refresh: {ARB_REFRESH_SEC}s\n"
                                      f"- Cooldown: {ARB_COOLDOWN_SEC}s\n"
                                      f"- Вторая пара если хуже ≤ {SECOND_PAIR_MAX_GAP*100:.1f}%")

                elif text.startswith("/arb_spread"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, "Формат: /arb_spread 3  (это 3%)")
                            continue
                        try:
                            new_spread = parse_percent_arg(parts[1])
                            if new_spread <= 0 or new_spread >= 0.5:
                                await tg_send(session, chat_id, "Слишком странное значение. Пример: 3 или 3%")
                                continue
                            async with settings_lock:
                                cs["arb_min_price_spread"] = new_spread
                                save_data(store)
                            await tg_send(session, chat_id, f"✅ ARB порог спреда: {new_spread*100:.2f}%")
                        except Exception:
                            await tg_send(session, chat_id, "Не понял число. Пример: /arb_spread 3")

                elif text.startswith("/arb_volume"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, "Формат: /arb_volume 5m  или  /arb_volume 5000000")
                            continue
                        try:
                            new_vol = parse_usd_arg(parts[1])
                            if new_vol < 100_000:
                                await tg_send(session, chat_id, "Слишком маленький объём. Пример: /arb_volume 5m")
                                continue
                            async with settings_lock:
                                cs["arb_min_volume_24h_usd"] = float(new_vol)
                                save_data(store)
                            await tg_send(session, chat_id, f"✅ ARB мин. объём 24h: {fmt_usd(float(new_vol))}")
                        except Exception:
                            await tg_send(session, chat_id, "Не понял число. Пример: /arb_volume 5m")

                elif text.startswith("/fair_config"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
                    else:
                        await tg_send(session, chat_id,
                                      "⚙️ Настройки MEXC FAIR (для этого чата):\n"
                                      f"- SHORT от: {float(cs['fair_short_from'])*100:.2f}%\n"
                                      f"- LONG  от: {float(cs['fair_long_from'])*100:.2f}%\n"
                                      f"- Мин. объём 24h: {fmt_usd(float(cs['fair_min_volume_24h_usd']))}\n"
                                      f"- Refresh: {MEXC_FAIR_REFRESH_SEC}s\n"
                                      f"- Cooldown: {MEXC_FAIR_COOLDOWN_SEC}s")

                elif text.startswith("/fair_short"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, "Формат: /fair_short 2  (это 2%)")
                            continue
                        try:
                            v = parse_percent_arg(parts[1])
                            if v <= 0 or v >= 0.5:
                                await tg_send(session, chat_id, "Слишком странное значение. Пример: 2 или 2%")
                                continue
                            async with settings_lock:
                                cs["fair_short_from"] = v
                                save_data(store)
                            await tg_send(session, chat_id, f"✅ MEXC FAIR SHORT: {v*100:.2f}%")
                        except Exception:
                            await tg_send(session, chat_id, "Не понял число. Пример: /fair_short 2")

                elif text.startswith("/fair_long"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, "Формат: /fair_long 3  (это 3%)")
                            continue
                        try:
                            v = parse_percent_arg(parts[1])
                            if v <= 0 or v >= 0.5:
                                await tg_send(session, chat_id, "Слишком странное значение. Пример: 3 или 3%")
                                continue
                            async with settings_lock:
                                cs["fair_long_from"] = v
                                save_data(store)
                            await tg_send(session, chat_id, f"✅ MEXC FAIR LONG: {v*100:.2f}%")
                        except Exception:
                            await tg_send(session, chat_id, "Не понял число. Пример: /fair_long 3")

                elif text.startswith("/fair_volume"):
                    if not is_admin:
                        await tg_send(session, chat_id, "Нет доступа.")
                    else:
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            await tg_send(session, chat_id, "Формат: /fair_volume 5m  или  /fair_volume 5000000")
                            continue
                        try:
                            new_vol = parse_usd_arg(parts[1])
                            if new_vol < 100_000:
                                await tg_send(session, chat_id, "Слишком маленький объём. Пример: /fair_volume 5m")
                                continue
                            async with settings_lock:
                                cs["fair_min_volume_24h_usd"] = float(new_vol)
                                save_data(store)
                            await tg_send(session, chat_id, f"✅ MEXC FAIR мин. объём 24h: {fmt_usd(float(new_vol))}")
                        except Exception:
                            await tg_send(session, chat_id, "Не понял число. Пример: /fair_volume 5m")

        except Exception as e:
            print(f"Telegram loop error: {type(e).__name__}: {e}")
            await asyncio.sleep(POLL_UPDATES_SLEEP)

# =========================
# MAIN
# =========================
async def main():
    if not BOT_TOKEN or "PASTE_" in BOT_TOKEN:
        raise RuntimeError("Вставь BOT_TOKEN в начало файла.")

    store = load_data()
    print(f"✅ Loaded subscribers: {len(all_subs(store))}")

    settings_lock = asyncio.Lock()

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            telegram_loop(session, store, settings_lock),
            mexc_fair_loop(session, store, settings_lock),
            arb_loop(session, store, settings_lock),
        )

if __name__ == "__main__":
    asyncio.run(main())
