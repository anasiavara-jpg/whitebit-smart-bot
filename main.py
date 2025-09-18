import logging
import os
import asyncio
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# -------------------- ЛОГІНГ --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# -------------------- НАЛАШТУВАННЯ --------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN відсутній!")

# Словник ринків для збереження параметрів
markets = {}

# -------------------- ЛОГІКА КОМАНД --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущений. Автоторгівля працює. Використовуйте /help")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/price <ринок> — ціна\n"
        "/balance — баланс\n"
        "/buy <ринок> <сума> — купити\n"
        "/sell <ринок> <сума> — продати\n"
        "/setamount <ринок> <сума> — дефолтна сума\n"
        "/market <ринок> — додати ринок\n"
        "/removemarket <ринок> — видалити ринок\n"
        "/settp <ринок> <відсоток> — встановити TP\n"
        "/setsl <ринок> <відсоток> — встановити SL\n"
        "/status — поточні пари та параметри\n"
        "/auto on|off — автоторгівля\n"
        "/stop — зупинити бота\n"
        "/restart — перезапустити бота"
    )

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("⚠️ Використовуйте: /market BTC_USDT")
        return
    pair = context.args[0].upper()
    markets[pair] = {"amount": None, "tp": None, "sl": None}
    await update.message.reply_text(f"✅ Додано {pair}")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("⚠️ Використовуйте: /setamount BTC_USDT 5")
        return
    pair, amount = context.args[0].upper(), context.args[1]
    if pair not in markets:
        await update.message.reply_text(f"❌ Ринок {pair} не знайдено.")
        return
    try:
        markets[pair]["amount"] = float(amount)
        await update.message.reply_text(f"Сума для {pair}: {amount}")
    except ValueError:
        await update.message.reply_text("❌ Невірний формат суми.")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("⚠️ Використовуйте: /settp BTC_USDT 0.5")
        return
    pair, tp = context.args[0].upper(), context.args[1]
    if pair not in markets:
        await update.message.reply_text(f"❌ Ринок {pair} не знайдено.")
        return
    try:
        markets[pair]["tp"] = float(tp)
        await update.message.reply_text(f"TP для {pair}: {tp}%")
    except ValueError:
        await update.message.reply_text("❌ Невірний формат TP.")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("⚠️ Використовуйте: /setsl BTC_USDT 0.3")
        return
    pair, sl = context.args[0].upper(), context.args[1]
    if pair not in markets:
        await update.message.reply_text(f"❌ Ринок {pair} не знайдено.")
        return
    try:
        markets[pair]["sl"] = float(sl)
        await update.message.reply_text(f"SL для {pair}: {sl}%")
    except ValueError:
        await update.message.reply_text("❌ Невірний формат SL.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not markets:
        await update.message.reply_text("ℹ️ Ринки не додані.")
        return
    msg = "\n".join([f"{pair}: amount={data['amount']}, TP={data['tp']}, SL={data['sl']}" for pair, data in markets.items()])
    await update.message.reply_text(msg)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ Бот зупинено.")
    os._exit(0)

# -------------------- МЕЙН --------------------

async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Видаляємо старий webhook перед polling
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning(f"Не вдалося видалити webhook: {e}")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop))

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
