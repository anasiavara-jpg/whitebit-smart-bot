import asyncio
import logging
import os
import json
import aiohttp
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)

# === ЛОГІ ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WHITEBIT_API = "https://whitebit.com/api/v4"

markets = {}
AUTO_TRADE = True

# === ДОПОМІЖНІ ФУНКЦІЇ ===
async def fetch_price(session, market):
    try:
        async with session.get(f"{WHITEBIT_API}/public/ticker?market={market}") as resp:
            data = await resp.json()
            return float(data.get(market, {}).get("last_price", 0))
    except Exception as e:
        logging.error(f"Помилка отримання ціни {market}: {e}")
        return None

async def save_markets():
    with open("markets.json", "w", encoding="utf-8") as f:
        json.dump(markets, f, indent=2)

async def load_markets():
    global markets
    try:
        with open("markets.json", "r", encoding="utf-8") as f:
            markets = json.load(f)
    except FileNotFoundError:
        markets = {}

# === КОМАНДИ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущений. Автоторгівля увімкнена, використовуйте /help")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "/price <ринок> — ціна
"
        "/balance — баланс
"
        "/buy <ринок> <сума> — купити
"
        "/sell <ринок> <сума> — продати
"
        "/setamount <ринок> <сума> — дефолтна сума
"
        "/market <ринок> — додати ринок
"
        "/removemarket <ринок> — видалити ринок
"
        "/settp <ринок> <відсоток> — встановити TP
"
        "/setsl <ринок> <відсоток> — встановити SL
"
        "/status — показати всі ринки
"
        "/auto on|off — увімк/вимк автоторгівлю
"
        "/stop — зупинити бота
"
        "/restart — перезапустити бота"
    )
    await update.message.reply_text(help_text)

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Вкажіть ринок, напр. /market DOGE_USDT")
        return
    m = context.args[0].upper()
    markets[m] = {"amount": None, "tp": None, "sl": None}
    await save_markets()
    await update.message.reply_text(f"✅ Додано {m}")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Вкажіть ринок")
        return
    m = context.args[0].upper()
    if m in markets:
        del markets[m]
        await save_markets()
        await update.message.reply_text(f"🗑 Ринок {m} видалено")
    else:
        await update.message.reply_text("❌ Ринку немає у списку")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Використання: /setamount <ринок> <сума>")
        return
    m, amount = context.args[0].upper(), context.args[1]
    if m not in markets:
        await update.message.reply_text("❌ Спочатку додайте ринок командою /market")
        return
    markets[m]["amount"] = float(amount)
    await save_markets()
    await update.message.reply_text(f"Сума для {m}: {amount}")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Використання: /settp <ринок> <відсоток>")
        return
    m, tp = context.args[0].upper(), context.args[1]
    if m not in markets:
        await update.message.reply_text("❌ Спочатку додайте ринок командою /market")
        return
    markets[m]["tp"] = float(tp)
    await save_markets()
    await update.message.reply_text(f"TP для {m}: {tp}%")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Використання: /setsl <ринок> <відсоток>")
        return
    m, sl = context.args[0].upper(), context.args[1]
    if m not in markets:
        await update.message.reply_text("❌ Спочатку додайте ринок командою /market")
        return
    markets[m]["sl"] = float(sl)
    await save_markets()
    await update.message.reply_text(f"SL для {m}: {sl}%")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not markets:
        await update.message.reply_text("❌ Список ринків порожній")
        return
    text = "\n".join([f"{m}: amount={d['amount']}, TP={d['tp']}, SL={d['sl']}" for m, d in markets.items()])
    await update.message.reply_text(text)

# === АВТОТОРГІВЛЯ ===
async def trade_loop(app):
    async with aiohttp.ClientSession() as session:
        while True:
            if AUTO_TRADE:
                for m in markets:
                    price = await fetch_price(session, m)
                    if price:
                        logging.info(f"Ціна {m}: {price}")
            await asyncio.sleep(10)

async def main():
    await load_markets()
    application = Application.builder().token(BOT_TOKEN).build()

    # Встановлення хендлерів
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("market", market))
    application.add_handler(CommandHandler("removemarket", removemarket))
    application.add_handler(CommandHandler("setamount", setamount))
    application.add_handler(CommandHandler("settp", settp))
    application.add_handler(CommandHandler("setsl", setsl))
    application.add_handler(CommandHandler("status", status))

    # Стартуємо polling і trade loop паралельно
    loop = asyncio.get_running_loop()
    loop.create_task(trade_loop(application))

    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await application.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())
