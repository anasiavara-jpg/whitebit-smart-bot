import os
import sys
import asyncio
import logging
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =================== НАЛАШТУВАННЯ ===================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN не знайдений у .env файлі")
    sys.exit(1)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

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

# =================== КОМАНДИ ===================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    AUTO_TRADE = True
    await update.message.reply_text("✅ Бот запущено та автоторгівля активована!")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("♻️ Перезапуск бота...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    AUTO_TRADE = False
    await update.message.reply_text("⏹ Автоторгівлю зупинено.")

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вкажи ринок, приклад: /market BTC_USDT")
        return
    m = context.args[0].upper()
    if is_valid_market(m):
        if m not in MARKETS:
            MARKETS.append(m)
            await update.message.reply_text(f"✅ {m} додано у список")
        else:
            await update.message.reply_text(f"⚠️ {m} вже у списку")
    else:
        await update.message.reply_text("❌ Невалідний ринок")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вкажи ринок для видалення: /removemarket BTC_USDT")
        return
    m = context.args[0].upper()
    if m in MARKETS:
        MARKETS.remove(m)
        await update.message.reply_text(f"🗑 {m} видалено зі списку")
    else:
        await update.message.reply_text("⚠️ Цього ринку немає у списку")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💰 Баланс недоступний у демо. Підключи WhiteBIT API ключі.")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /settp BTC_USDT 1")
        return
    try:
        m, tp = context.args[0].upper(), float(context.args[1])
        TP_MAP[m] = tp
        await update.message.reply_text(f"✅ TP для {m} встановлено: {tp}%")
    except:
        await update.message.reply_text("❌ Невірний формат")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setsl BTC_USDT 1")
        return
    try:
        m, sl = context.args[0].upper(), float(context.args[1])
        SL_MAP[m] = sl
        await update.message.reply_text(f"✅ SL для {m} встановлено: {sl}%")
    except:
        await update.message.reply_text("❌ Невірний формат")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = []
    for m in sorted(set(MARKETS) | set(DEFAULT_AMOUNT) | set(TP_MAP) | set(SL_MAP)):
        if not is_valid_market(m):
            continue
        tp = TP_MAP.get(m, '-')
        sl = SL_MAP.get(m, '-')
        amt = DEFAULT_AMOUNT.get(m, '-')
        lines.append(f"{m}: сума={amt}, TP={tp}, SL={sl}")
    txt = "\n".join(lines) if lines else "Список порожній"
    await update.message.reply_text(f"📊 Параметри:\n{txt}")

# =================== АВТОЦИКЛ ===================

async def auto_trade_loop(application):
    while True:
        if AUTO_TRADE and MARKETS:
            for m in [x for x in MARKETS if is_valid_market(x)]:
                try:
                    response = requests.get(f"https://whitebit.com/api/v4/public/ticker?market={m}")
                    data = response.json()
                    if m in data:
                        logging.info(f"Отримано ціну {m}: {data[m]['last_price']}")
                except Exception as e:
                    logging.error(f"Помилка для {m}: {e}")
        await asyncio.sleep(60)

# =================== ЗАПУСК ===================

async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("removemarket", removemarket))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("status", status))

    asyncio.create_task(auto_trade_loop(app))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
