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

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"MAIN ERROR: {e}")
        os.execv(sys.executable, [sys.executable] + sys.argv)
