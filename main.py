#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот автоторгівлі для WhiteBIT (PTB v20.x, aiohttp).
Особливості:
 • Видалення webhook перед стартом polling (для Render).
 • Команди: /start, /help, /price, /balance, /buy, /sell,
            /market, /removemarket, /setamount, /settp, /setsl,
            /status, /auto, /stop, /restart
 • Лімітні ордери + автоторгівля з TP/SL у %.
 • Асинхронні запити через aiohttp.
"""

import os
import hmac
import json
import time
import asyncio
import logging
import base64
from hashlib import sha512
from typing import Dict, Any, Optional

import aiohttp
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, JobQueue, Job
)

# ---------- Логи ----------
logging.basicConfig(
    format="%(asctime)s %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger("bot")

# ---------- ENV ----------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WB_API_KEY = os.getenv("WHITEBIT_API_KEY", "").strip()
WB_API_SECRET = os.getenv("WHITEBIT_API_SECRET", "").strip()
REAL_TRADE = os.getenv("REAL_TRADING", "false").lower() in {"1","true","yes","on"}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN відсутній у змінних середовища")

WB_PUBLIC = "https://whitebit.com/api/v4/public"
WB_PRIVATE = "https://whitebit.com/api/v4"

# ---------- Глобальні змінні ----------
session: aiohttp.ClientSession | None = None
user_state: Dict[int, Dict[str, Any]] = {}  # стани користувачів

# ---------- WhiteBIT API ----------
def _sign(body: dict) -> Dict[str, str]:
    payload = json.dumps(body, separators=(",", ":"))
    encoded = base64.b64encode(payload.encode()).decode()
    signature = hmac.new(
        WB_API_SECRET.encode(), encoded.encode(), sha512
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": WB_API_KEY,
        "X-TXC-PAYLOAD": encoded,
        "X-TXC-SIGNATURE": signature,
    }

async def wb_get_price(market: str) -> Optional[float]:
    url = f"{WB_PUBLIC}/ticker?market={market}"
    async with session.get(url) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        return float(data.get("last", 0))

async def wb_get_balance() -> Dict[str, float]:
    url = f"{WB_PRIVATE}/trade-account/balance"
    body = {"request": "/api/v4/trade-account/balance", "nonce": int(time.time()*1000)}
    headers = _sign(body)
    async with session.post(url, headers=headers, data=json.dumps(body)) as resp:
        if resp.status != 200:
            return {}
        data = await resp.json()
        return {k: float(v["available"]) for k,v in data.items()}

async def wb_place_order(market: str, side: str, amount: float, price: float) -> dict:
    url = f"{WB_PRIVATE}/order"
    body = {
        "market": market,
        "side": side,
        "amount": str(amount),
        "price": str(price),
        "request": "/api/v4/order",
        "nonce": int(time.time()*1000)
    }
    headers = _sign(body)
    async with session.post(url, headers=headers, data=json.dumps(body)) as resp:
        return await resp.json()

# ---------- Команди ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я бот для автоторгівлі на WhiteBIT.\n"
        "Використовуй /help для списку команд."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/price BTCUSDT - показати ціну\n"
        "/balance - баланс\n"
        "/market BTCUSDT - вибрати ринок\n"
        "/removemarket - прибрати ринок\n"
        "/setamount 0.1 - задати обсяг\n"
        "/settp 3 - тейк-профіт (%)\n"
        "/setsl 2 - стоп-лос (%)\n"
        "/buy BTCUSDT 0.1 65000 - лімітний buy\n"
        "/sell BTCUSDT 0.1 67000 - лімітний sell\n"
        "/status - показати параметри\n"
        "/auto - запустити автоторгівлю\n"
        "/stop - зупинити автоторгівлю\n"
        "/restart - перезапуск"
    )

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Вкажи пару, напр. /price BTCUSDT")
    market = context.args[0].upper()
    p = await wb_get_price(market)
    if not p:
        return await update.message.reply_text("Не вдалося отримати ціну.")
    await update.message.reply_text(f"Ціна {market}: {p}")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = await wb_get_balance()
    if not b:
        return await update.message.reply_text("Не вдалося отримати баланс.")
    text = "\n".join([f"{k}: {v}" for k,v in b.items()])
    await update.message.reply_text(f"Баланс:\n{text}")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        return await update.message.reply_text("Формат: /buy BTCUSDT 0.1 65000")
    market, amount, price = context.args[0].upper(), float(context.args[1]), float(context.args[2])
    res = await wb_place_order(market, "buy", amount, price)
    await update.message.reply_text(f"Buy result: {res}")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        return await update.message.reply_text("Формат: /sell BTCUSDT 0.1 67000")
    market, amount, price = context.args[0].upper(), float(context.args[1]), float(context.args[2])
    res = await wb_place_order(market, "sell", amount, price)
    await update.message.reply_text(f"Sell result: {res}")

# ---------- Автоторгівля ----------
async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    uid = job.chat_id
    state = user_state.get(uid, {})
    market, amount, tp, sl, entry = (
        state.get("market"), state.get("amount"), state.get("tp"), state.get("sl"), state.get("entry")
    )
    if not all([market, amount, tp, sl, entry]):
        return
    price = await wb_get_price(market)
    if not price:
        return
    if price >= entry * (1 + tp/100):
        res = await wb_place_order(market, "sell", amount, price)
        await context.bot.send_message(uid, f"TP досягнуто: {res}")
        job.schedule_removal()
    elif price <= entry * (1 - sl/100):
        res = await wb_place_order(market, "sell", amount, price)
        await context.bot.send_message(uid, f"SL спрацював: {res}")
        job.schedule_removal()

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    state = user_state.setdefault(uid, {})
    if not all(k in state for k in ("market","amount","tp","sl")):
        return await update.message.reply_text("Спочатку задай /market, /setamount, /settp, /setsl")
    state["entry"] = await wb_get_price(state["market"])
    context.job_queue.run_repeating(auto_job, interval=60, first=5, chat_id=uid)
    await update.message.reply_text("Автоторгівля запущена.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.get_jobs_by_chat_id(update.effective_chat.id)
    for j in jobs: j.schedule_removal()
    await update.message.reply_text("Автоторгівлю зупинено.")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stop(update, context)
    await auto(update, context)

# ---------- Параметри ----------
async def market_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Формат: /market BTCUSDT")
    uid = update.effective_chat.id
    user_state.setdefault(uid, {})["market"] = context.args[0].upper()
    await update.message.reply_text(f"Ринок {context.args[0].upper()} встановлено.")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    user_state.get(uid, {}).pop("market", None)
    await update.message.reply_text("Ринок прибрано.")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Формат: /setamount 0.1")
    uid = update.effective_chat.id
    user_state.setdefault(uid, {})["amount"] = float(context.args[0])
    await update.message.reply_text(f"Обсяг {context.args[0]} збережено.")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    user_state.setdefault(uid, {})["tp"] = float(context.args[0])
    await update.message.reply_text(f"TP {context.args[0]}% збережено.")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    user_state.setdefault(uid, {})["sl"] = float(context.args[0])
    await update.message.reply_text(f"SL {context.args[0]}% збережено.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = user_state.get(update.effective_chat.id, {})
    await update.message.reply_text(f"Статус: {state}")

# ---------- Main ----------
async def main():
    global session
    session = aiohttp.ClientSession()
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        await app.bot.delete_webhook(drop_pending_updates=True)

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(CommandHandler("price", price))
        app.add_handler(CommandHandler("balance", balance))
        app.add_handler(CommandHandler("buy", buy))
        app.add_handler(CommandHandler("sell", sell))
        app.add_handler(CommandHandler("market", market_cmd))
        app.add_handler(CommandHandler("removemarket", removemarket))
        app.add_handler(CommandHandler("setamount", setamount))
        app.add_handler(CommandHandler("settp", settp))
        app.add_handler(CommandHandler("setsl", setsl))
        app.add_handler(CommandHandler("status", status))
        app.add_handler(CommandHandler("auto", auto))
        app.add_handler(CommandHandler("stop", stop))
        app.add_handler(CommandHandler("restart", restart))

        await app.run_polling()
    finally:
        await session.close()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.get_event_loop().run_until_complete(main())
