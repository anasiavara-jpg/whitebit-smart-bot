import os
import logging
import hmac
import hashlib
import time
import json
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Завантажуємо .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_PUBLIC = os.getenv("API_PUBLIC")
API_SECRET = os.getenv("API_SECRET")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не знайдений у environment variables")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# Глобальні змінні
AUTO_TRADE = False
MARKETS = []
DEFAULT_AMOUNT = {}
TP = 0.0
SL = 0.0

WHITEBIT_API_URL = "https://whitebit.com/api/v4/order/market"

def sign_request(payload: dict) -> dict:
    data_json = json.dumps(payload, separators=(',', ':'))
    signature = hmac.new(API_SECRET.encode(), data_json.encode(), hashlib.sha512).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_PUBLIC,
        "X-TXC-PAYLOAD": data_json,
        "X-TXC-SIGNATURE": signature
    }

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Я бот для WhiteBIT.
Використай /help, щоб побачити команди.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/price <ринок> — ціна\n"
        "/balance [тикер] — баланс (WIP)\n"
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
        response = requests.get(f"https://whitebit.com/api/v4/public/ticker?market={market}")
        data = response.json()
        if market in data:
            price_value = data[market]["last_price"]
            await update.message.reply_text(f"Поточна ціна {market}: {price_value}")
        else:
            await update.message.reply_text("Не вдалося отримати ціну.")
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

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
    market = context.args[0]
    if market not in MARKETS:
        MARKETS.append(market)
        await update.message.reply_text(f"✅ Додано {market}. Поточні: {', '.join(MARKETS)}")
    else:
        await update.message.reply_text(f"{market} вже доданий.")

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

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2 and (not context.args[0] in DEFAULT_AMOUNT):
        await update.message.reply_text("Вкажи ринок і суму: /buy BTC_USDT 10 або задай дефолтну суму через /setamount")
        return
    market = context.args[0]
    amount = float(context.args[1]) if len(context.args) > 1 else DEFAULT_AMOUNT.get(market)
    payload = {
        "market": market,
        "side": "buy",
        "amount": str(amount),
        "type": "market",
        "clientOrderId": str(int(time.time()))
    }
    try:
        headers = sign_request(payload)
        response = requests.post(WHITEBIT_API_URL, headers=headers, data=json.dumps(payload))
        await update.message.reply_text(f"Buy response: {response.text}")
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("auto", auto))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("buy", buy))
    app.run_polling()

if __name__ == "__main__":
    main()
