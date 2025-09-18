import os
import sys
import logging
import asyncio
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Завантаження змінних середовища
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    print("❌ BOT_TOKEN не знайдено у .env файлі")
    sys.exit(1)

# Логування
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

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

# Команди
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущений. Автоторгівля працює, використовуйте /help")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/price <ринок> — ціна\n"
        "/balance — баланс\n"
        "/buy <ринок> [сума] — купити\n"
        "/sell <ринок> [сума] — продати\n"
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
    await update.message.reply_text(text)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not MARKETS:
        await update.message.reply_text("Немає активних пар")
        return
    lines = []
    for m in [x for x in MARKETS if is_valid_market(x)]:
        amt = DEFAULT_AMOUNT.get(m, "—")
        tp = TP_MAP.get(m, "—")
        sl = SL_MAP.get(m, "—")
        lines.append(f"{m}: amount={amt}, TP={tp}, SL={sl}")
    await update.message.reply_text("\n".join(lines))

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    m = context.args[0]
    if not is_valid_market(m):
        await update.message.reply_text("❌ Невалідний ринок")
        return
    if m not in MARKETS:
        MARKETS.append(m)
    await update.message.reply_text(f"✅ Додано {m}")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /removemarket BTC_USDT")
        return
    m = context.args[0]
    if m in MARKETS:
        MARKETS.remove(m)
        await update.message.reply_text(f"🗑 Видалено {m}")
    else:
        await update.message.reply_text(f"{m} не було в списку")

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    if not context.args:
        await update.message.reply_text(f"Автоторгівля {'увімкнена' if AUTO_TRADE else 'вимкнена'}")
        return
    if context.args[0].lower() == "on":
        AUTO_TRADE = True
        await update.message.reply_text("✅ Автоторгівля увімкнена")
    elif context.args[0].lower() == "off":
        AUTO_TRADE = False
        await update.message.reply_text("⏸ Автоторгівля вимкнена")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏹ Зупинка бота")
    await context.application.stop()

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Перезапуск бота...")
    os.execv(sys.executable, ["python"] + sys.argv)

async def trade_loop(app: Application):
    while True:
        if AUTO_TRADE and MARKETS:
            for m in [x for x in MARKETS if is_valid_market(x)]:
                logging.info(f"🔄 Перевірка ринку {m}")
                # Логіка торгівлі буде тут
        await asyncio.sleep(5)

async def hourly_report(app: Application):
    while True:
        if MARKETS:
            text = "\n".join([f"{m}: amount={DEFAULT_AMOUNT.get(m,'—')}, TP={TP_MAP.get(m,'—')}, SL={SL_MAP.get(m,'—')}" for m in MARKETS])
            for chat_id in app.chat_ids:
                await app.bot.send_message(chat_id=chat_id, text=f"📊 Щогодинний звіт:\n{text}")
        await asyncio.sleep(3600)

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("removemarket", removemarket))
    app.add_handler(CommandHandler("auto", auto))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("restart", restart))

    asyncio.get_running_loop().create_task(trade_loop(app))
    asyncio.get_running_loop().create_task(hourly_report(app))

    import asyncio

    # Автозняття вебхука перед polling
    try:
        asyncio.run(app.bot.delete_webhook())
    except Exception as e:
        print(f"Не вдалося зняти вебхук: {e}")

    app.run_polling()

if __name__ == "__main__":
    main()
