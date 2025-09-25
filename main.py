import asyncio
import logging
import os
import hmac
import time
import hashlib
import httpx
import json

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from dotenv import load_dotenv

# ---------------- CONFIG ----------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)

BASE_URL = "https://whitebit.com/api/v4"
MARKETS_FILE = "markets.json"

markets = {}

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
        save_markets()  # створюємо порожній файл при першому запуску

# ---------------- HELPERS ----------------
async def signed_request(endpoint: str, body: dict = None) -> dict:
    if body is None:
        body = {}
    body["request"] = endpoint
    body["nonce"] = int(time.time() * 1000)
    payload = str(body).replace("'", '"').encode()

    sign = hmac.new(API_SECRET.encode(), payload, hashlib.sha512).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_KEY,
        "X-TXC-SIGNATURE": sign
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(BASE_URL + endpoint, json=body, headers=headers)
        try:
            return r.json()
        except Exception:
            return {"error": r.text}

async def public_request(endpoint: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(BASE_URL + endpoint)
        return r.json()

async def cancel_order(order_id: int):
    return await signed_request("/order/cancel", {"order_id": order_id})

async def order_status(order_id: int):
    return await signed_request("/order/status", {"order_id": order_id})

# ---------------- BOT COMMANDS ----------------
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("👋 Привіт! Я трейдинг-бот для WhiteBIT.\nВикористай /help щоб подивитись список команд.")

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
    data = await signed_request("/profile/balance")
    if "error" in data:
        await message.answer(f"❌ Помилка: {data['error']}")
        return
    text = "💰 Баланс:\n"
    for asset, info in data.items():
        available = info.get("available", "0")
        if float(available) > 0:
            text += f"{asset}: {available}\n"
    await message.answer(text)

@dp.message(Command("market"))
async def market_cmd(message: types.Message):
    try:
        _, market = message.text.split()
        market = market.upper().replace("/", "_")
        markets[market] = {"tp": None, "sl": None, "orders": [], "autotrade": False, "buy_usdt": 10}
        save_markets()
        await message.answer(f"✅ Додано ринок {market} (за замовчуванням 10 USDT)")
    except:
        await message.answer("⚠️ Використання: /market BTC/USDT")

@dp.message(Command("settp"))
async def settp_cmd(message: types.Message):
    try:
        _, market, percent = message.text.split()
        market = market.upper().replace("/", "_")
        markets[market]["tp"] = float(percent)
        save_markets()
        await message.answer(f"📈 TP для {market}: {percent}%")
    except:
        await message.answer("⚠️ Використання: /settp BTC/USDT 5")

@dp.message(Command("setsl"))
async def setsl_cmd(message: types.Message):
    try:
        _, market, percent = message.text.split()
        market = market.upper().replace("/", "_")
        markets[market]["sl"] = float(percent)
        save_markets()
        await message.answer(f"📉 SL для {market}: {percent}%")
    except:
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
    except:
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
    except:
        await message.answer("⚠️ Використання: /autotrade BTC/USDT on|off")

# ------------------- TRADE -------------------
async def place_market_order(market: str, side: str, amount: float):
    body = {"market": market, "side": side, "amount": str(amount), "type": "market"}
    return await signed_request("/order/new", body)

async def place_limit_order(market: str, side: str, price: float, amount: float):
    body = {"market": market, "side": side, "amount": str(amount), "price": str(price), "type": "limit"}
    return await signed_request("/order/new", body)

async def start_new_trade(market: str, cfg: dict):
    balances = await signed_request("/profile/balance")
    usdt = float(balances.get("USDT", {}).get("available", 0))
    spend = cfg.get("buy_usdt", 10)
    if usdt < spend:
        logging.warning(f"Недостатньо USDT для {market}.")
        return
    buy_res = await place_market_order(market, "buy", spend)
    if "error" in buy_res:
        logging.error(f"Помилка купівлі: {buy_res}")
        return
    ticker = await public_request(f"/public/ticker/{market}")
    last_price = float(ticker.get("last_price", 0))
    amount = float(buy_res.get("amount", 0))
    cfg["orders"] = []
    if cfg["tp"]:
        tp_price = last_price * (1 + cfg["tp"] / 100)
        tp_order = await place_limit_order(market, "sell", tp_price, amount)
        if "id" in tp_order: cfg["orders"].append(tp_order["id"])
    if cfg["sl"]:
        sl_price = last_price * (1 - cfg["sl"] / 100)
        sl_order = await place_limit_order(market, "sell", sl_price, amount)
        if "id" in sl_order: cfg["orders"].append(sl_order["id"])
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
    except:
        await message.answer("⚠️ Використання: /buy BTC/USDT")

# ------------------- STATUS -------------------
@dp.message(Command("status"))
async def status_cmd(message: types.Message):
    if not markets:
        await message.answer("ℹ️ Активних ринків немає.")
        return
    text = "📊 Статус:\n"
    for m, cfg in markets.items():
        text += f"\n{m}:\n TP: {cfg['tp']}%\n SL: {cfg['sl']}%\n Buy: {cfg['buy_usdt']} USDT\n Автотрейд: {cfg['autotrade']}\n Ордерів: {len(cfg['orders'])}\n"
    await message.answer(text)

# ------------------- OTHER -------------------
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
    except:
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

# ------------------- MONITOR -------------------
async def monitor_orders():
    while True:
        try:
            for market, cfg in list(markets.items()):
                for order_id in list(cfg["orders"]):
                    status = await order_status(order_id)
                    if status.get("status") == "closed":
                        await bot.send_message(chat_id=cfg.get("chat_id", 0) or 0,
                                               text=f"✅ Ордер {order_id} ({market}) виконано!")
                        for oid in cfg["orders"]:
                            if oid != order_id:
                                await cancel_order(oid)
                        cfg["orders"].clear()
                        if cfg.get("autotrade"):
                            await bot.send_message(chat_id=cfg.get("chat_id", 0) or 0,
                                                   text=f"♻️ Автотрейд {market}: нова угода на {cfg['buy_usdt']} USDT")
                            await start_new_trade(market, cfg)
        except Exception as e:
            logging.error(f"Monitor error: {e}")
        await asyncio.sleep(10)

# ---------------- RUN ----------------
async def main():
    load_markets()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(monitor_orders())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
