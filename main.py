import os
import logging
import asyncio
import requests
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Завантаження .env
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
API_PUBLIC = os.getenv("API_PUBLIC")
API_SECRET = os.getenv("API_SECRET")

if not TOKEN:
    raise ValueError("BOT_TOKEN не знайдено у середовищі")

# Логування
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

AUTO_TRADE = False
MARKETS = []
DEFAULT_AMOUNT = {}
TP = 0.0
SL = 0.0

# --- Допоміжні функції для WhiteBIT API ---
BASE_URL = "https://whitebit.com/api/v4"

async def whitebit_request(endpoint, method="GET", payload=None):
    headers = {"Content-Type": "application/json"}
    try:
        if method == "GET":
            r = requests.get(f"{BASE_URL}{endpoint}", headers=headers, params=payload)
        else:
            r = requests.post(f"{BASE_URL}{endpoint}", headers=headers, data=json.dumps(payload) if payload else None)
        if r.status_code == 200:
            return r.json()
        else:
            logging.error(f"WhiteBIT API error: {r.text}")
            return {"error": r.text}
    except Exception as e:
        logging.error(f"Request failed: {e}")
        return {"error": str(e)}

# --- Telegram команди ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Я бот для WhiteBIT.
Використай /help, щоб побачити команди.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/price <ринок> — показати ціну\n"
        "/balance — показати баланс\n"
        "/buy <ринок> [сума] — купити\n"
        "/sell <ринок> [сума] — продати\n"
        "/setamount <ринок> <сума> — дефолтна сума\n"
        "/market <ринок> — додати ринок\n"
        "/auto on|off — увімк/вимк автоторгівлю\n"
        "/settp <відсоток> — встановити TP\n"
        "/setsl <відсоток> — встановити SL\n"
        "/status — показати статус бота\n"
        "/stop — зупинити бота"
    )
    await update.message.reply_text(text)

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /price BTC_USDT")
        return
    market = context.args[0]
    data = await whitebit_request(f"/public/ticker?market={market}")
    if market in data:
        await update.message.reply_text(f"Поточна ціна {market}: {data[market]['last_price']}")
    else:
        await update.message.reply_text("Не вдалося отримати ціну.")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Показ балансу працюватиме після підключення API-ключів.")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /buy BTC_USDT 10")
        return
    market, amount = context.args
    await update.message.reply_text(f"✅ Купівля {amount} {market} виконана (демо).")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /sell BTC_USDT 10")
        return
    market, amount = context.args
    await update.message.reply_text(f"✅ Продаж {amount} {market} виконано (демо).")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setamount BTC_USDT 10")
        return
    market, amount = context.args
    DEFAULT_AMOUNT[market] = float(amount)
    await update.message.reply_text(f"Сума для {market} встановлена: {amount}")

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    market = context.args[0]
    if market not in MARKETS:
        MARKETS.append(market)
    await update.message.reply_text(f"✅ Додано {market}. Поточні: {', '.join(MARKETS)}")

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    if not context.args:
        await update.message.reply_text(f"Автоторгівля {'увімкнена' if AUTO_TRADE else 'вимкнена'}.")
        return
    if context.args[0].lower() == "on":
        AUTO_TRADE = True
        await update.message.reply_text("Автоторгівля УВІМКНЕНА ✅")
    elif context.args[0].lower() == "off":
        AUTO_TRADE = False
        await update.message.reply_text("Автоторгівля вимкнена ❌")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TP
    try:
        TP = float(context.args[0])
        await update.message.reply_text(f"TP встановлено: {TP}%")
    except:
        await update.message.reply_text("Приклад: /settp 1")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SL
    try:
        SL = float(context.args[0])
        await update.message.reply_text(f"SL встановлено: {SL}%")
    except:
        await update.message.reply_text("Приклад: /setsl 1")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Статус бота:\nАвтоторгівля: {'ON' if AUTO_TRADE else 'OFF'}\nTP: {TP}%\nSL: {SL}%")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏹ Бот зупиняється...")
    await context.application.stop()

# --- Головний запуск ---
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
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop))

    app.run_polling()

if __name__ == "__main__":
    main()
