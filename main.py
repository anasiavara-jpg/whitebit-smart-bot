import os
import sys
import asyncio
import logging
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Завантаження .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("❌ BOT_TOKEN не знайдений у .env")
    sys.exit(1)

# Логування
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)

# Глобальні змінні
AUTO_TRADE = True
MARKETS = []
DEFAULT_AMOUNT = {}
TP_MAP = {}
SL_MAP = {}
TASKS = set()

VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}

def is_valid_market(m: str) -> bool:
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущено! Автоторгівля активна.")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Баланс: (реалізація з API WhiteBIT)")  # TODO: додати реальну перевірку

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setamount BTC_USDT 10")
        return
    market, amount = context.args
    DEFAULT_AMOUNT[market] = float(amount)
    await update.message.reply_text(f"Для {market} встановлено суму {amount}")

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    m = context.args[0]
    if is_valid_market(m):
        if m not in MARKETS:
            MARKETS.append(m)
            await update.message.reply_text(f"✅ {m} додано.")
        else:
            await update.message.reply_text(f"{m} вже є.")
    else:
        await update.message.reply_text("❌ Невалідний ринок")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /removemarket BTC_USDT")
        return
    m = context.args[0]
    if m in MARKETS:
        MARKETS.remove(m)
        await update.message.reply_text(f"🗑 {m} видалено.")
    else:
        await update.message.reply_text(f"{m} немає у списку.")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /settp BTC_USDT 1")
        return
    m, tp = context.args
    TP_MAP[m] = float(tp)
    await update.message.reply_text(f"TP для {m} встановлено: {tp}%")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setsl BTC_USDT 1")
        return
    m, sl = context.args
    SL_MAP[m] = float(sl)
    await update.message.reply_text(f"SL для {m} встановлено: {sl}%")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not MARKETS:
        await update.message.reply_text("Список ринків порожній.")
        return
    msg = "📊 Параметри:
"
    for m in MARKETS:
        msg += f"{m} | Сума: {DEFAULT_AMOUNT.get(m,'?')} | TP: {TP_MAP.get(m,'?')} | SL: {SL_MAP.get(m,'?')}
"
    await update.message.reply_text(msg)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ Бот зупиняється...")
    await context.application.stop()

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Перезапуск бота...")
    os.execv(sys.executable, ['python'] + sys.argv)

async def auto_trade_loop(app: Application):
    while True:
        for m in [x for x in MARKETS if is_valid_market(x)]:
            logging.info(f"🔄 Перевірка {m}")
            # TODO: додати логіку торгівлі з API
        await asyncio.sleep(60)

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("setamount", setamount))
    application.add_handler(CommandHandler("market", market))
    application.add_handler(CommandHandler("removemarket", removemarket))
    application.add_handler(CommandHandler("settp", settp))
    application.add_handler(CommandHandler("setsl", setsl))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("restart", restart))

    loop = asyncio.get_event_loop()
    loop.create_task(auto_trade_loop(application))
    application.run_polling()

if __name__ == "__main__":
    main()
