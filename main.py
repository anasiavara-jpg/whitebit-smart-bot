import logging
import aiohttp
from telegram import Update
from telegram.ext import ContextTypes
import asyncio

# ✅ Перевірка токена перед стартом
async def check_bot_instance(application=None):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe") as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logging.error("[TOKEN] Невірний токен або бот не активний.")
                    if application:
                        await application.bot.send_message(chat_id=CHAT_ID, text="❌ Запуск скасовано: невірний токен.")
                    return False
        return True
    except Exception as e:
        logging.error(f"[TOKEN] Помилка перевірки токена: {e}")
        return True

# 🧹 Фільтр ринків
VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}

def is_valid_market(m: str) -> bool:
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

# ⏳ Щогодинний звіт
async def hourly_report(context: ContextTypes.DEFAULT_TYPE):
    try:
        report_lines = ["📊 Щогодинний звіт:"]
        for m in [x for x in MARKETS if is_valid_market(x)]:
            price = LAST_PRICES.get(m, "—")
            tp = TP_MAP.get(m, "—")
            sl = SL_MAP.get(m, "—")
            amt = DEFAULT_AMOUNT.get(m, "—")
            report_lines.append(f"{m}: TP={tp} SL={sl} Amt={amt} Ціна={price}")
        await context.bot.send_message(chat_id=CHAT_ID, text="\n".join(report_lines))
    except Exception as e:
        logging.error(f"[REPORT] Помилка звіту: {e}")

# 🔄 Оновлена команда /restart
async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    await update.message.reply_text("♻ Перезапуск бота...")
    if not await check_bot_instance(context.application):
        await update.message.reply_text("⚠ Інший інстанс вже працює. Запуск скасовано.")
        return
    AUTO_TRADE = False
    AUTO_TRADE = True
    await update.message.reply_text("✅ Бот успішно перезапущений. Автоторгівля УВІМКНЕНА.")


import aiohttp

async def check_bot_instance(application=None):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe") as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logging.error("[INSTANCE] Невірний токен або бот не активний.")
                    if application:
                        await application.bot.send_message(chat_id=CHAT_ID, text="❌ Запуск скасовано: невірний токен.")
                    return False
                return True
    except Exception as e:
        logging.error(f"[INSTANCE] Помилка перевірки інстансу: {e}")
        return True  # не блокуємо запуск у випадку помилки перевірки

# Оновлений restart
async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    await update.message.reply_text("♻️ Перезапуск бота...")
    if not await check_bot_instance(context.application):
        await update.message.reply_text("⚠️ Інший інстанс бота вже працює, запуск скасовано.")
        return
    AUTO_TRADE = False
    AUTO_TRADE = True
    await update.message.reply_text("✅ Бот успішно перезапущений. Автоторгівля УВІМКНЕНА.")

# Виклик перевірки перед стартом
async def safe_start(app):
    if not await check_bot_instance(app):
        logging.warning("[INSTANCE] Запуск скасовано: інший бот уже працює.")
        return False
    return True

import os
import json
import time
import hmac
import hashlib
import logging
import requests
import asyncio
import sys
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_PUBLIC = os.getenv("API_PUBLIC")
API_SECRET = os.getenv("API_SECRET")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не знайдено")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

AUTO_TRADE = False
MARKETS = {}
DEFAULT_AMOUNT = {}
TP = {}
SL = {}
LAST_PRICE = {}

BASE_URL = "https://whitebit.com/api/v4"

def sign_request(payload):
    data = json.dumps(payload, separators=(',', ':'))
    signature = hmac.new(API_SECRET.encode(), data.encode(), hashlib.sha512).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_PUBLIC,
        "X-TXC-PAYLOAD": data,
        "X-TXC-SIGNATURE": signature
    }

def get_price(market):
    r = requests.get(f"{BASE_URL}/public/ticker?market={market}", timeout=10)
    r.raise_for_status()
    return float(r.json().get(market, {}).get("last_price", 0))

def create_order(market, side, amount):
    payload = {"market": market, "side": side, "amount": str(amount), "type": "market"}
    headers = sign_request(payload)
    r = requests.post(f"{BASE_URL}/order/market", headers=headers, data=json.dumps(payload), timeout=15)
    return r.json()

async def notify(update_or_app, text):
    try:
        if isinstance(update_or_app, Update):
            await update_or_app.message.reply_text(text)
        else:
            await update_or_app.bot.send_message(chat_id=update_or_app.chat_id, text=text)
    except Exception as e:
        logging.error(f"Notify error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Бот готовий. Використай /help")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/price <ринок>\n/balance\n/buy <ринок> <сума>\n/sell <ринок> <сума>\n"
        "/market <ринок>\n/setamount <ринок> <сума>\n/settp <ринок> <відсоток>\n/setsl <ринок> <відсоток>\n"
        "/auto on|off\n/status\n/stop"
    )

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /price BTC_USDT")
        return
    market = context.args[0].upper()
    p = get_price(market)
    await update.message.reply_text(f"{market}: {p}")

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    m = context.args[0].upper()
    MARKETS[m] = True
    await update.message.reply_text(f"✅ Додано {m}")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setamount BTC_USDT 5")
        return
    m, amt = context.args[0].upper(), float(context.args[1])
    DEFAULT_AMOUNT[m] = amt
    await update.message.reply_text(f"Сума для {m}: {amt}")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /settp BTC_USDT 1.5")
        return
    m, val = context.args[0].upper(), float(context.args[1])
    TP[m] = val
    await update.message.reply_text(f"TP для {m}: {val}%")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setsl BTC_USDT 1")
        return
    m, val = context.args[0].upper(), float(context.args[1])
    SL[m] = val
    await update.message.reply_text(f"SL для {m}: {val}%")

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    AUTO_TRADE = context.args and context.args[0].lower() == "on"
    await update.message.reply_text(f"Автоторгівля {'УВІМКНЕНА' if AUTO_TRADE else 'ВИМКНЕНА'}")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m, amt = context.args[0].upper(), float(context.args[1])
    res = create_order(m, "buy", amt)
    await update.message.reply_text(f"BUY {m}: {res}")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m, amt = context.args[0].upper(), float(context.args[1])
    res = create_order(m, "sell", amt)
    await update.message.reply_text(f"SELL {m}: {res}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"{m}: TP={TP.get(m,'-')} SL={SL.get(m,'-')} Amt={DEFAULT_AMOUNT.get(m,'-')}" for m in MARKETS]
    await update.message.reply_text("Статус:\n" + "\n".join(lines))

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Зупинка бота...")
    await context.application.stop()

async def auto_trade_loop(app):
    global LAST_PRICE
    while True:
        if AUTO_TRADE:
            for m in MARKETS:
                try:
                    price = get_price(m)
                    if m not in LAST_PRICE:
                        LAST_PRICE[m] = price
                        continue
                    if price <= LAST_PRICE[m] * 0.99:
                        amt = DEFAULT_AMOUNT.get(m, 0.001)
                        res = create_order(m, "buy", amt)
                        await app.bot.send_message(chat_id=list(app.bot_data.keys())[0], text=f"✅ Купив {amt} {m} @ {price}")
                        LAST_PRICE[m] = price
                    if TP.get(m) and price >= LAST_PRICE[m] * (1+TP[m]/100):
                        amt = DEFAULT_AMOUNT.get(m, 0.001)
                        res = create_order(m, "sell", amt)
                        await app.bot.send_message(chat_id=list(app.bot_data.keys())[0], text=f"💰 TP SELL {amt} {m} @ {price}")
                        LAST_PRICE[m] = price
                    if SL.get(m) and price <= LAST_PRICE[m] * (1-SL[m]/100):
                        amt = DEFAULT_AMOUNT.get(m, 0.001)
                        res = create_order(m, "sell", amt)
                        await app.bot.send_message(chat_id=list(app.bot_data.keys())[0], text=f"❌ SL SELL {amt} {m} @ {price}")
                        LAST_PRICE[m] = price
                except Exception as e:
                    logging.error(f"AUTO LOOP ERROR: {e}")
        await asyncio.sleep(10)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("auto", auto))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop))

    loop = asyncio.get_event_loop()
    loop.create_task(auto_trade_loop(app))
    app.run_polling()


# -------------------- ADDED PATCH (non-destructive) --------------------
VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}
CHAT_ID = None

def is_valid_market(m: str) -> bool:
    if not isinstance(m, str) or "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = (context.args[0] if context.args else "").upper()
    if not m:
        await update.message.reply_text("Приклад: /removemarket BTC_USDT")
        return
    removed = False
    if m in MARKETS:
        MARKETS.pop(m, None)
        removed = True
    DEFAULT_AMOUNT.pop(m, None)
    TP.pop(m, None)
    SL.pop(m, None)
    await update.message.reply_text(("🗑 Видалено " + m) if removed else ("⚠️ " + m + " не знайдено"))

# override status to show only valid items
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.message.chat_id
    keys = set([k for k in MARKETS.keys() if is_valid_market(k)])
    keys |= set([k for k in DEFAULT_AMOUNT.keys() if is_valid_market(k)])
    keys |= set([k for k in TP.keys() if is_valid_market(k)])
    keys |= set([k for k in SL.keys() if is_valid_market(k)])
    if not keys:
        await update.message.reply_text("Поки що немає валідних ринків.")
        return
    lines = []
    for m in sorted(keys):
        lines.append(f"{m}: TP={TP.get(m,'-')} SL={SL.get(m,'-')} Amt={DEFAULT_AMOUNT.get(m,'-')}")
    await update.message.reply_text("Статус:\n" + "\n".join(lines))

# hourly report with last prices
async def hourly_report(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id if getattr(context, "job", None) else CHAT_ID
    if not chat_id:
        return
    try:
        text_lines = ["⏰ Щогодинний звіт:"]
        for m in sorted([k for k in MARKETS.keys() if is_valid_market(k)]):
            try:
                price = get_price(m)
            except Exception:
                price = None
            text_lines.append(f"{m}: TP={TP.get(m,'-')} SL={SL.get(m,'-')} Amt={DEFAULT_AMOUNT.get(m,'-')} Price={price}")
        await context.bot.send_message(chat_id=chat_id, text="\n".join(text_lines))
    except Exception as e:
        logging.error(f"[hourly_report] {e}")

# make /start actually arm the loop
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID, AUTO_TRADE
    CHAT_ID = update.message.chat_id
    AUTO_TRADE = True
    await update.message.reply_text("✅ Бот запущено. Автоторгівля УВІМКНЕНА.")

# add a restart command
async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    AUTO_TRADE = False
    await update.message.reply_text("♻️ Перезапуск...")
    AUTO_TRADE = True
    await update.message.reply_text("✅ Перезапуск виконано. Автоторгівля УВІМКНЕНА.")

# robust auto loop override: skip invalid/incomplete markets
async def auto_trade_loop(app):
    global LAST_PRICE
    while True:
        if AUTO_TRADE:
            for m in list(MARKETS.keys()):
                if not is_valid_market(m):
                    continue
                tp = TP.get(m)
                sl = SL.get(m)
                amt = DEFAULT_AMOUNT.get(m)
                if tp is None or sl is None or amt is None:
                    continue
                try:
                    price = get_price(m)
                    if price is None:
                        continue
                    if m not in LAST_PRICE:
                        LAST_PRICE[m] = price
                        continue
                    # buy trigger: -1% від референсної
                    if price <= LAST_PRICE[m] * 0.99:
                        res = create_order(m, "buy", amt)
                        try:
                            if CHAT_ID:
                                await app.bot.send_message(chat_id=CHAT_ID, text=f"✅ Купив {amt} {m} @ {price}")
                        except Exception as ee:
                            logging.error(f"[notify buy] {ee}")
                        LAST_PRICE[m] = price
                    # TP
                    if tp and price >= LAST_PRICE[m] * (1 + tp/100):
                        res = create_order(m, "sell", amt)
                        try:
                            if CHAT_ID:
                                await app.bot.send_message(chat_id=CHAT_ID, text=f"💰 TP SELL {amt} {m} @ {price}")
                        except Exception as ee:
                            logging.error(f"[notify tp] {ee}")
                        LAST_PRICE[m] = price
                    # SL
                    if sl and price <= LAST_PRICE[m] * (1 - sl/100):
                        res = create_order(m, "sell", amt)
                        try:
                            if CHAT_ID:
                                await app.bot.send_message(chat_id=CHAT_ID, text=f"❌ SL SELL {amt} {m} @ {price}")
                        except Exception as ee:
                            logging.error(f"[notify sl] {ee}")
                        LAST_PRICE[m] = price
                except Exception as e:
                    logging.error(f"[AUTO LOOP] {m}: {e}")
        await asyncio.sleep(10)

# override main to add new handlers and schedule hourly report
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("removemarket", removemarket))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("auto", auto))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop))

    try:
        app.job_queue.run_repeating(hourly_report, interval=3600, first=3600)
    except Exception as e:
        logging.error(f"[job_queue] {e}")

    loop = asyncio.get_event_loop()
    loop.create_task(auto_trade_loop(app))
    app.run_polling()
# ------------------ END PATCH ------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"MAIN ERROR: {e}")
        os.execv(sys.executable, [sys.executable] + sys.argv)
