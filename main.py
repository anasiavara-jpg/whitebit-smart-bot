# main.py — WhiteBIT Smart Bot (v4-ready, consolidated + scalp-fix + antiflood)
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Dict, Any, Optional

import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from dotenv import load_dotenv
from decimal import Decimal, ROUND_DOWN, ROUND_UP

# ---------------- CONFIG ----------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

if not (BOT_TOKEN and API_KEY and API_SECRET):
    raise RuntimeError("BOT_TOKEN / API_KEY / API_SECRET must be set in environment")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --------- PERSIST PATH FOR markets.json (Render-safe) ----------
def _ensure_dir(p: str) -> None:
    try:
        d = os.path.dirname(p)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
    except Exception:
        pass

def _pick_markets_path() -> str:
    """
    Порядок пріоритету:
    1) env MARKETS_FILE (якщо заданий)
    2) /var/tmp/markets.json  (часто має права на запис)
    3) /tmp/markets.json
    4) ./markets.json (текуща папка — ок для локалки)
    """
    candidates = [
        os.getenv("MARKETS_FILE"),
        "/var/tmp/markets.json",
        "/tmp/markets.json",
        os.path.join(os.getcwd(), "markets.json"),
    ]
    for p in candidates:
        if not p:
            continue
        try:
            _ensure_dir(p)
            # пробний запис/читання
            with open(p, "a", encoding="utf-8") as _:
                pass
            return p
        except Exception:
            continue
    # останній фолбек: поточна директорія
    return "markets.json"

# WhiteBIT base (важливо: без /api/v4 у BASE_URL)
BASE_URL = "https://whitebit.com"
MARKETS_FILE = _pick_markets_path()
markets: Dict[str, Dict[str, Any]] = {}

# Кеш правил ринків (price/amount precision, min тощо)
market_rules: Dict[str, Dict[str, Any]] = {}

# ---------------- JSON SAVE/LOAD ----------------
def save_markets():
    try:
        _ensure_dir(MARKETS_FILE)
        with open(MARKETS_FILE, "w", encoding="utf-8") as f:
            json.dump(markets, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Помилка збереження markets.json ({MARKETS_FILE}): {e}")

def _normalize_market_cfg(cfg: dict) -> dict:
    # гарантуємо наявність ключів для різних версій файлу
    cfg = dict(cfg or {})
    cfg.setdefault("tp", None)
    cfg.setdefault("sl", None)
    cfg.setdefault("orders", [])
    cfg.setdefault("autotrade", False)
    cfg.setdefault("buy_usdt", 10)
    cfg.setdefault("chat_id", None)
    cfg.setdefault("rebuy_pct", 0.0)
    cfg.setdefault("last_tp_price", None)
    # >>> нове: мікро-скальп і режими SL
    cfg.setdefault("scalp", False)
    cfg.setdefault("tick_pct", 0.25)
    cfg.setdefault("levels", 3)
    cfg.setdefault("maker_only", True)
    cfg.setdefault("sl_mode", "trigger")   # "trigger" | "trailing"
    cfg.setdefault("entry_price", None)
    cfg.setdefault("peak", None)
    # >>> анти-«ресідинг» скальп сітки
    cfg.setdefault("scalp_seeded", False)
    cfg.setdefault("last_seed_at", 0)
    cfg.setdefault("seed_cooldown_s", 30)
    # >>> анти-флуд повідомлень
    cfg.setdefault("last_msg", {})
    return cfg

def load_markets():
    global markets
    if os.path.exists(MARKETS_FILE):
        try:
            with open(MARKETS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                markets = raw if isinstance(raw, dict) else {}
        except Exception as e:
            logging.error(f"Помилка завантаження markets.json: {e}")
            markets = {}
    else:
        markets = {}
        save_markets()

    # нормалізація існуючих ринків
    dirty = False
    for m in list(markets.keys()):
        if isinstance(markets[m], dict):
            new_cfg = _normalize_market_cfg(markets[m])
            if new_cfg != markets[m]:
                markets[m] = new_cfg
                dirty = True
        else:
            del markets[m]
            dirty = True
    if dirty:
        save_markets()

# ---------------- TIME/HELPERS ----------------
def now_ms() -> int:
    return int(time.time() * 1000)

# монотонний nonce: гарантуємо зростання навіть якщо кілька запитів у той самий ms
_nonce = now_ms()
def next_nonce() -> int:
    global _nonce
    n = now_ms()
    if n <= _nonce:
        _nonce += 1
    else:
        _nonce = n
    return _nonce

def _payload_and_headers(path: str, extra_body: Optional[dict] = None) -> tuple[bytes, dict]:
    """
    WhiteBIT v4 auth:
      body JSON містить: request (повний шлях), nonce (ms), + дод.поля
      X-TXC-PAYLOAD = base64(body_bytes)
      X-TXC-SIGNATURE = hex(HMAC_SHA512(payload_b64, API_SECRET))
    """
    body = {"request": path, "nonce": next_nonce()}
    if extra_body:
        body.update(extra_body)

    body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    payload_b64 = base64.b64encode(body_bytes)
    signature = hmac.new(API_SECRET.encode(), payload_b64, hashlib.sha512).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_KEY,
        "X-TXC-PAYLOAD": payload_b64.decode(),
        "X-TXC-SIGNATURE": signature,
    }
    return body_bytes, headers

# ---------------- HTTP (WhiteBIT v4) ----------------
async def public_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(BASE_URL + path)
        try:
            return r.json()
        except Exception:
            logging.error(f"Помилка декодування public відповіді: {r.text}")
            return {"error": r.text}

async def private_post(path: str, extra_body: Optional[dict] = None) -> dict:
    body_bytes, headers = _payload_and_headers(path, extra_body)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(BASE_URL + path, headers=headers, content=body_bytes)
        try:
            data = r.json()
        except Exception:
            logging.error(f"Помилка декодування private відповіді: {r.text}")
            return {"error": r.text}

        if isinstance(data, dict) and (data.get("success") is False) and "message" in data:
            logging.error(f"WhiteBIT error: {data.get('message')}")
        return data

# ---------------- MARKET RULES ----------------
async def load_market_rules():
    """
    Завантажуємо правила ринків і кешуємо:
      - amount/price precision (різні назви ключів підтримані)
      - мінімальні обмеження, якщо є
    Основний ендпоінт: /api/v4/public/markets
    """
    global market_rules

    def _to_dec(v):
        try:
            return Decimal(str(v))
        except Exception:
            return None

    def _parse_list(lst):
        rules = {}
        for s in lst:
            if not isinstance(s, dict):
                continue
            name = (s.get("name") or s.get("symbol") or s.get("market") or "").upper()
            if not name:
                continue

            amt_prec = (
                s.get("amount_precision")
                or s.get("stock_precision")
                or s.get("stockPrecision")
                or s.get("amountPrecision")
                or s.get("quantity_precision")
                or s.get("quantityPrecision")
                or s.get("stockPrec")
            )
            price_prec = (
                s.get("price_precision")
                or s.get("money_precision")
                or s.get("moneyPrecision")
                or s.get("pricePrecision")
                or s.get("moneyPrec")
            )
            try:
                amt_prec = int(amt_prec) if amt_prec is not None else None
            except Exception:
                amt_prec = None
            try:
                price_prec = int(price_prec) if price_prec is not None else None
            except Exception:
                price_prec = None

            min_amount = s.get("min_amount") or s.get("minAmount")
            min_total  = s.get("min_total")  or s.get("minTotal") or s.get("min_value") or s.get("minValue")

            rules[name] = {
                "amount_precision": amt_prec if amt_prec is not None else 6,
                "price_precision":  price_prec if price_prec is not None else 6,
                "min_amount": _to_dec(min_amount),
                "min_total":  _to_dec(min_total),
            }
        return rules

    try:
        data = await public_get("/api/v4/public/markets")
        if isinstance(data, list) and data:
            market_rules = _parse_list(data)
            logging.info(f"Loaded market rules from /markets for {len(market_rules)} symbols")
            return

        alt = await public_get("/api/v4/public/symbols")
        if isinstance(alt, list) and alt:
            market_rules = _parse_list(alt)
            logging.info(f"Loaded market rules from /symbols for {len(market_rules)} symbols")
            return

        logging.warning(f"Rules fetch returned unexpected payloads: /markets={type(data)}, /symbols={type(alt)}")
    except Exception as e:
        logging.error(f"load_market_rules error: {e}")

# ---------------- PRECISION HELPERS ----------------
def get_rules(market: str) -> Dict[str, Any]:
    m = market.upper()
    r = market_rules.get(m, {})
    return {
        "amount_precision": r.get("amount_precision", 6),
        "price_precision":  r.get("price_precision", 6),
        "min_amount":       r.get("min_amount"),
        "min_total":        r.get("min_total"),
    }

def step_from_precision(prec: int) -> Decimal:
    return Decimal(1) / (Decimal(10) ** int(prec))

def ceil_to_step(x: Decimal, step: Decimal) -> Decimal:
    """
    Підняти число x до найближчого кратного step (CEIL).
    Використовується, коли треба довести amount/total до біржового мінімуму.
    """
    x = Decimal(str(x))
    if step <= 0:
        return x
    units = (x / step).to_integral_value(rounding=ROUND_UP)
    return units * step

def quantize_amount(market: str, amount: float) -> Decimal:
    rules = get_rules(market)
    step = step_from_precision(rules["amount_precision"])
    return (Decimal(str(amount)) // step) * step

def quantize_price(market: str, price: float) -> Decimal:
    rules = get_rules(market)
    step = step_from_precision(rules["price_precision"])
    return (Decimal(str(price)) // step) * step

def ensure_minima_for_order(market: str, side: str, price: Optional[float],
                            amount_base: Optional[Decimal], amount_quote: Optional[Decimal]):
    """
    Повертає (amount_base, amount_quote) з урахуванням мінімалок:
      - min_amount (BASE)
      - min_total  (QUOTE = price * amount_base)

    MARKET BUY -> керуємось amount_quote (price немає).
    LIMIT (buy/sell) -> перевіряємо і amount, і total (бо price відома).
    MARKET SELL -> застосовуємо лише min_amount.
    """
    rules = get_rules(market)
    min_amount = rules.get("min_amount")  # Decimal | None
    min_total  = rules.get("min_total")   # Decimal | None

    ap = step_from_precision(rules["amount_precision"])
    pp = step_from_precision(rules["price_precision"])

    side_l = (side or "").lower()

    # MARKET BUY: керуємось лише сумою в QUOTE (price немає)
    if side_l == "buy" and price is None:
        if amount_quote is not None and min_total:
            if amount_quote < min_total:
                adj = (min_total * Decimal("1.001"))
                adj = (adj // pp) * pp
                if adj <= 0:
                    adj = pp
                amount_quote = adj
        return (amount_base, amount_quote)

    # MARKET SELL: price немає — перевіряємо лише min_amount у BASE
    if side_l == "sell" and price is None and amount_base is not None:
        if min_amount and amount_base < min_amount:
            logging.info(f"[MIN AMOUNT] {market}: amount {amount_base} < {min_amount}, піднімаю.")
            amount_base = ceil_to_step(min_amount, ap)
        return (amount_base, amount_quote)

    # SELL або LIMIT BUY (price відома): перевіряємо min_amount і min_total (тільки CEIL!)
    if price and amount_base is not None:
        price_dec = Decimal(str(price))
        if min_amount and amount_base < min_amount:
            amount_base = ceil_to_step(min_amount, ap)

        if min_total:
            total = price_dec * amount_base
            if total < min_total:
                need_base = min_total / price_dec
                need_base = ceil_to_step(need_base, ap)
                if need_base > amount_base:
                    amount_base = need_base

    return (amount_base, amount_quote)

# ---------------- WHITEBIT API WRAPPERS ----------------
async def get_balance() -> dict:
    data = await private_post("/api/v4/trade-account/balance")
    logging.info(f"DEBUG balance: {data}")
    return data if isinstance(data, dict) else {}

async def place_market_order(market: str, side: str, amount: float) -> dict:
    """
    BUY  -> amount = сума у QUOTE (USDT)
    SELL -> amount = кількість у BASE
    Підганяємо під прецизійність біржі + мінімальні ліміти.
    """
    body = {"market": market, "side": side, "type": "market"}

    if side.lower() == "buy":
        # Без зайвої квантизації — лише доводимо до min_total
        q_amount = Decimal(str(amount))
        _, q_amount = ensure_minima_for_order(market, "buy", price=None,
                                              amount_base=None, amount_quote=q_amount)
        body["amount"] = float(q_amount)
    else:
        a = quantize_amount(market, amount)
        if a <= 0:
            a = step_from_precision(get_rules(market)["amount_precision"])
        # Перевіряємо мінімальне min_amount
        a, _ = ensure_minima_for_order(market, "sell", price=None,
                                       amount_base=a, amount_quote=None)
        body["amount"] = float(a)

    logging.info(
        f"[DEBUG] market={market} side={side} amount={body['amount']} "
        f"({'quote' if side.lower()=='buy' else 'base'})"
    )
    return await private_post("/api/v4/order/market", body)

async def place_limit_order(
    market: str, side: str, price: float, amount: float,
    client_order_id: Optional[str] = None, post_only: Optional[bool] = None,
    stp: Optional[str] = None
) -> dict:
    p = quantize_price(market, price)
    a = quantize_amount(market, amount)
    if a <= 0:
        a = step_from_precision(get_rules(market)["amount_precision"])
    if p <= 0:
        p = step_from_precision(get_rules(market)["price_precision"])

    # CEIL-мінімалки для лімітів
    a, _ = ensure_minima_for_order(market, side, price=float(p),
                                   amount_base=a, amount_quote=None)

    body = {
        "market": market,
        "side": side,
        "amount": float(a),
        "price": float(p),
        "type": "limit",
    }
    if client_order_id:
        body["clientOrderId"] = str(client_order_id)
    if post_only is not None:
        body["postOnly"] = bool(post_only)
    # STP вимикаємо: на WhiteBIT v4 часто не підтримується і дає 400

    return await private_post("/api/v4/order/new", body)

async def active_orders(market: Optional[str] = None) -> dict:
    body = {}
    if market:
        body["market"] = market

    data = await private_post("/api/v4/orders", body)

    def _normalize(d):
        if isinstance(d, list):
            return {"orders": d}
        if isinstance(d, dict):
            lst = d.get("orders")
            if isinstance(lst, list):
                return {"orders": lst}
            for k in ("result", "data"):
                v = d.get(k)
                if isinstance(v, list):
                    return {"orders": v}
        return None

    norm = _normalize(data)
    if norm is not None:
        return norm

    # Фолбек: альтернативний ендпоінт активних ордерів
    alt = await private_post("/api/v4/order/active", body)
    norm_alt = _normalize(alt)
    if norm_alt is not None:
        return norm_alt

    logging.warning(f"[active_orders] unexpected payloads: /orders={type(data)}, /order/active={type(alt)}")
    return {"orders": []}

async def cancel_order(market: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> dict:
    body = {"market": market}
    if client_order_id:
        body["clientOrderId"] = str(client_order_id)
    elif order_id is not None:
        body["orderId"] = str(order_id)
    else:
        return {"success": False, "message": "Потрібно вказати order_id або client_order_id"}
    return await private_post("/api/v4/order/cancel", body)

# ---------------- PUBLIC TICKER (надійний) ----------------
async def get_last_price(market: str) -> Optional[float]:
    """
    Стабільно дістає last_price незалежно від формату відповіді.
    Спочатку точковий запит, далі фолбек на загальний.
    """
    try:
        # 1) точково
        data = await public_get(f"/api/v4/public/ticker?market={market}")
        if isinstance(data, dict) and market in data:
            lp = data[market].get("last_price")
            try:
                return float(lp) if lp is not None else None
            except Exception:
                return None
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("market") == market:
                    lp = item.get("last_price")
                    try:
                        return float(lp) if lp is not None else None
                    except Exception:
                        return None

        # 2) фолбек — загальний тікер
        t = await public_get("/api/v4/public/ticker")
        if isinstance(t, dict):
            lp = (t.get(market) or {}).get("last_price")
            try:
                return float(lp) if lp is not None else None
            except Exception:
                return None
        if isinstance(t, list):
            for item in t:
                if isinstance(item, dict) and item.get("market") == market:
                    lp = item.get("last_price")
                    try:
                        return float(lp) if lp is not None else None
                    except Exception:
                        return None
    except Exception as e:
        logging.exception(f"Не вдалося взяти last_price для {market}: {e}")
    return None

# ---------------- EXTRA HELPERS FOR HOLDINGS/AUTOSTART ----------------
def base_symbol_from_market(market: str) -> str:
    return market.split("_")[0].upper()

async def get_usdt_available() -> Decimal:
    b = await get_balance()
    try:
        return Decimal(str((b.get("USDT") or {}).get("available", "0")))
    except Exception:
        return Decimal("0")

async def get_base_available(market: str) -> Decimal:
    b = await get_balance()
    base = base_symbol_from_market(market)
    try:
        return Decimal(str((b.get(base) or {}).get("available", "0")))
    except Exception:
        return Decimal("0")

# ---------------- NOTIFY HELPERS ----------------
def can_notify(cfg: dict, key: str, cooldown_s: int = 10) -> bool:
    """
    Простий анти-флуд: те саме повідомлення не частіше, ніж раз на cooldown_s секунд.
    """
    try:
        last = int(cfg.get("last_msg", {}).get(key, 0))
    except Exception:
        last = 0
    if now_ms() - last > cooldown_s * 1000:
        cfg.setdefault("last_msg", {})[key] = now_ms()
        save_markets()
        return True
    return False

# ---------------- BOT COMMANDS ----------------
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 Привіт! Я трейдинг-бот для WhiteBIT.\n"
        "Використай /help щоб подивитись список команд."
    )

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "<b>Основні:</b>\n"
        "/start — вітання\n"
        "/help — список команд\n\n"
        "<b>Торгові:</b>\n"
        "/balance — баланс\n"
        "/market BTC/USDT — додати ринок\n"
        "/settp BTC/USDT 5 — TP у %\n"
        "/setsl BTC/USDT 2 — SL у %\n"
        "/setbuy BTC/USDT 30 — купівля на 30 USDT\n"
        "/buy BTC/USDT — разова купівля\n"
        "/status — активні ринки\n"
        "/stop — зупиняє торгівлю\n"
        "/removemarket BTC/USDT — видаляє ринок\n\n"
        "<b>Технічні:</b>\n"
        "/restart — перезапуск логіки\n"
        "/autotrade BTC/USDT on|off — увімк/вимк автотрейд\n"
        "/setrebuy BTC/USDT 2 — % відкупу нижче TP (0 = вимкнено)\n"
        "/scalp BTC/USDT on|off — мікро-скальп (сітка buy/sell)\n"
        "/settick BTC/USDT 0.25 — крок сітки у %\n"
        "/setlevels BTC/USDT 3 — кількість рівнів сітки\n"
        "/slmode BTC/USDT trigger|trailing — тип SL (ринковий тригер або трейлінг)"
    )

@dp.message(Command("balance"))
async def balance_cmd(message: types.Message):
    data = await get_balance()
    if not data or not isinstance(data, dict):
        await message.answer("❌ Помилка: не вдалося отримати баланс.")
        return

    lines = []
    for asset, info in sorted(data.items()):
        try:
            available = float(info.get("available", 0))
            freeze = float(info.get("freeze", 0))
        except Exception:
            available, freeze = 0.0, 0.0
        if available > 0 or freeze > 0:
            lines.append(f"{asset}: {available} (freeze {freeze})")

    text = "💰 <b>Баланс</b>:\n" + ("\n".join(lines) if lines else "0 на всіх гаманцях")
    await message.answer(text)

@dp.message(Command("market"))
async def market_cmd(message: types.Message):
    try:
        _, market = message.text.split()
        market = market.upper().replace("/", "_")  # BTC/USDT -> BTC_USDT
        markets[market] = _normalize_market_cfg({
            "tp": None,
            "sl": None,
            "orders": [],
            "autotrade": False,
            "buy_usdt": 10,
            "chat_id": message.chat.id,
            "rebuy_pct": 0.0,
            "last_tp_price": None,
            "scalp": False,
            "tick_pct": 0.25,
            "levels": 3,
            "maker_only": True,
            "sl_mode": "trigger",
            "entry_price": None,
            "peak": None,
        })
        save_markets()
        await message.answer(f"✅ Додано ринок {market} (за замовчуванням 10 USDT)")
    except Exception:
        await message.answer("⚠️ Використання: /market BTC/USDT")

@dp.message(Command("settp"))
async def settp_cmd(message: types.Message):
    try:
        _, market, percent = message.text.split()
        market = market.upper().replace("/", "_")
        if market not in markets:
            await message.answer("❌ Спочатку додай ринок через /market.")
            return
        markets[market]["tp"] = float(percent)
        save_markets()
        await message.answer(f"📈 TP для {market}: {percent}%")
    except Exception:
        await message.answer("⚠️ Використання: /settp BTC/USDT 5")

@dp.message(Command("setsl"))
async def setsl_cmd(message: types.Message):
    try:
        _, market, percent = message.text.split()
        market = market.upper().replace("/", "_")
        if market not in markets:
            await message.answer("❌ Спочатку додай ринок через /market.")
            return
        markets[market]["sl"] = float(percent)
        save_markets()
        await message.answer(f"📉 SL для {market}: {percent}%")
    except Exception:
        await message.answer("⚠️ Використання: /setsl BTC/USDT 2")

@dp.message(Command("setbuy"))
async def setbuy_cmd(message: types.Message):
    try:
        _, market, usdt = message.text.split()
        market = market.upper().replace("/", "_")
        usdt = float(usdt)
        if usdt <= 0:
            await message.answer("⚠️ Сума повинна бути більшою за 0.")
            return
        markets[market]["buy_usdt"] = usdt
        save_markets()
        await message.answer(f"📊 Для {market} встановлено {usdt} USDT на кожну купівлю.")
    except Exception:
        await message.answer("⚠️ Використання: /setbuy BTC/USDT 30")

@dp.message(Command("setrebuy"))
async def setrebuy_cmd(message: types.Message):
    try:
        _, market, pct = message.text.split()
        market = market.upper().replace("/", "_")
        pct = float(pct)
        if market not in markets:
            await message.answer("❌ Спочатку додай ринок через /market.")
            return
        if pct < 0:
            await message.answer("⚠️ Вкажи відсоток ≥ 0. (0 вимикає відкуп нижче TP)")
            return
        markets[market]["rebuy_pct"] = pct
        save_markets()
        await message.answer(
            f"🔁 Re-buy для {market}: {pct}% нижче TP " + ("(вимкнено)" if pct == 0 else "")
        )
    except Exception:
        await message.answer("⚠️ Використання: /setrebuy BTC/USDT 2")

@dp.message(Command("scalp"))
async def scalp_cmd(message: types.Message):
    try:
        _, market, state = message.text.split()
        market = market.upper().replace("/", "_")
        if market not in markets:
            return await message.answer("❌ Спочатку додай ринок через /market.")
        markets[market]["scalp"] = (state.lower() == "on")
        # при перемиканні скидаємо прапор, щоб дозволити одноразовий сид
        markets[market]["scalp_seeded"] = False
        save_markets()
        await message.answer(f"⚙️ SCALP для {market}: {state.upper()}")
    except Exception:
        await message.answer("⚠️ Використання: /scalp BTC/USDT on|off")

@dp.message(Command("settick"))
async def settick_cmd(message: types.Message):
    try:
        _, market, pct = message.text.split()
        market = market.upper().replace("/", "_")
        if market not in markets:
            return await message.answer("❌ Спочатку додай ринок через /market.")
        markets[market]["tick_pct"] = float(pct)
        save_markets()
        await message.answer(f"📏 Tick для {market}: {pct}%")
    except Exception:
        await message.answer("⚠️ Використання: /settick BTC/USDT 0.25")

@dp.message(Command("setlevels"))
async def setlevels_cmd(message: types.Message):
    try:
        _, market, n = message.text.split()
        market = market.upper().replace("/", "_")
        if market not in markets:
            return await message.answer("❌ Спочатку додай ринок через /market.")
        markets[market]["levels"] = max(1, int(n))
        save_markets()
        await message.answer(f"🪜 Levels для {market}: {n}")
    except Exception:
        await message.answer("⚠️ Використання: /setlevels BTC/USDT 3")

@dp.message(Command("slmode"))
async def slmode_cmd(message: types.Message):
    try:
        _, market, mode = message.text.split()
        market = market.upper().replace("/", "_")
        if market not in markets:
            return await message.answer("❌ Спочатку додай ринок через /market.")
        mode = mode.lower()
        if mode not in ("trigger", "trailing"):
            return await message.answer("⚠️ slmode: trigger|trailing")
        markets[market]["sl_mode"] = mode
        save_markets()
        await message.answer(f"🛡️ SL mode для {market}: {mode}")
    except Exception:
        await message.answer("⚠️ Використання: /slmode BTC/USDT trigger|trailing")

@dp.message(Command("autotrade"))
async def autotrade_cmd(message: types.Message):
    try:
        _, market, state = message.text.split()
        market = market.upper().replace("/", "_")
        state = state.strip().lower()
        if market not in markets:
            await message.answer("❌ Спочатку додай ринок через /market.")
            return
        if state not in ("on", "off"):
            await message.answer("⚠️ Використання: /autotrade BTC/USDT on|off")
            return
        markets[market]["autotrade"] = (state == "on")
        save_markets()
        await message.answer(
            f"{'✅' if markets[market]['autotrade'] else '⏹️'} Autotrade для {market}: {state.upper()}"
        )
    except Exception:
        await message.answer("⚠️ Використання: /autotrade BTC/USDT on|off")

@dp.message(Command("status"))
async def status_cmd(message: types.Message):
    if not markets:
        await message.answer("ℹ️ Активних ринків немає.")
        return
    text = "📊 <b>Статус</b>:\n"
    for m, cfg in markets.items():
        tp = f"{cfg['tp']}%" if cfg.get("tp") is not None else "—"
        sl = f"{cfg['sl']}%" if cfg.get("sl") is not None else "—"
        text += (
            f"\n{m}:\n"
            f" TP: {tp}\n"
            f" SL: {sl}\n"
            f" Buy: {cfg['buy_usdt']} USDT\n"
            f" Автотрейд: {cfg['autotrade']}\n"
            f" Rebuy: {cfg.get('rebuy_pct', 0)}%\n"
            f" Ордерів: {len(cfg.get('orders', []))}\n"
        )
    await message.answer(text)

@dp.message(Command("orders"))
async def orders_cmd(message: types.Message):
    try:
        _, market = message.text.split()
        market = market.upper().replace("/", "_")
    except Exception:
        await message.answer("⚠️ Використання: /orders BTC/USDT")
        return

    data = await active_orders(market)
    lst = data.get("orders", []) if isinstance(data, dict) else []
    if not lst:
        await message.answer(f"ℹ️ Для {market} активних ордерів немає.")
        return

    lines = []
    for o in lst:
        try:
            oid = o.get("orderId") or o.get("id")
            side = o.get("side")
            typ  = o.get("type")
            price = o.get("price")
            amount = o.get("amount")
            lines.append(f"#{oid}: {side}/{typ} price={price} amount={amount}")
        except Exception:
            continue

    await message.answer("📄 <b>Активні ордери</b>:\n" + "\n".join(lines))

@dp.message(Command("cancel"))
async def cancel_cmd(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("⚠️ Використання: /cancel BTC/USDT [orderId|all]")
        return
    market = parts[1].upper().replace("/", "_")
    target = parts[2].lower() if len(parts) >= 3 else None

    if target == "all":
        data = await active_orders(market)
        lst = data.get("orders", []) if isinstance(data, dict) else []
        cnt = 0
        for o in lst:
            oid_raw = o.get("orderId") or o.get("id")
            try:
                oid = int(str(oid_raw))
            except Exception:
                oid = None
            if oid:
                res = await cancel_order(market, order_id=oid)
                if isinstance(res, dict) and res.get("success") is not False:
                    cnt += 1
        await message.answer(f"🧹 Скасовано {cnt} ордер(и/ів) на {market}.")
        return

    if target and target.isdigit():
        res = await cancel_order(market, order_id=int(target))
        ok = isinstance(res, dict) and res.get("success") is not False
        await message.answer("✅ Скасовано." if ok else f"❌ Не вдалось скасувати #{target}.")
    else:
        await message.answer("⚠️ Використання: /cancel BTC/USDT 123456 або /cancel BTC/USDT all")

VERSION = "v4.1.2-scalpfix"
@dp.message(Command("version"))
async def version_cmd(message: types.Message):
    await message.answer(f"🤖 Bot version: {VERSION}")

# ---------------- TRADE LOGIC ----------------
def _extract_order_id(resp: dict) -> Optional[int]:
    if not isinstance(resp, dict):
        return None
    if "orderId" in resp:
        try:
            return int(resp["orderId"])
        except Exception:
            return None
    if "id" in resp:
        try:
            return int(resp["id"])
        except Exception:
            return None
    return None

# >>> REBUY FEATURE: допоміжна функція виставити лімітний BUY на знижці від довідкової ціни
async def place_limit_buy_at_discount(market: str, cfg: dict, ref_price: float) -> Optional[int]:
    try:
        pct = float(cfg.get("rebuy_pct", 0) or 0)
    except Exception:
        pct = 0.0
    if pct <= 0 or not ref_price or ref_price <= 0:
        return None

    target_price = float(quantize_price(market, ref_price * (1 - pct / 100.0)))
    spend = Decimal(str(cfg.get("buy_usdt", 10)))
    spend_adj = (spend * Decimal("0.998")).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if float(spend_adj) <= 0:
        return None

    # amount у BASE = USDT / price
    base_amount = quantize_amount(market, float(spend_adj) / target_price)
    if base_amount <= 0:
        base_amount = step_from_precision(get_rules(market)["amount_precision"])

    # Доводимо до мінімумів біржі (ceil!)
    base_amount, _ = ensure_minima_for_order(
        market, side="buy", price=float(target_price),
        amount_base=base_amount, amount_quote=None
    )

    cid = f"wb-{market}-rebuy-{now_ms()}"
    res = await place_limit_order(
        market, "buy", target_price, float(base_amount),
        client_order_id=cid, post_only=True
    )
    oid = _extract_order_id(res)
    if oid:
        cfg.setdefault("orders", []).append({"id": oid, "cid": cid, "type": "rebuy", "market": market})
        save_markets()
    return oid

def _pp(market: str, cfg: dict) -> tuple[float, int]:
    return float(cfg.get("tick_pct", 0.25)), int(cfg.get("levels", 3))

async def _place_maker_limit(market, side, price, amount, tag):
    oid = _extract_order_id(
        await place_limit_order(market, side, price, amount, client_order_id=tag, post_only=True)
    )
    return oid

async def seed_scalp_grid(market: str, cfg: dict, ref_price: float):
    tick, levels = _pp(market, cfg)
    spend = Decimal(str(cfg.get("buy_usdt", 5)))
    base_av = await get_base_available(market)
    ap = step_from_precision(get_rules(market)["amount_precision"])
    # BUY-сітка
    for i in range(1, levels + 1):
        p = float(quantize_price(market, ref_price * (1 - (tick * i) / 100)))
        amt = quantize_amount(market, float((spend / Decimal(str(p)))))
        if amt <= 0:
            amt = ap
        tag = f"wb-{market}-scalp-buy-{i}-{now_ms()}"
        oid = await _place_maker_limit(market, "buy", p, float(amt), tag)
        if oid:
            cfg.setdefault("orders", []).append({"id": oid, "type": "scalp_buy", "market": market, "price": p, "amount": float(amt)})
    # SELL-сітка (якщо є холдинги)
    if base_av > 0:
        portion = (base_av / Decimal(max(1, levels))).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        portion = quantize_amount(market, float(portion))
        if portion > 0:
            for i in range(1, levels + 1):
                p = float(quantize_price(market, ref_price * (1 + (tick * i) / 100)))
                tag = f"wb-{market}-scalp-sell-{i}-{now_ms()}"
                oid = await _place_maker_limit(market, "sell", p, float(portion), tag)
                if oid:
                    cfg["orders"].append({"id": oid, "type": "scalp_sell", "market": market, "price": p, "amount": float(portion)})
    # відмічаємо одноразовий сид + таймштамп
    cfg["scalp_seeded"] = True
    cfg["last_seed_at"] = now_ms()
    save_markets()

async def on_fill_pingpong(market: str, cfg: dict, filled: dict):
    tick, _ = _pp(market, cfg)
    typ = filled.get("type")
    try:
        price = float(filled.get("price") or 0)
        amt = float(filled.get("amount") or 0)
    except Exception:
        return
    if price <= 0 or amt <= 0:
        return
    if typ == "scalp_buy":
        cfg["entry_price"] = price
        p_out = float(quantize_price(market, price * (1 + tick / 100)))
        tag = f"wb-{market}-pp-sell-{now_ms()}"
        oid = await _place_maker_limit(market, "sell", p_out, amt, tag)
        if oid:
            cfg["orders"].append({"id": oid, "type": "scalp_sell", "market": market, "price": p_out, "amount": amt})
    elif typ == "scalp_sell":
        p_in = float(quantize_price(market, price * (1 - tick / 100)))
        spend = Decimal(str(cfg.get("buy_usdt", 5)))
        usdt = await get_usdt_available()
        amt_in = amt if usdt * Decimal("0.999") >= spend else quantize_amount(market, float(spend / Decimal(str(p_in))))
        tag = f"wb-{market}-pp-buy-{now_ms()}"
        oid = await _place_maker_limit(market, "buy", p_in, float(amt_in), tag)
        if oid:
            cfg["orders"].append({"id": oid, "type": "scalp_buy", "market": market, "price": p_in, "amount": float(amt_in)})
    save_markets()

async def start_new_trade(market: str, cfg: dict):
    # 1) Баланс до
    balances_before = await get_balance()
    usdt_av = (balances_before.get("USDT") or {}).get("available", 0)
    try:
        usdt = float(usdt_av)
    except Exception:
        usdt = 0.0

    spend = float(cfg.get("buy_usdt", 10.0))

    # >>> Перевіряємо мінімальну суму для ринку (min_total)
    _, spend_dec = ensure_minima_for_order(market, "buy", price=None,
                                           amount_base=None, amount_quote=Decimal(str(spend)))
    spend = float(spend_dec)

    if usdt < spend:
        logging.warning(f"Недостатньо USDT для {market}. Є {usdt}, треба {spend}.")
        return

    # 2) Поточна ціна
    last_price = await get_last_price(market)
    if not last_price or last_price <= 0:
        logging.error(f"Не вдалося отримати last_price для {market}.")
        return

    # 3) Маркет-купівля
    buy_res = await place_market_order(market, "buy", spend)
    if not isinstance(buy_res, dict) or (buy_res.get("success") is False):
        logging.error(f"Помилка купівлі: {buy_res}")
        return
    logging.info(f"BUY placed: {buy_res}")

    # 4) Баланс після — фактично куплена базова кількість
    balances_after = await get_balance()

    def _f(v):
        try:
            return float(v)
        except:
            return 0.0

    base_symbol = base_symbol_from_market(market)
    base_before = _f((balances_before.get(base_symbol) or {}).get("available", 0))
    base_after  = _f((balances_after.get(base_symbol)  or {}).get("available", 0))
    base_amount = round(max(base_after - base_before, 0.0), 8)

    if base_amount <= 0:
        base_amount = round(spend / last_price, 8)
    if base_amount <= 0:
        logging.error(f"Нульовий обсяг базової монети після купівлі: spend={spend}, price={last_price}")
        return

    # >>> референти для SL (trigger/trailing)
    cfg["entry_price"] = float(last_price)
    cfg["peak"] = float(last_price)

    # 5) Створення лише TP (SL як ліміт не ставимо — SL зробить монітор ринковим)
    cfg["orders"] = []
    ts = now_ms()

    if cfg.get("tp"):
        tp_price = float(quantize_price(market, last_price * (1 + float(cfg["tp"]) / 100)))
        cfg["last_tp_price"] = tp_price
        cid = f"wb-{market}-tp-{ts}"
        tp_order = await place_limit_order(market, "sell", tp_price, base_amount, client_order_id=cid)
        oid = _extract_order_id(tp_order)
        if oid:
            cfg["orders"].append({"id": oid, "cid": cid, "type": "tp", "market": market})

    save_markets()

# --- NEW: старт TP/SL від уже наявних монет (без купівлі) ---
async def place_tp_sl_from_holdings(market: str, cfg: dict) -> bool:
    last_price = await get_last_price(market)
    if not last_price or last_price <= 0:
        logging.error(f"[HOLDINGS] Не вдалося отримати last_price для {market}.")
        return False

    # референти для SL trigger/trailing
    cfg["entry_price"] = float(last_price)
    cfg["peak"] = float(last_price)

    base_av = await get_base_available(market)
    # буфер 0.5% від холдингів + квантизація до кроку
    safe_amount = (base_av * Decimal("0.995")).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    safe_amount = quantize_amount(market, float(safe_amount))

    if safe_amount <= 0:
        logging.info(f"[HOLDINGS] Немає базового балансу для {market}. base_av={base_av}")
        return False

    cfg["orders"] = []
    ts = now_ms()

    # --- TP тільки якщо проходить мінімалки
    if cfg.get("tp"):
        tp_price = float(quantize_price(market, float(last_price) * (1 + float(cfg["tp"]) / 100)))
        cfg["last_tp_price"] = tp_price
        rules = get_rules(market)
        min_total = rules.get("min_total")
        can_place_tp = True
        if min_total:
            est_total = Decimal(str(tp_price)) * Decimal(str(safe_amount))
            if est_total < min_total:
                can_place_tp = False
                logging.warning(f"[HOLDINGS-TP] {market}: safe_amount*TP({tp_price}) < min_total ({min_total}). Пропускаю TP.")
        if can_place_tp:
            cid = f"wb-{market}-tp-{ts}"
            tp_order = await place_limit_order(market, "sell", tp_price, float(safe_amount), client_order_id=cid)
            oid = _extract_order_id(tp_order)
            if oid:
                cfg["orders"].append({"id": oid, "cid": cid, "type": "tp", "market": market})

    # SL-ліміт НЕ ставимо — зробить монітор ринком при тригері
    save_markets()
    created = len(cfg.get("orders", [])) > 0
    if created:
        logging.info(f"[HOLDINGS] Для {market} створений TP від холдингів: {cfg['orders']}")
    else:
        logging.warning(f"[HOLDINGS] Не вдалося створити TP для {market}.")
    return created

@dp.message(Command("buy"))
async def buy_cmd(message: types.Message):
    try:
        _, market = message.text.split()
        market = market.upper().replace("/", "_")
        if market not in markets:
            await message.answer("❌ Спочатку додай ринок через /market.")
            return
        await start_new_trade(market, markets[market])
        await message.answer(f"✅ Купівля {market} виконана на {markets[market]['buy_usdt']} USDT.")
    except Exception:
        await message.answer("⚠️ Використання: /buy BTC/USDT")

@dp.message(Command("removemarket"))
async def removemarket_cmd(message: types.Message):
    try:
        _, market = message.text.split()
        market = market.upper().replace("/", "_")
        if market in markets:
            del markets[market]
            save_markets()
            await message.answer(f"🗑️ Видалено {market}")
        else:
            await message.answer("❌ Ринок не знайдено.")
    except Exception:
        await message.answer("⚠️ Використання: /removemarket BTC/USDT")

@dp.message(Command("stop"))
async def stop_cmd(message: types.Message):
    markets.clear()
    save_markets()
    await message.answer("⏹️ Торгівлю зупинено. Всі ринки очищено.")

@dp.message(Command("restart"))
async def restart_cmd(message: types.Message):
    for m in markets:
        markets[m]["orders"] = []
        markets[m]["scalp_seeded"] = False
    save_markets()
    await message.answer("🔄 Логіку перезапущено.")

# ---------------- MONITOR ----------------
async def monitor_orders():
    """
    Частий монітор: 2с.
    Логіка:
      - Trigger/Trailing SL: якщо ціна <= поріг — скасувати ліміти і продати ринком.
      - Якщо ордер закрився: прибрати пару, зробити ребай після TP (опційно), або ping-pong для скальпу.
      - Autostart: якщо пусто — спробувати старт від холдингів; якщо нема — купівля за USDT;
        якщо увімкнено scalp — сформувати сітку.
    """
    while True:
        try:
            for market, cfg in list(markets.items()):
                # --- HARD/TRAILING SL ---
                try:
                    sl_pct = float(cfg.get("sl") or 0)
                except Exception:
                    sl_pct = 0.0
                if sl_pct > 0:
                    lp = await get_last_price(market)
                    if lp:
                        mode = (cfg.get("sl_mode") or "trigger").lower()
                        if mode == "trailing":
                            peak = float(cfg.get("peak") or 0)
                            if lp > (peak or 0):
                                cfg["peak"] = lp
                                save_markets()
                        threshold = None
                        if mode == "trigger" and cfg.get("entry_price"):
                            threshold = float(cfg["entry_price"]) * (1 - sl_pct / 100)
                        elif mode == "trailing" and cfg.get("peak"):
                            threshold = float(cfg["peak"]) * (1 - sl_pct / 100)
                        if threshold and lp <= threshold:
                            acts = await active_orders(market)
                            for o in acts.get("orders", []):
                                oid = o.get("orderId") or o.get("id")
                                if oid:
                                    await cancel_order(market, order_id=int(oid))
                            cfg["orders"].clear()
                            save_markets()
                            base_av = await get_base_available(market)
                            if base_av > 0:
                                await place_market_order(market, "sell", float(base_av))
                                if cfg.get("chat_id") and can_notify(cfg, "sl_msg", 10):
                                    await bot.send_message(cfg["chat_id"], f"🛑 {market}: SL спрацював, продано ринком.")
                            cfg["entry_price"] = None
                            cfg["peak"] = None
                            cfg["scalp_seeded"] = False
                            save_markets()
                            continue  # до наступного ринку

                # --- активні ордери ---
                act = await active_orders(market)
                active_ids = set()
                if isinstance(act, dict):
                    orders_list = act.get("orders") if isinstance(act.get("orders"), list) else None
                    if orders_list:
                        for o in orders_list:
                            oid = None
                            if isinstance(o, dict):
                                oid_raw = o.get("orderId") or o.get("id")
                                try:
                                    oid = int(str(oid_raw))
                                except Exception:
                                    oid = None
                            if oid is not None:
                                active_ids.add(oid)

                finished_any = None
                for entry in list(cfg.get("orders", [])):
                    if entry["id"] not in active_ids:
                        finished_any = entry
                        break

                if finished_any:
                    chat_id = cfg.get("chat_id")
                    if chat_id and can_notify(cfg, "filled_msg", 2):
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"✅ Ордер {finished_any['id']} ({market}, {finished_any['type']}) закрито!"
                        )
                    # скасувати інші з пари
                    for entry in list(cfg["orders"]):
                        if entry["id"] != finished_any["id"]:
                            await cancel_order(market, order_id=entry["id"])
                    cfg["orders"].clear()
                    # дозволити новий одноразовий сид при наступному циклі
                    cfg["scalp_seeded"] = False
                    save_markets()

                    # REBUY/рестарт логіка
                    handled = False
                    if cfg.get("autotrade"):
                        if finished_any.get("type") == "tp" and float(cfg.get("rebuy_pct", 0) or 0) > 0:
                            ref = cfg.get("last_tp_price") or (await get_last_price(market))
                            oid = await place_limit_buy_at_discount(market, cfg, float(ref or 0))
                            if oid:
                                if chat_id and can_notify(cfg, "rebuy_msg", 5):
                                    await bot.send_message(
                                        chat_id=chat_id,
                                        text=f"🔻 {market}: лімітний відкуп на {cfg['rebuy_pct']}% нижче TP виставлено (order {oid})"
                                    )
                                handled = True
                        elif finished_any.get("type") == "rebuy":
                            ok = await place_tp_sl_from_holdings(market, cfg)
                            if ok and chat_id and can_notify(cfg, "after_rebuy_tp", 5):
                                await bot.send_message(
                                    chat_id=chat_id,
                                    text=f"🎯 {market}: після відкупу виставлено TP від холдингів"
                                )
                                handled = True

                        # >>> ping-pong для скальпу
                        if cfg.get("scalp") and str(finished_any.get("type", "")).startswith("scalp"):
                            await on_fill_pingpong(market, cfg, finished_any)
                            handled = True

                        if not handled:
                            if chat_id and can_notify(cfg, "autotrade_new", 5):
                                await bot.send_message(
                                    chat_id=chat_id,
                                    text=f"♻️ Автотрейд {market}: нова угода на {cfg['buy_usdt']} USDT"
                                )
                            await start_new_trade(market, cfg)

                # --- АВТОСТАРТ / FALLBACK / SCALP GRID ---
                if cfg.get("autotrade"):
                    no_tracked = len(cfg.get("orders", [])) == 0
                    no_active = (len(active_ids) == 0)
                    if no_tracked and no_active:
                        # якщо увімкнено скальп — спочатку сформуємо сітку (разово + кулдаун)
                        if cfg.get("scalp"):
                            lp = await get_last_price(market)
                            cooldown_ok = (now_ms() - int(cfg.get("last_seed_at", 0))) > int(cfg.get("seed_cooldown_s", 30)) * 1000
                            if lp and (not cfg.get("scalp_seeded", False) or cooldown_ok):
                                await seed_scalp_grid(market, cfg, lp)
                                if cfg.get("chat_id") and can_notify(cfg, "seed_msg", 10):
                                    await bot.send_message(cfg["chat_id"], f"▶️ {market}: запущено мікро-скальп сітку")
                                continue
                        # 1) старт від холдингів
                        started_from_holdings = await place_tp_sl_from_holdings(market, cfg)
                        if started_from_holdings:
                            if cfg.get("chat_id") and can_notify(cfg, "start_from_holdings", 10):
                                await bot.send_message(
                                    cfg["chat_id"], f"▶️ {market}: старт від наявних монет (TP виставлено)"
                                )
                        else:
                            # 2) fallback: купівля за USDT
                            usdt = await get_usdt_available()
                            spend = Decimal(str(cfg.get("buy_usdt", 10)))
                            spend_adj = (spend * Decimal("0.998")).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                            if usdt >= spend_adj and float(spend_adj) > 0:
                                if cfg.get("chat_id") and can_notify(cfg, "autostart_buy", 10):
                                    await bot.send_message(
                                        cfg["chat_id"],
                                        text=f"▶️ {market}: автостарт купівлі на {spend_adj} USDT (бо холдингів немає)"
                                    )
                                await start_new_trade(market, cfg)
                            else:
                                logging.info(f"[AUTOSTART SKIP] {market}: ні холдингів, ні достатньо USDT (USDT={usdt}, need≈{spend_adj})")

        except Exception as e:
            logging.error(f"Monitor error: {e}")

        await asyncio.sleep(2)  # було 10

# ---------------- RUN ----------------
async def main():
    load_markets()
    await load_market_rules()  # <- завантажуємо правила ринків на старті
    logging.info("🚀 Bot is running and waiting for commands...")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("✅ Webhook очищено успішно")
    except Exception as e:
        logging.error(f"❌ Помилка очищення webhook: {e}")

    asyncio.create_task(monitor_orders())
    import aiohttp

async def ensure_single_instance():
    try:
        async with aiohttp.ClientSession() as session:
            # Telegram API test
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
            async with session.get(url) as resp:
                if resp.status == 200:
                    logging.info("✅ Telegram API reachable, safe to start polling")
                else:
                    logging.warning(f"⚠️ Telegram returned {resp.status}, waiting...")
    except Exception as e:
        logging.warning(f"⚠️ Delay before polling due to {e}")
        await asyncio.sleep(5)

# Виклик перед запуском polling:
await ensure_single_instance()
await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    try:
        print("✅ main.py started")
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Bot stopped manually")
