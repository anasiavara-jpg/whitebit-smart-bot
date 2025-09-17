import logging
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
)
import os

# ===== Налаштування =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}
MARKETS = set()
DEFAULT_AMOUNT = {}
TP_MAP = {}
SL_MAP = {}
AUTO_TRADE = True

def is_valid_market(m: str) -> bool:
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

async def check_bot_instance(application=None):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe") as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logging.error("[INSTANCE] Невірний токен або бот не активний.")
                    if application:
                        await application.bot.send_message(chat_id=CHAT_ID, text="❌ Запуск скасовано: невірний токен.")
                    return False
                return True
    except Exception as e:
        logging.error(f"[INSTANCE] Помилка перевірки інстансу: {e}")
        return True

async def hourly_report(context: ContextTypes.DEFAULT_TYPE):
    try:
        report_lines = []
        for m in sorted(MARKETS):
            if not is_valid_market(m):
                continue
            tp = TP_MAP.get(m, "-")
            sl = SL_MAP.get(m, "-")
            amt = DEFAULT_AMOUNT.get(m, "-")
            report_lines.append(f"{m}: TP={tp} SL={sl} Amt={amt}")
        msg = "📊 Щогодинний звіт:\n" + "\n".join(report_lines)
        await context.bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        logging.error(f"[HOURLY_REPORT] {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    if not await check_bot_instance(context.application):
        await update.message.reply_text("⚠️ Інший інстанс бота вже працює. Запуск скасовано.")
        return
    AUTO_TRADE = True
    await update.message.reply_text("✅ Бот запущено. Автоторгівля УВІМКНЕНА.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    AUTO_TRADE = False
    await update.message.reply_text("⛔️ Автоторгівлю вимкнено.")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    AUTO_TRADE = False
    await asyncio.sleep(1)
    if not await check_bot_instance(context.application):
        await update.message.reply_text("⚠️ Інший інстанс бота вже працює. Запуск скасовано.")
        return
    AUTO_TRADE = True
    await update.message.reply_text("♻️ Бот перезапущено. Автоторгівля УВІМКНЕНА.")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Вкажіть ринок для видалення. Приклад: /removemarket BTC_USDT")
        return
    market = context.args[0].upper()
    if market in MARKETS:
        MARKETS.discard(market)
        TP_MAP.pop(market, None)
        SL_MAP.pop(market, None)
        DEFAULT_AMOUNT.pop(market, None)
        await update.message.reply_text(f"🗑 Ринок {market} видалено.")
    else:
        await update.message.reply_text(f"⚠️ Ринок {market} не знайдено.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    report_lines = []
    for m in sorted(MARKETS):
        if not is_valid_market(m):
            continue
        tp = TP_MAP.get(m, "-")
        sl = SL_MAP.get(m, "-")
        amt = DEFAULT_AMOUNT.get(m, "-")
        report_lines.append(f"{m}: TP={tp} SL={sl} Amt={amt}")
    msg = "\n".join(report_lines) if report_lines else "Ринків немає."
    await update.message.reply_text("📊 Статус:\n" + msg)

async def auto_trade_loop():
    while True:
        if AUTO_TRADE:
            for market in [m for m in MARKETS if is_valid_market(m)]:
                try:
                    # Тут логіка торгівлі
                    pass
                except Exception as e:
                    logging.error(f"[AUTO LOOP] Помилка для {market}: {e}")
                    continue
        await asyncio.sleep(5)

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("restart", restart))
    application.add_handler(CommandHandler("removemarket", removemarket))
    application.add_handler(CommandHandler("status", status))

    job_queue: JobQueue = application.job_queue
    if job_queue:
        job_queue.run_repeating(hourly_report, interval=3600, first=10, name="hourly_report")

    asyncio.create_task(auto_trade_loop())
    await application.run_polling()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
