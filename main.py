# main.py — WhiteBIT Smart Bot (v4-ready, clean)
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

        # WhiteBIT може віддавати {"success": False, "message": "..."}:
        if isinstance(data, dict) and (data.get("success") is False) and "message" in data:
            logging.error(f"WhiteBIT error: {data.get('message')}")
        return data

# ---------------- WHITEBIT API WRAPPERS ----------------
async def get_balance() -> dict:
    # POST /api/v4/trade-account/balance
    data = await private_post("/api/v4/trade-account/balance")
    logging.info(f"DEBUG balance: {data}")
    return data if isinstance(data, dict) else {}

async def place_market_order(market: str, side: str, amount: float) -> dict:
    # POST /api/v4/order/market
    return await private_post("/api/v4/order/market", {
        "market": market,           # "BTC_USDT"
        "side": side,               # "buy" | "sell"
        "amount": amount,           # ← число, не str
        "type": "market",
    })

async def place_limit_order(market: str, side: str, price: float, amount: float,
                            client_order_id: Optional[str] = None, post_only: Optional[bool] = None,
                            stp: Optional[str] = None) -> dict:
    # POST /api/v4/order/new
async def place_limit_order(market: str, side: str, price: float, amount: float,
                            client_order_id: Optional[str] = None, post_only: Optional[bool] = None,
                            stp: Optional[str] = None) -> dict:
    body = {
        "market": market,
        "side": side,
        "amount": amount,   # ← число
        "price": price,     # ← число
        "type": "limit",
    }
    if client_order_id:
        body["clientOrderId"] = str(client_order_id)
    if post_only is not None:
        body["postOnly"] = bool(post_only)
    if stp:
        body["stp"] = st
    return await private_post("/api/v4/order/new", body)

async def active_orders(market: Optional[str] = None) -> dict:
    # POST /api/v4/orders — активні ордери (можна фільтрувати по market)
    body = {}
    if market:
        body["market"] = market
    return await private_post("/api/v4/orders", body)

async def cancel_order(market: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> dict:
    # POST /api/v4/order/cancel — потрібен market і orderId або clientOrderId
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
    # GET /api/v4/public/ticker -> { "BTC_USDT": {"last_price":"..."} , ... }
    t = await public_get("/api/v4/public/ticker")
    try:
        lp = t.get(market, {}).get("last_price")
        return float(lp) if lp is not None else None
    except Exception:
        logging.error(f"Не вдалося взяти last_price для {market}: {t}")
        return None

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
        "/autotrade BTC/USDT on|off — увімк/вимк автотрейд"
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
            "tp": None,            # % take-profit
            "sl": None,            # % stop-loss
            "orders": [],          # список ордерів [{"id":..., "cid":..., "type":"tp|sl", "market":...}]
            "autotrade": False,
            "buy_usdt": 10,
            "chat_id": message.chat.id,
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

@dp.message(Command("autotrade"))
async def autotrade_cmd(message: types.Message):
    try:
        _, market, mode = message.text.split()
        market = market.upper().replace("/", "_")
        if market not in markets:
            await message.answer("❌ Спочатку додай ринок через /market.")
            return
        if mode.lower() == "on":
            markets[market]["autotrade"] = True
            save_markets()
            await message.answer(f"♻️ Автотрейд для {market} увімкнено.")
        elif mode.lower() == "off":
            markets[market]["autotrade"] = False
            save_markets()
            await message.answer(f"⏹️ Автотрейд для {market} вимкнено.")
        else:
            await message.answer("⚠️ Використання: /autotrade BTC/USDT on|off")
    except Exception:
        await message.answer("⚠️ Використання: /autotrade BTC/USDT on|off")

# ---------------- TRADE LOGIC ----------------
def _extract_order_id(resp: dict) -> Optional[int]:
    """
    WhiteBIT у відповіді на створення може повертати 'orderId' або 'id'.
    """
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

async def start_new_trade(market: str, cfg: dict):
    # 1) Баланс
    balances = await get_balance()
    usdt_av = (balances.get("USDT") or {}).get("available", 0)
    try:
        usdt = float(usdt_av)
    except Exception:
        usdt = 0.0

    spend = float(cfg.get("buy_usdt", 10.0))
    if usdt < spend:
        logging.warning(f"Недостатньо USDT для {market}. Є {usdt}, треба {spend}.")
        return

    # 2) Поточна ціна
    last_price = await get_last_price(market)
    if not last_price or last_price <= 0:
        logging.error(f"Не вдалося отримати last_price для {market}.")
        return

    # 3) Розрахунок кількості (базова монета)
    base_amount = round(spend / last_price, 8)
    if base_amount <= 0:
        logging.error(f"Нульовий обсяг базової монети: spend={spend}, price={last_price}")
        return

    # 4) Купівля market
    buy_res = await place_market_order(market, "buy", base_amount)
    if isinstance(buy_res, dict) and buy_res.get("success") is False:
        logging.error(f"Помилка купівлі: {buy_res}")
        return
    logging.info(f"BUY placed: {buy_res}")

    # 5) Створення TP/SL як окремих лімітів (дзеркальне OCO)
    cfg["orders"] = []
    # для відстеження — clientOrderId (зручніше для cancel)
    ts = now_ms()

    if cfg.get("tp"):
        tp_price = round(last_price * (1 + float(cfg["tp"]) / 100), 6)
        cid = f"wb-{market}-tp-{ts}"
        tp_order = await place_limit_order(market, "sell", tp_price, base_amount,
                                           client_order_id=cid, stp="cancel_new")
        oid = _extract_order_id(tp_order)
        if oid:
            cfg["orders"].append({"id": oid, "cid": cid, "type": "tp", "market": market})

    if cfg.get("sl"):
        sl_price = round(last_price * (1 - float(cfg["sl"]) / 100), 6)
        cid = f"wb-{market}-sl-{ts}"
        sl_order = await place_limit_order(market, "sell", sl_price, base_amount,
                                           client_order_id=cid, stp="cancel_new")
        oid = _extract_order_id(sl_order)
        if oid:
            cfg["orders"].append({"id": oid, "cid": cid, "type": "sl", "market": market})

    save_markets()

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

@dp.message(Command("status"))
async def status_cmd(message: types.Message):
    if not markets:
        await message.answer("ℹ️ Активних ринків немає.")
        return
    text = "📊 <b>Статус</b>:\n"
    for m, cfg in markets.items():
        text += (
            f"\n{m}:\n"
            f" TP: {cfg['tp']}%\n"
            f" SL: {cfg['sl']}%\n"
            f" Buy: {cfg['buy_usdt']} USDT\n"
            f" Автотрейд: {cfg['autotrade']}\n"
            f" Ордерів: {len(cfg.get('orders', []))}\n"
        )
    await message.answer(text)

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
    """
    while True:
        try:
            for market, cfg in list(markets.items()):
                # Тягнемо активні ордери по маркету, виділяємо їх ID (рядкові, але ми зберігаємо int)
                act = await active_orders(market)
                active_ids = set()
                if isinstance(act, dict):
                    # формат може бути {"orders":[{...}], ...} або { ... "orderId": ... }
                    orders_list = act.get("orders") if isinstance(act.get("orders"), list) else None
                    if orders_list:
                        for o in orders_list:
                            # WhiteBIT часто повертає 'orderId' або 'id'
                            oid = None
                            if isinstance(o, dict):
                                if "orderId" in o:
                                    oid = int(str(o["orderId"]))
                                elif "id" in o:
                                    oid = int(str(o["id"]))
                            if oid is not None:
                                active_ids.add(oid)

                # перевіряємо наші відслідковувані ордери
                finished_any = None
                for entry in list(cfg.get("orders", [])):
                    if entry["id"] not in active_ids:
                        # цього ордера більше немає в активних -> вважаємо виконаним/закритим/відхиленим
                        finished_any = entry
                        break

                if finished_any:
                    # повідомити
                    await bot.send_message(
                        chat_id=cfg.get("chat_id", 0) or 0,
                        text=f"✅ Ордер {finished_any['id']} ({market}, {finished_any['type']}) закрито!"
                    )
                    # скасувати інший із пари
                    for entry in list(cfg["orders"]):
                        if entry["id"] != finished_any["id"]:
                            await cancel_order(market, order_id=entry["id"])
                    cfg["orders"].clear()
                    save_markets()

                    # автотрейд — одразу нова угода
                    if cfg.get("autotrade"):
                        await bot.send_message(
                            chat_id=cfg.get("chat_id", 0) or 0,
                            text=f"♻️ Автотрейд {market}: нова угода на {cfg['buy_usdt']} USDT"
                        )
                        await start_new_trade(market, cfg)

        except Exception as e:
            logging.error(f"Monitor error: {e}")

        await asyncio.sleep(10)

# ---------------- RUN ----------------
async def main():
    load_markets()
    logging.info("🚀 Bot is running and waiting for commands...")

    # Вимкнути webhook і скинути pending перед polling (важливо для Render/перезапусків)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("✅ Webhook очищено успішно")
    except Exception as e:
        logging.error(f"❌ Помилка очищення webhook: {e}")

    # Запускаємо монітор
    asyncio.create_task(monitor_orders())

    # Старт polling
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    try:
        print("✅ main.py started")
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Bot stopped manually")
