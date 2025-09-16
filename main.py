import os
import logging
import json
import requests
import time
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram Bot Token
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WHITEBIT_API_KEY = os.getenv("WHITEBIT_API_KEY")
WHITEBIT_API_SECRET = os.getenv("WHITEBIT_API_SECRET")

bot = Bot(token=TELEGRAM_TOKEN)

# --- WhiteBIT API helpers ---
BASE_URL = "https://whitebit.com/api/v4"


def get_price(symbol):
    try:
        response = requests.get(f"{BASE_URL}/public/ticker?market={symbol}")
        data = response.json()
        return float(data["result"][symbol]["last"])
    except Exception as e:
        logger.error(f"Error fetching price: {e}")
        return None


def get_balance(asset=None):
    try:
        headers = {"Content-Type": "application/json", "X-TXC-APIKEY": WHITEBIT_API_KEY}
        response = requests.post(f"{BASE_URL}/main-account/balance", headers=headers)
        if response.status_code != 200:
            return None, f"Помилка балансу: {response.status_code}"
        data = response.json()
        if asset:
            return data.get(asset, {"available": 0}), None
        return data, None
    except Exception as e:
        return None, str(e)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я бот для WhiteBIT.

"
        "Команди:
"
        "/price <ринок> — ціна (наприклад /price BTC_USDT)
"
        "/balance [тикер] — баланс (наприклад /balance або /balance USDT)
"
        "/buy <ринок> [сума] — ринкова покупка
"
        "/sell <ринок> [сума] — ринковий продаж
"
        "/setamount <ринок> <сума> — встановити дефолтну суму
"
        "/stop — зупинити бота
"
        "/restart — перезапустити бота
"
        "/auto on|off — увімкнути або вимкнути автоторгівлю
"
        "/settp <відсоток> — встановити take-profit
"
        "/setsl <відсоток> — встановити stop-loss"
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("Вкажи ринок, наприклад: /price BTC_USDT")
        return
    symbol = context.args[0].upper()
    price = get_price(symbol)
    if price:
        await update.message.reply_text(f"{symbol}: {price}")
    else:
        await update.message.reply_text("Не вдалося отримати ціну.")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    asset = context.args[0].upper() if context.args else None
    data, err = get_balance(asset)
    if err:
        await update.message.reply_text(f"Помилка: {err}")
    else:
        await update.message.reply_text(json.dumps(data, indent=2, ensure_ascii=False))


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏹️ Бот зупинений. Перезапусти сервіс на Render або /restart.")


# Main function
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("stop", stop))
    app.run_polling()


if __name__ == "__main__":
    main()
