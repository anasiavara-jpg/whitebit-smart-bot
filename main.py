# main.py — WhiteBIT Smart Bot (clean)
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Dict, Any

import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from dotenv import load_dotenv

_nonce = int(time.time() * 1000)

def get_nonce():
    global _nonce
    _nonce += 1
    return _nonce

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

BASE_URL = "https://whitebit.com/api/v4"
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

# ---------------- HTTP HELPERS (WHITEBIT v4) ----------------
async def public_request(endpoint: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(BASE_URL + endpoint)
        return r.json()

def make_headers(endpoint: str, extra_body: dict | None = None) -> tuple[dict, str]:
    """
    ✅ Коректна версія підпису для WhiteBIT v4 (2025)
    Підпис: HMAC_SHA512(secret, nonce + url + payload)
    """
    nonce = str(get_nonce())
    endpoint_clean = endpoint.lstrip("/")  # 👈 ВАЖЛИВО: без початкового "/"
    body = extra_body or {}
    payload = json.dumps(body, separators=(",", ":"))

    signature_base = nonce + endpoint_clean + payload
    signature = hmac.new(API_SECRET.encode(), signature_base.encode(), hashlib.sha512).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_KEY,
        "X-TXC-PAYLOAD": payload,
        "X-TXC-SIGNATURE": signature,
        "X-TXC-Nonce": nonce,
    }
    return headers, payload


async def private_post(endpoint: str, extra_body: dict | None = None) -> dict:
    headers, payload = make_headers(endpoint, extra_body)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(BASE_URL + endpoint, headers=headers, content=payload)
        try:
            return r.json()
        except Exception:
            logging.error(f"Помилка декодування відповіді: {r.text}")
            return {"error": r.text}
            
# ---------------- WHITEBIT API ----------------
async def get_balance() -> dict:
    data = await private_post("/trade-account/balance")
    logging.info(f"DEBUG balance: {data}")
    print("🟡 RAW WhiteBIT balance response:", data)
    return data if isinstance(data, dict) else {}

async def place_market_order(market: str, side: str, amount: float) -> dict:
    return await private_post("/order/market", {
        "market": market,
        "side": side,                # "buy" | "sell"
        "amount": str(amount),
        "type": "market",
    })

async def place_limit_order(market: str, side: str, price: float, amount: float) -> dict:
    return await private_post("/order/new", {
        "market": market,
        "side": side,
        "amount": str(amount),
        "price": str(price),
        "type": "limit",
    })

async def order_status(order_id: int) -> dict:
    return await private_post("/trade-account/order", {"orderId": order_id})

async def cancel_order(order_id: int) -> dict:
    return await private_post("/trade-account/order/cancel", {"orderId": order_id})

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
    if not data:
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
async def start_new_trade(market: str, cfg: dict):
    # 1) Баланс
    balances = await get_balance()
    usdt_av = balances.get("USDT", {}).get("available", 0)
    try:
        usdt = float(usdt_av)
    except Exception:
        usdt = 0.0

    spend = float(cfg.get("buy_usdt", 10))
    if usdt < spend:
        logging.warning(f"Недостатньо USDT для {market}. Є {usdt}, треба {spend}.")
        return

    # 2) Поточна ціна
    ticker = await public_request("/public/ticker")
    try:
        last_price = float(ticker.get(market, {}).get("last_price"))
    except Exception:
        logging.error(f"Не вдалося отримати last_price для {market}: {ticker}")
        return

    # 3) Розрахунок кількості
    base_amount = round(spend / last_price, 8)
    if base_amount <= 0:
        logging.error(f"Нульовий обсяг базової монети: spend={spend}, price={last_price}")
        return

    # 4) Купівля
    buy_res = await place_market_order(market, "buy", base_amount)
    if "error" in buy_res:
        logging.error(f"Помилка купівлі: {buy_res}")
        return
    logging.info(f"BUY placed: {buy_res}")

    # 5) TP/SL
    cfg["orders"] = []
    if cfg.get("tp"):
        tp_price = round(last_price * (1 + float(cfg["tp"]) / 100), 6)
        tp_order = await place_limit_order(market, "sell", tp_price, base_amount)
        if isinstance(tp_order, dict) and "id" in tp_order:
            cfg["orders"].append(tp_order["id"])

    if cfg.get("sl"):
        sl_price = round(last_price * (1 - float(cfg["sl"]) / 100), 6)
        sl_order = await place_limit_order(market, "sell", sl_price, base_amount)
        if isinstance(sl_order, dict) and "id" in sl_order:
            cfg["orders"].append(sl_order["id"])

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
            f" Ордерів: {len(cfg['orders'])}\n"
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
    while True:
        try:
            for market, cfg in list(markets.items()):
                for order_id in list(cfg["orders"]):
                    st = await order_status(order_id)
                    if st.get("status") == "closed":
                        await bot.send_message(
                            chat_id=cfg.get("chat_id", 0) or 0,
                            text=f"✅ Ордер {order_id} ({market}) виконано!"
                        )
                        # Скасувати інші ордери (дзеркальний OCO)
                        for oid in list(cfg["orders"]):
                            if oid != order_id:
                                await cancel_order(oid)
                        cfg["orders"].clear()
                        save_markets()
                        # Автотрейд — нова угода
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

    # ✅ Видаляємо старий webhook і pending updates перед polling
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("✅ Webhook очищено успішно")
    except Exception as e:
        logging.error(f"❌ Помилка очищення webhook: {e}")

    # ✅ Запускаємо монітор ордерів
    asyncio.create_task(monitor_orders())

    # ✅ Старт polling без on_startup
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    try:
        print("✅ main.py started")
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Bot stopped manually")
