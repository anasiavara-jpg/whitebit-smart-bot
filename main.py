# main.py — WhiteBIT Smart Bot (v4-ready, clean + market rules/precision + holdings autostart + rebuy-after-TP)
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
from decimal import Decimal, ROUND_DOWN

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

# WhiteBIT base (важливо: без /api/v4 у BASE_URL)
BASE_URL = "https://whitebit.com"
MARKETS_FILE = "markets.json"
markets: Dict[str, Dict[str, Any]] = {}

# Кеш правил ринків (price/amount precision, min тощо)
market_rules: Dict[str, Dict[str, Any]] = {}

# ---------------- JSON SAVE/LOAD ----------------
def save_markets():
    try:
        with open(MARKETS_FILE, "w", encoding="utf-8") as f:
            json.dump(markets, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Помилка збереження markets.json: {e}")

def load_markets():
    global markets
    if os.path.exists(MARKETS_FILE):
        try:
            with open(MARKETS_FILE, "r", encoding="utf-8") as f:
                markets = json.load(f)
        except Exception as e:
            logging.error(f"Помилка завантаження markets.json: {e}")
            markets = {}
    else:
        markets = {}
        save_markets()

# ---------------- TIME/HELPERS ----------------
def now_ms() -> int:
    return int(time.time() * 1000)

def _payload_and_headers(path: str, extra_body: Optional[dict] = None) -> tuple[bytes, dict]:
    """
    WhiteBIT v4 auth:
      body JSON містить: request (повний шлях), nonce (ms), + дод.поля
      X-TXC-PAYLOAD = base64(body_bytes)
      X-TXC-SIGNATURE = hex(HMAC_SHA512(payload_b64, API_SECRET))
    """
    body = {"request": path, "nonce": now_ms()}
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

# ---------------- MARKET RULES (fix: use /public/markets, fallback) ----------------
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

            # можливі варіанти ключів
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
        # основний запит
        data = await public_get("/api/v4/public/markets")
        if isinstance(data, list) and data:
            market_rules = _parse_list(data)
            logging.info(f"Loaded market rules from /markets for {len(market_rules)} symbols")
            return

        # страховка: деякі інсталяції мають /public/symbols
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
    """
    Повертає precision та min-обмеження для ринку.
    Якщо в кеші нема — дефолт: 6 знаків.
    """
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

def quantize_amount(market: str, amount: float) -> Decimal:
    """
    Округляє КІЛЬКІСТЬ (BASE) до кроку біржі вниз.
    """
    rules = get_rules(market)
    step = step_from_precision(rules["amount_precision"])
    return (Decimal(str(amount)) // step) * step

def quantize_price(market: str, price: float) -> Decimal:
    """
    Округляє ЦІНУ (QUOTE) до кроку біржі вниз.
    """
    rules = get_rules(market)
    step = step_from_precision(rules["price_precision"])
    return (Decimal(str(price)) // step) * step

def ensure_minima_for_order(market: str, side: str, price: Optional[float],
                            amount_base: Optional[Decimal], amount_quote: Optional[Decimal]):
    """
    Повертає (amount_base, amount_quote) з урахуванням мінімалок:
      - min_amount (BASE)
      - min_total  (QUOTE = price * amount_base)

    Для MARKET BUY керуємось amount_quote (price відсутня).
    Для LIMIT (buy/sell) — перевіряємо і amount, і total (бо price відома).
    ДОДАНО: для MARKET SELL (price відсутня) — застосовуємо лише min_amount.
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

    # MARKET SELL: price немає, але треба дотриматись min_amount
    if side_l == "sell" and price is None and amount_base is not None:
        if min_amount and amount_base < min_amount:
            amount_base = ((min_amount // ap) * ap) if min_amount > 0 else ap
        return (amount_base, amount_quote)

    # SELL або LIMIT BUY (price відома): перевіряємо min_amount і min_total
    if price and amount_base is not None:
        if min_amount and amount_base < min_amount:
            amount_base = ((min_amount // ap) * ap) if min_amount > 0 else ap

        if min_total:
            total = Decimal(str(price)) * amount_base
            if total < min_total:
                need_base = (min_total / Decimal(str(price)))
                need_base = (need_base // ap) * ap
                if need_base <= 0:
                    need_base = ap
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
        rules = get_rules(market)
        quote_step = step_from_precision(rules["price_precision"])
        q_amount = (Decimal(str(amount)) // quote_step) * quote_step
        if q_amount <= 0:
            q_amount = quote_step

        # >>> Перевіряємо мінімальні значення (min_total)
        _, q_amount = ensure_minima_for_order(market, "buy", price=None,
                                              amount_base=None, amount_quote=q_amount)

        body["amount"] = float(q_amount)

    else:
        a = quantize_amount(market, amount)
        if a <= 0:
            a = step_from_precision(get_rules(market)["amount_precision"])

        # >>> Перевіряємо мінімальні значення (min_amount)
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

    # >>> Перевіряємо мінімальні значення (min_total, min_amount)
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
    if stp:
        body["stp"] = stp

    return await private_post("/api/v4/order/new", body)

async def active_orders(market: Optional[str] = None) -> dict:
    body = {}
    if market:
        body["market"] = market
    return await private_post("/api/v4/orders", body)

async def cancel_order(market: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> dict:
    body = {"market": market}
    if client_order_id:
        body["clientOrderId"] = str(client_order_id)
    elif order_id is not None:
        body["orderId"] = str(order_id)
    else:
        return {"success": False, "message": "Потрібно вказати order_id або client_order_id"}
    return await private_post("/api/v4/order/cancel", body)

# ---------------- PUBLIC TICKER ----------------
async def get_last_price(market: str) -> Optional[float]:
    t = await public_get("/api/v4/public/ticker")
    try:
        lp = t.get(market, {}).get("last_price")
        return float(lp) if lp is not None else None
    except Exception:
        logging.error(f"Не вдалося взяти last_price для {market}: {t}")
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
        "/setrebuy BTC/USDT 2 — % відкупу нижче TP (0 = вимкнено)"
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
        markets[market] = {
            "tp": None,
            "sl": None,
            "orders": [],
            "autotrade": False,
            "buy_usdt": 10,
            "chat_id": message.chat.id,
            "rebuy_pct": 0.0,          # >>> REBUY FEATURE: % нижче TP для лімітного buy
            "last_tp_price": None,     # >>> REBUY FEATURE: остання ціна TP, щоб знати від чого відраховувати
        }
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

# >>> REBUY FEATURE: команда налаштування % відкупу нижче TP
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
            oid = o.get("orderId") or o.get("id")
            if oid:
                res = await cancel_order(market, order_id=int(oid))
                if isinstance(res, dict) and res.get("success") is not False:
                    cnt += 1
        await message.answer(f"🧹 Скасовано {cnt} ордер(и/ів) на {market}.")
        return

    # конкретний orderId
    if target and target.isdigit():
        res = await cancel_order(market, order_id=int(target))
        ok = isinstance(res, dict) and res.get("success") is not False
        await message.answer("✅ Скасовано." if ok else f"❌ Не вдалось скасувати #{target}.")
    else:
        await message.answer("⚠️ Використання: /cancel BTC/USDT 123456 або /cancel BTC/USDT all")
VERSION = "v4.1-rebuy-fix+rules+retry"
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
    # трохи нижче, щоб влізти у кроки
    spend_adj = (spend * Decimal("0.998")).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    if float(spend_adj) <= 0:
        return None

    # amount у BASE = USDT / price
    base_amount = quantize_amount(market, float(spend_adj) / target_price)
    if base_amount <= 0:
        base_amount = step_from_precision(get_rules(market)["amount_precision"])

    cid = f"wb-{market}-rebuy-{now_ms()}"
    res = await place_limit_order(market, "buy", target_price, float(base_amount), client_order_id=cid, post_only=True)
    oid = _extract_order_id(res)
    if oid:
        cfg.setdefault("orders", []).append({"id": oid, "cid": cid, "type": "rebuy", "market": market})
        save_markets()
    return oid

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

    # 5) Створення TP/SL як окремих лімітів
    cfg["orders"] = []
    ts = now_ms()

    if cfg.get("tp"):
        tp_price = float(quantize_price(market, last_price * (1 + float(cfg["tp"]) / 100)))
        cfg["last_tp_price"] = tp_price  # >>> REBUY FEATURE: запам'ятовуємо TP ціну
        cid = f"wb-{market}-tp-{ts}"
        tp_order = await place_limit_order(market, "sell", tp_price, base_amount, client_order_id=cid, stp="cancel_new")
        oid = _extract_order_id(tp_order)
        if oid:
            cfg["orders"].append({"id": oid, "cid": cid, "type": "tp", "market": market})

    if cfg.get("sl"):
        sl_price = float(quantize_price(market, last_price * (1 - float(cfg["sl"]) / 100)))
        cid = f"wb-{market}-sl-{ts}"
        sl_order = await place_limit_order(market, "sell", sl_price, base_amount, client_order_id=cid, stp="cancel_new")
        oid = _extract_order_id(sl_order)
        if oid:
            cfg["orders"].append({"id": oid, "cid": cid, "type": "sl", "market": market})

    save_markets()

# --- NEW: старт TP/SL від уже наявних монет (без купівлі) ---
async def place_tp_sl_from_holdings(market: str, cfg: dict) -> bool:
    last_price = await get_last_price(market)
    if not last_price or last_price <= 0:
        logging.error(f"[HOLDINGS] Не вдалося отримати last_price для {market}.")
        return False

    base_av = await get_base_available(market)
    # буфер 0.5% від холдингів + квантизація до кроку
    safe_amount = (base_av * Decimal("0.995")).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    safe_amount = quantize_amount(market, float(safe_amount))

    if safe_amount <= 0:
        logging.info(f"[HOLDINGS] Немає базового балансу для {market}. base_av={base_av}")
        return False

    cfg["orders"] = []
    ts = now_ms()

    if cfg.get("tp"):
        tp_price = float(quantize_price(market, float(last_price) * (1 + float(cfg["tp"]) / 100)))
        cfg["last_tp_price"] = tp_price  # >>> REBUY FEATURE: запам'ятовуємо TP ціну
        cid = f"wb-{market}-tp-{ts}"
        tp_order = await place_limit_order(market, "sell", tp_price, float(safe_amount), client_order_id=cid, stp="cancel_new")
        oid = _extract_order_id(tp_order)
        if oid:
            cfg["orders"].append({"id": oid, "cid": cid, "type": "tp", "market": market})

    if cfg.get("sl"):
        sl_price = float(quantize_price(market, float(last_price) * (1 - float(cfg["sl"]) / 100)))
        cid = f"wb-{market}-sl-{ts}"
        sl_order = await place_limit_order(market, "sell", sl_price, float(safe_amount), client_order_id=cid, stp="cancel_new")
        oid = _extract_order_id(sl_order)
        if oid:
            cfg["orders"].append({"id": oid, "cid": cid, "type": "sl", "market": market})

    save_markets()
    created = len(cfg.get("orders", [])) > 0
    if created:
        logging.info(f"[HOLDINGS] Для {market} створені ордери з наявних монет: {cfg['orders']}")
    else:
        logging.warning(f"[HOLDINGS] Не вдалося створити TP/SL для {market}.")
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
    save_markets()
    await message.answer("🔄 Логіку перезапущено.")

# ---------------- MONITOR ----------------
async def monitor_orders():
    """
    Кожні 10с перевіряємо активні ордери.
    Якщо один із пари TP/SL закрився — відміняємо інший і (якщо autotrade) перезапускаємо цикл.
    Також, якщо autotrейд ON і немає активних/відстежуваних ордерів — автозапуск:
      1) спроба старту від наявних монет (TP/SL без купівлі),
      2) якщо холдингів нема — fallback на купівлю за USDT.
      3) >>> REBUY FEATURE: після TP можемо ставити лімітний відкуп нижче TP
    """
    while True:
        try:
            for market, cfg in list(markets.items()):
                act = await active_orders(market)
                active_ids = set()
                if isinstance(act, dict):
                    orders_list = act.get("orders") if isinstance(act.get("orders"), list) else None
                    if orders_list:
                        for o in orders_list:
                            oid = None
                            if isinstance(o, dict):
                                if "orderId" in o:
                                    oid = int(str(o["orderId"]))
                                elif "id" in o:
                                    oid = int(str(o["id"]))
                            if oid is not None:
                                active_ids.add(oid)

                finished_any = None
                for entry in list(cfg.get("orders", [])):
                    if entry["id"] not in active_ids:
                        finished_any = entry
                        break

                if finished_any:
                    await bot.send_message(
                        chat_id=cfg.get("chat_id", 0) or 0,
                        text=f"✅ Ордер {finished_any['id']} ({market}, {finished_any['type']}) закрито!"
                    )
                    # Cкасувати інші з пари (як і раніше)
                    for entry in list(cfg["orders"]):
                        if entry["id"] != finished_any["id"]:
                            await cancel_order(market, order_id=entry["id"])
                    cfg["orders"].clear()
                    save_markets()

                    # >>> REBUY FEATURE: умовна логіка після закриття
                    handled = False
                    if cfg.get("autotrade"):
                        if finished_any.get("type") == "tp" and float(cfg.get("rebuy_pct", 0) or 0) > 0:
                            ref = cfg.get("last_tp_price") or (await get_last_price(market))
                            oid = await place_limit_buy_at_discount(market, cfg, float(ref or 0))
                            if oid:
                                await bot.send_message(
                                    chat_id=cfg.get("chat_id", 0) or 0,
                                    text=f"🔻 {market}: лімітний відкуп на {cfg['rebuy_pct']}% нижче TP виставлено (order {oid})"
                                )
                                handled = True
                        elif finished_any.get("type") == "rebuy":
                            # купівля відбулась по ліміту — ставимо TP/SL від холдингів
                            ok = await place_tp_sl_from_holdings(market, cfg)
                            if ok:
                                await bot.send_message(
                                    chat_id=cfg.get("chat_id", 0) or 0,
                                    text=f"🎯 {market}: після відкупу виставлено TP/SL від холдингів"
                                )
                                handled = True

                        if not handled:
                            # стара поведінка (ринковий рестарт циклу)
                            await bot.send_message(
                                chat_id=cfg.get("chat_id", 0) or 0,
                                text=f"♻️ Автотрейд {market}: нова угода на {cfg['buy_usdt']} USDT"
                            )
                            await start_new_trade(market, cfg)

                # --- АВТОСТАРТ ВІД НАЯВНИХ МОНЕТ / FALLBACK НА USDT ---
                if cfg.get("autotrade"):
                    no_tracked = len(cfg.get("orders", [])) == 0
                    no_active = (len(active_ids) == 0)
                    if no_tracked and no_active:
                        # 1) спроба старту без купівлі — з холдингів
                        started_from_holdings = await place_tp_sl_from_holdings(market, cfg)
                        if started_from_holdings:
                            await bot.send_message(
                                chat_id=cfg.get("chat_id", 0) or 0,
                                text=f"▶️ {market}: старт від наявних монет (TP/SL виставлено)"
                            )
                        else:
                            # 2) fallback: купівля за USDT, якщо достатньо коштів
                            usdt = await get_usdt_available()
                            spend = Decimal(str(cfg.get("buy_usdt", 10)))
                            spend_adj = (spend * Decimal("0.998")).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                            if usdt >= spend_adj and float(spend_adj) > 0:
                                await bot.send_message(
                                    chat_id=cfg.get("chat_id", 0) or 0,
                                    text=f"▶️ {market}: автостарт купівлі на {spend_adj} USDT (бо холдингів немає)"
                                )
                                await start_new_trade(market, cfg)
                            else:
                                logging.info(f"[AUTOSTART SKIP] {market}: ні холдингів, ні достатньо USDT (USDT={usdt}, need≈{spend_adj})")

        except Exception as e:
            logging.error(f"Monitor error: {e}")

        await asyncio.sleep(10)

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
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    try:
        print("✅ main.py started")
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Bot stopped manually")
