import os
import asyncio
import logging
import json
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
WHITEBIT_API_KEY = os.getenv("WHITEBIT_API_KEY")
WHITEBIT_SECRET = os.getenv("WHITEBIT_SECRET")

logging.basicConfig(level=logging.INFO)
markets = {}
auto_trade = False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущено. Автоторгівля вимкнена.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/market <ринок> — додати ринок\n"
        "/removemarket <ринок> — видалити ринок\n"
        "/setamount <ринок> <сума> — дефолтна сума\n"
        "/settp <ринок> <TP%> — take-profit\n"
        "/setsl <ринок> <SL%> — stop-loss\n"
        "/auto on|off — автоторгівля\n"
        "/status — статус ринків\n"
        "/balance — баланс акаунта\n"
        "/buy <ринок> <сума> — купити\n"
        "/sell <ринок> <сума> — продати\n"
        "/stop — зупинити бота\n"
        "/restart — перезапустити бота"
    )

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("⚠️ Вкажи ринок, наприклад: /market BTC_USDT")
        return
    market = context.args[0].upper()
    markets[market] = {"amount": None, "tp": None, "sl": None}
    await update.message.reply_text(f"✅ Додано {market}")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("⚠️ Формат: /setamount BTC_USDT 5")
        return
    market, amount = context.args[0].upper(), context.args[1]
    if market not in markets:
        await update.message.reply_text("⚠️ Спочатку додай ринок /market")
        return
    markets[market]["amount"] = float(amount)
    await update.message.reply_text(f"Сума для {market}: {amount}")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("⚠️ Формат: /settp BTC_USDT 0.5")
        return
    market, tp = context.args[0].upper(), context.args[1]
    if market not in markets:
        await update.message.reply_text("⚠️ Спочатку додай ринок /market")
        return
    markets[market]["tp"] = float(tp)
    await update.message.reply_text(f"TP для {market}: {tp}%")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("⚠️ Формат: /setsl BTC_USDT 0.3")
        return
    market, sl = context.args[0].upper(), context.args[1]
    if market not in markets:
        await update.message.reply_text("⚠️ Спочатку додай ринок /market")
        return
    markets[market]["sl"] = float(sl)
    await update.message.reply_text(f"SL для {market}: {sl}%")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not markets:
        await update.message.reply_text("📭 Список ринків порожній")
        return
    reply = "📊 Поточні ринки:\n"
    for m, data in markets.items():
        reply += f"{m}: amount={data['amount']}, TP={data['tp']}, SL={data['sl']}\n"
    await update.message.reply_text(reply)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ⚠️ Тут можна інтегрувати реальний запит на WhiteBIT API
    await update.message.reply_text("💰 Баланс: 100 USDT (демо)")

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_trade
    if not context.args or context.args[0] not in ["on", "off"]:
        await update.message.reply_text("⚠️ Формат: /auto on|off")
        return
    auto_trade = context.args[0] == "on"
    await update.message.reply_text("🤖 Автоторгівля увімкнена" if auto_trade else "⏸ Автоторгівля вимкнена")

async def trade_loop():
    while True:
        if auto_trade:
            logging.info("✅ Виконується автоторгівля...")
            # Тут має бути логіка перевірки цін і виставлення ордерів
        await asyncio.sleep(10)

async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    await app.bot.delete_webhook(drop_pending_updates=True)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("auto", auto))

    asyncio.create_task(trade_loop())
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
