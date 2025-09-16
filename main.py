import os
import logging
import sys
import asyncio
import hmac
import hashlib
import time
import json
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Завантаження .env або Render Environment Variables
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
API_PUBLIC = os.getenv("API_PUBLIC_KEY")
API_SECRET = os.getenv("API_SECRET_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не знайдений у Environment Variables")

# Логування
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# Глобальні змінні
AUTO_TRADE = False
MARKETS = []
DEFAULT_AMOUNT = {}
TP = 0.0
SL = 0.0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Я бот для WhiteBIT. Використай /help, щоб побачити команди.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/price <ринок> — ціна\n"
        "/balance [тикер] — баланс\n"
        "/buy <ринок> [сума] — ринкова покупка\n"
        "/sell <ринок> [кількість] — ринковий продаж\n"
        "/setamount <ринок> <сума> — дефолтна сума\n"
        "/market <ринок> — додати ринок у список\n"
        "/auto on|off — увімк/вимк автоторгівлю\n"
        "/settp <відсоток> — встановити TP\n"
        "/setsl <відсоток> — встановити SL\n"
        "/stop — зупинити бота\n"
        "/restart — перезапустити бота"
    )
    await update.message.reply_text(text)

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вкажи ринок, приклад: /price BTC_USDT")
        return
    market = context.args[0]
    try:
        r = requests.get(f"https://whitebit.com/api/v4/public/ticker?market={market}")
        data = r.json()
        if market in data:
            await update.message.reply_text(f"Поточна ціна {market}: {data[market]['last_price']}")
        else:
            await update.message.reply_text("Не вдалося отримати ціну.")
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ts = int(time.time())
        payload = {"request": "/api/v4/trade-account/balance", "nonce": ts}
        sign = hmac.new(API_SECRET.encode(), json.dumps(payload).encode(), hashlib.sha512).hexdigest()
        r = requests.post("https://whitebit.com/api/v4/trade-account/balance",
                          headers={"Content-Type": "application/json", "X-TXC-APIKEY": API_PUBLIC, "X-TXC-SIGNATURE": sign},
                          json=payload)
        data = r.json()
        await update.message.reply_text(json.dumps(data, indent=2))
    except Exception as e:
        await update.message.reply_text(f"Помилка отримання балансу: {e}")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вкажи ринок, приклад: /buy BTC_USDT")
        return
    market = context.args[0]
    amount = DEFAULT_AMOUNT.get(market, None)
    if len(context.args) > 1:
        amount = context.args[1]
    if not amount:
        await update.message.reply_text("Сума не задана. Використай /setamount або передай суму командою.")
        return
    try:
        payload = {"market": market, "side": "buy", "amount": str(amount), "type": "market", "clientOrderId": str(int(time.time()))}
        sign = hmac.new(API_SECRET.encode(), json.dumps(payload).encode(), hashlib.sha512).hexdigest()
        r = requests.post("https://whitebit.com/api/v4/order/market",
                          headers={"Content-Type": "application/json", "X-TXC-APIKEY": API_PUBLIC, "X-TXC-SIGNATURE": sign},
                          json=payload)
        await update.message.reply_text(f"Buy response: {r.json()}")
    except Exception as e:
        await update.message.reply_text(f"Помилка покупки: {e}")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вкажи ринок, приклад: /sell BTC_USDT")
        return
    market = context.args[0]
    amount = DEFAULT_AMOUNT.get(market, None)
    if len(context.args) > 1:
        amount = context.args[1]
    if not amount:
        await update.message.reply_text("Сума не задана. Використай /setamount або передай суму командою.")
        return
    try:
        payload = {"market": market, "side": "sell", "amount": str(amount), "type": "market", "clientOrderId": str(int(time.time()))}
        sign = hmac.new(API_SECRET.encode(), json.dumps(payload).encode(), hashlib.sha512).hexdigest()
        r = requests.post("https://whitebit.com/api/v4/order/market",
                          headers={"Content-Type": "application/json", "X-TXC-APIKEY": API_PUBLIC, "X-TXC-SIGNATURE": sign},
                          json=payload)
        await update.message.reply_text(f"Sell response: {r.json()}")
    except Exception as e:
        await update.message.reply_text(f"Помилка продажу: {e}")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setamount BTC_USDT 10")
        return
    market, amount = context.args
    DEFAULT_AMOUNT[market] = float(amount)
    await update.message.reply_text(f"Для {market} встановлено дефолтну суму: {amount}")

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    m = context.args[0]
    if m not in MARKETS:
        MARKETS.append(m)
        await update.message.reply_text(f"✅ Додано {m}. Поточні: {', '.join(MARKETS)}")
    else:
        await update.message.reply_text(f"{m} вже доданий.")

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    if not context.args:
        await update.message.reply_text(f"Автоторгівля зараз {'увімкнена' if AUTO_TRADE else 'вимкнена'}.")
        return
    if context.args[0].lower() == "on":
        AUTO_TRADE = True
        await update.message.reply_text("Автоторгівля увімкнена.")
    elif context.args[0].lower() == "off":
        AUTO_TRADE = False
        await update.message.reply_text("Автоторгівля вимкнена.")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TP
    try:
        TP = float(context.args[0])
        await update.message.reply_text(f"TP встановлено: {TP}%")
    except:
        await update.message.reply_text("Вкажи число, приклад: /settp 1")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SL
    try:
        SL = float(context.args[0])
        await update.message.reply_text(f"SL встановлено: {SL}%")
    except:
        await update.message.reply_text("Вкажи число, приклад: /setsl 1")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот зупиняється...")
    await context.application.stop()

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Перезапуск бота...")
    await context.application.stop()
    os.execv(sys.executable, ['python'] + sys.argv)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("auto", auto))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("restart", restart))
    app.run_polling()

if __name__ == "__main__":
    main()
