import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import aiohttp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== Глобальні змінні =====
markets = {}
auto_trading_enabled = False

# ===== Хелпери =====
async def fetch_price(market: str):
    url = f"https://whitebit.com/api/v4/public/ticker?market={market}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("last_price")
            return None

# ===== Команди =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущений. Використовуйте /help")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/price <ринок> — ціна\n"
        "/market <ринок> — додати ринок\n"
        "/removemarket <ринок> — видалити ринок\n"
        "/setamount <ринок> <сума> — сума ордеру\n"
        "/settp <ринок> <відсоток> — встановити TP\n"
        "/setsl <ринок> <відсоток> — встановити SL\n"
        "/status — статус ботa\n"
        "/auto on|off — автоторгівля\n"
        "/stop — зупинити бота\n"
        "/restart — перезапустити"
    )
    await update.message.reply_text(text)

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Вкажіть ринок")
        return
    market = context.args[0]
    price = await fetch_price(market)
    if price:
        await update.message.reply_text(f"💲 {market}: {price}")
    else:
        await update.message.reply_text("❌ Не вдалося отримати ціну")

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Вкажіть ринок")
        return
    market = context.args[0]
    markets[market] = {"amount": 0, "tp": 0, "sl": 0}
    await update.message.reply_text(f"✅ Додано ринок {market}")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Вкажіть ринок")
        return
    market = context.args[0]
    if market in markets:
        del markets[market]
        await update.message.reply_text(f"🗑 Видалено ринок {market}")
    else:
        await update.message.reply_text("❌ Цього ринку немає")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not markets:
        await update.message.reply_text("ℹ️ Ринки не додані")
    else:
        text = "\n".join(
            [f"{m}: amount={v['amount']} TP={v['tp']} SL={v['sl']}" for m, v in markets.items()]
        )
        await update.message.reply_text(f"📊 Статус:\n{text}")

# ===== Автоторгівля =====
async def trade_loop():
    while auto_trading_enabled:
        for market in markets.keys():
            price = await fetch_price(market)
            logger.info(f"[AUTO] {market}: {price}")
        await asyncio.sleep(30)

async def toggle_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_trading_enabled
    if not context.args:
        await update.message.reply_text("⚠️ Використовуйте /auto on або /auto off")
        return
    if context.args[0] == "on":
        auto_trading_enabled = True
        asyncio.create_task(trade_loop())
        await update.message.reply_text("✅ Автоторгівля увімкнена")
    elif context.args[0] == "off":
        auto_trading_enabled = False
        await update.message.reply_text("⏹ Автоторгівля вимкнена")

# ===== Основна функція =====
async def main():
    application = Application.builder().token("YOUR_TELEGRAM_BOT_TOKEN").build()
    await application.bot.delete_webhook(drop_pending_updates=True)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("price", price))
    application.add_handler(CommandHandler("market", market))
    application.add_handler(CommandHandler("removemarket", removemarket))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("auto", toggle_auto))

    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
