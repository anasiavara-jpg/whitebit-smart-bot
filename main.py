import os
import sys
import logging
import asyncio
import json
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Завантаження змінних середовища
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("WHITEBIT_API_KEY")
API_SECRET = os.getenv("WHITEBIT_API_SECRET")
if not TOKEN:
    raise ValueError("BOT_TOKEN не знайдений у .env")

# Налаштування логування
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Глобальні змінні
AUTO_TRADE = True
MARKETS = []
DEFAULT_AMOUNT = {}
TP_MAP = {}
SL_MAP = {}

VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}

def is_valid_market(m: str) -> bool:
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущений. Автоторгівля активна." if AUTO_TRADE else "Бот запущений.")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("♻️ Перезапуск бота...")
    await context.application.stop()
    os.execv(sys.executable, ['python'] + sys.argv)

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    m = context.args[0].upper()
    if is_valid_market(m):
        MARKETS.append(m)
        await update.message.reply_text(f"✅ Додано {m}")
    else:
        await update.message.reply_text("❌ Невалідний ринок")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /removemarket BTC_USDT")
        return
    m = context.args[0].upper()
    if m in MARKETS:
        MARKETS.remove(m)
        await update.message.reply_text(f"🗑 Видалено {m}")
    else:
        await update.message.reply_text("Цього ринку нема у списку")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /settp BTC_USDT 1.5")
        return
    TP_MAP[context.args[0].upper()] = float(context.args[1])
    await update.message.reply_text(f"TP для {context.args[0].upper()} встановлено")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setsl BTC_USDT 1.0")
        return
    SL_MAP[context.args[0].upper()] = float(context.args[1])
    await update.message.reply_text(f"SL для {context.args[0].upper()} встановлено")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "📊 Статус:
"
    for m in MARKETS:
        if is_valid_market(m):
            msg += f"{m}: сума={DEFAULT_AMOUNT.get(m, 'N/A')}, TP={TP_MAP.get(m,'-')}%, SL={SL_MAP.get(m,'-')}%
"
    await update.message.reply_text(msg)

async def auto_trade_loop(app: Application):
    while True:
        try:
            for m in [x for x in MARKETS if is_valid_market(x)]:
                # Тут буде логіка запиту до WhiteBIT API та виконання угод
                logging.info(f"Перевірка {m}")
            await asyncio.sleep(60)
        except Exception as e:
            logging.error(f"Помилка в автоторгівлі: {e}")

async def hourly_report(app: Application):
    while True:
        text = "⏰ Щогодинний звіт:
"
        for m in [x for x in MARKETS if is_valid_market(x)]:
            text += f"{m}: TP={TP_MAP.get(m,'-')}%, SL={SL_MAP.get(m,'-')}%, сума={DEFAULT_AMOUNT.get(m,'N/A')}
"
        for chat_id in app.bot_data.get("subscribers", []):
            await app.bot.send_message(chat_id=chat_id, text=text)
        await asyncio.sleep(3600)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("removemarket", removemarket))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("status", status))

    loop = asyncio.get_event_loop()
    loop.create_task(auto_trade_loop(app))
    loop.create_task(hourly_report(app))
    app.run_polling()

if __name__ == "__main__":
    main()
