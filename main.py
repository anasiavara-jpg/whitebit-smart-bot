import os
import logging
import asyncio
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Завантаження змінних
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не знайдений у .env")

# Логування
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальні змінні
AUTO_TRADE = False
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
    global AUTO_TRADE
    AUTO_TRADE = True
    await update.message.reply_text("✅ Бот запущено. Автоторгівля активна.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    AUTO_TRADE = False
    await update.message.reply_text("⏹ Автоторгівлю зупинено.")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Перезапуск бота...")
    os.execv(__file__, ["python"] + os.sys.argv)

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    m = context.args[0].upper()
    if is_valid_market(m):
        if m not in MARKETS:
            MARKETS.append(m)
            await update.message.reply_text(f"✅ Додано {m}.")
        else:
            await update.message.reply_text(f"{m} вже є в списку.")
    else:
        await update.message.reply_text("⚠️ Невалідний ринок.")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /removemarket BTC_USDT")
        return
    m = context.args[0].upper()
    if m in MARKETS:
        MARKETS.remove(m)
        await update.message.reply_text(f"❌ {m} видалено зі списку.")
    else:
        await update.message.reply_text(f"{m} не знайдено у списку.")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💰 Баланси поки що заглушка (API можна додати).")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setamount BTC_USDT 10")
        return
    m, a = context.args
    DEFAULT_AMOUNT[m.upper()] = float(a)
    await update.message.reply_text(f"🔧 Для {m.upper()} встановлено суму {a}")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /settp BTC_USDT 1.5")
        return
    m, tp = context.args
    TP_MAP[m.upper()] = float(tp)
    await update.message.reply_text(f"🎯 TP для {m.upper()} встановлено {tp}%")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setsl BTC_USDT 1")
        return
    m, sl = context.args
    SL_MAP[m.upper()] = float(sl)
    await update.message.reply_text(f"🛑 SL для {m.upper()} встановлено {sl}%")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_text = "📊 *Статус бота*
"
    status_text += f"Автоторгівля: {'ON' if AUTO_TRADE else 'OFF'}
"
    if MARKETS:
        status_text += "Ринки: " + ", ".join(MARKETS) + "
"
    await update.message.reply_text(status_text)

async def auto_trading_loop(app: Application):
    while True:
        if AUTO_TRADE:
            for m in [mm for mm in MARKETS if is_valid_market(mm)]:
                try:
                    # Тут буде логіка торгівлі
                    logger.info(f"Працюємо з {m}")
                except Exception as e:
                    logger.error(f"Помилка з {m}: {e}")
        await asyncio.sleep(60)

async def hourly_report(app: Application):
    while True:
        if MARKETS:
            text = "⏳ Щогодинний звіт:
" + ", ".join(MARKETS)
            for chat_id in app.chat_ids:
                try:
                    await app.bot.send_message(chat_id=chat_id, text=text)
                except:
                    pass
        await asyncio.sleep(3600)

async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("removemarket", removemarket))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("status", status))

    asyncio.create_task(auto_trading_loop(app))
    asyncio.create_task(hourly_report(app))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await app.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())
