import os
import logging
import asyncio
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Завантаження .env
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не знайдений у .env файлі")

# Логування
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# Глобальні змінні
AUTO_TRADE = False
MARKETS = []
DEFAULT_AMOUNT = {}
TP = 0.0
SL = 0.0

# Команди
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Я бот для WhiteBIT.\nВикористай /help, щоб побачити команди.")

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
        response = requests.get(f"https://whitebit.com/api/v4/public/ticker?market={market}")
        data = response.json()
        if market in data:
            price_value = data[market]["last_price"]
            await update.message.reply_text(f"Поточна ціна {market}: {price_value}")
        else:
            await update.message.reply_text("Не вдалося отримати ціну.")
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Баланс недоступний у демо-версії (підключення до WhiteBIT API треба налаштувати).")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Тут буде логіка покупки (ще не підключено до біржі).")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Тут буде логіка продажу (ще не підключено до біржі).")

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

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот зупиняється...")
    await context.application.stop()

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Перезапуск бота...")
    await context.application.stop()
    os.execv(__file__, ["python"] + sys.argv)

# Запуск бота
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
