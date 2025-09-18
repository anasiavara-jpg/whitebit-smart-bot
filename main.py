import asyncio
import logging
import os
import json
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WHITEBIT_API = "https://whitebit.com/api/v4"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

markets = {}
auto_trade_enabled = True

# --- Utility functions ---
async def delete_webhook(app: Application):
    await app.bot.delete_webhook(drop_pending_updates=True)

def get_balance():
    # Placeholder for real API call
    return {"USDT": 100.0}

def place_order(market, side, amount):
    logger.info(f"Placing {side} order for {market}, amount {amount}")
    return True

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущено. Автоторгівля активна.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    commands = [
        "/balance — показати баланс",
        "/buy <ринок> <сума> — купити",
        "/sell <ринок> <сума> — продати",
        "/market <ринок> — додати ринок",
        "/removemarket <ринок> — видалити ринок",
        "/setamount <ринок> <сума> — встановити суму",
        "/settp <ринок> <відсоток> — встановити TP",
        "/setsl <ринок> <відсоток> — встановити SL",
        "/status — поточні пари",
        "/auto on|off — вкл/викл автоторгівлю",
        "/stop — зупинити бота",
        "/restart — перезапустити бота"
    ]
    await update.message.reply_text("\n".join(commands))

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = get_balance()
    text = "\n".join([f"{k}: {v}" for k, v in bal.items()])
    await update.message.reply_text(f"💰 Баланс:\n{text}")

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        return await update.message.reply_text("⚠️ Вкажіть ринок.")
    mk = context.args[0].upper()
    markets[mk] = {"amount": 0, "tp": 0, "sl": 0}
    await update.message.reply_text(f"✅ Додано {mk}")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        return await update.message.reply_text("⚠️ Вкажіть ринок.")
    mk = context.args[0].upper()
    markets.pop(mk, None)
    await update.message.reply_text(f"❌ Видалено {mk}")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("⚠️ Використання: /setamount <ринок> <сума>")
    mk, amount = context.args[0].upper(), float(context.args[1])
    if mk in markets:
        markets[mk]["amount"] = amount
        await update.message.reply_text(f"Сума для {mk}: {amount}")
    else:
        await update.message.reply_text("⚠️ Спершу додайте ринок.")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("⚠️ Використання: /settp <ринок> <відсоток>")
    mk, tp = context.args[0].upper(), float(context.args[1])
    if mk in markets:
        markets[mk]["tp"] = tp
        await update.message.reply_text(f"TP для {mk}: {tp}%")
    else:
        await update.message.reply_text("⚠️ Спершу додайте ринок.")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("⚠️ Використання: /setsl <ринок> <відсоток>")
    mk, sl = context.args[0].upper(), float(context.args[1])
    if mk in markets:
        markets[mk]["sl"] = sl
        await update.message.reply_text(f"SL для {mk}: {sl}%")
    else:
        await update.message.reply_text("⚠️ Спершу додайте ринок.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not markets:
        return await update.message.reply_text("⚠️ Немає активних ринків.")
    text = "\n".join([f"{k}: amount={v['amount']}, TP={v['tp']}, SL={v['sl']}" for k, v in markets.items()])
    await update.message.reply_text(text)

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_trade_enabled
    if len(context.args) == 0:
        return await update.message.reply_text(f"Автоторгівля: {'ON' if auto_trade_enabled else 'OFF'}")
    state = context.args[0].lower()
    auto_trade_enabled = state == "on"
    await update.message.reply_text(f"Автоторгівля {'увімкнена' if auto_trade_enabled else 'вимкнена'}")

async def trade_loop(app: Application):
    while True:
        if auto_trade_enabled:
            for mk, params in markets.items():
                if params["amount"] > 0:
                    place_order(mk, "buy", params["amount"])
        await asyncio.sleep(3600)

async def main():
    application = Application.builder().token(BOT_TOKEN).build()
    await delete_webhook(application)

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("market", market))
    application.add_handler(CommandHandler("removemarket", removemarket))
    application.add_handler(CommandHandler("setamount", setamount))
    application.add_handler(CommandHandler("settp", settp))
    application.add_handler(CommandHandler("setsl", setsl))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("auto", auto))

    loop = asyncio.get_running_loop()
    loop.create_task(trade_loop(application))

    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
