import os
import hmac
import hashlib
import time
import aiohttp
import asyncio
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)

# ==============================
# 🔑 Keys from environment
# ==============================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WHITEBIT_API_KEY = os.getenv("API_PUBLIC_KEY")
WHITEBIT_API_SECRET = os.getenv("API_SECRET_KEY", "").encode()
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "false").lower() == "true"

API_URL = "https://whitebit.com/api/v4"

# ==============================
# ⚙️ User state
# ==============================
user_state = {
    "market": None,
    "amount": None,
    "tp": None,
    "sl": None,
    "auto": False,
}

# ==============================
# 🌐 WhiteBIT API
# ==============================
async def wb_request(endpoint, method="GET", params=None, private=False):
    url = f"{API_URL}{endpoint}"
    headers = {}
    data = params or {}

    if private:
        data["request"] = endpoint
        data["nonce"] = int(time.time() * 1000)
        payload = "&".join([f"{k}={v}" for k, v in data.items()])
        sign = hmac.new(WHITEBIT_API_SECRET, payload.encode(), hashlib.sha512).hexdigest()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-TXC-APIKEY": WHITEBIT_API_KEY,
            "X-TXC-PAYLOAD": payload,
            "X-TXC-SIGNATURE": sign,
        }

    async with aiohttp.ClientSession() as session:
        if method == "GET":
            async with session.get(url, headers=headers) as resp:
                return await resp.json()
        else:
            async with session.post(url, data=data, headers=headers) as resp:
                return await resp.json()

async def wb_get_price(market: str):
    return await wb_request(f"/public/ticker?market={market}", method="GET")

async def wb_get_balance():
    return await wb_request("/account/balance", method="POST", private=True)

async def wb_place_order(market: str, side: str, amount: float, price: float):
    if not TRADING_ENABLED:
        return {"demo": True, "market": market, "side": side, "amount": amount, "price": price}
    endpoint = "/order/new"
    params = {
        "market": market,
        "side": side,
        "amount": str(amount),
        "price": str(price),
        "type": "limit"
    }
    return await wb_request(endpoint, method="POST", params=params, private=True)

# ==============================
# 🤖 Bot commands
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привіт! Це WhiteBIT бот. Використовуй /help для команд.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/market <PAIR>\n/removemarket\n/setamount <AMOUNT>\n/settp <PERCENT>\n/setsl <PERCENT>\n"
        "/buy <PAIR> <AMOUNT> <PRICE>\n/sell <PAIR> <AMOUNT> <PRICE>\n/price <PAIR>\n/balance\n/orders\n"
        "/cancel <ORDER_ID>\n/cancel_all\n/status\n/auto\n/stop\n/restart"
    )

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Використання: /market BTCUSDT")
        return
    user_state["market"] = context.args[0].upper()
    await update.message.reply_text(f"✅ Ринок: {user_state['market']}")

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state["market"] = None
    await update.message.reply_text("❌ Ринок прибрано")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_state["amount"] = float(context.args[0])
        await update.message.reply_text(f"✅ Кількість: {user_state['amount']}")
    except:
        await update.message.reply_text("⚠️ Використання: /setamount 0.1")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_state["tp"] = float(context.args[0])
        await update.message.reply_text(f"✅ TP: {user_state['tp']}%")
    except:
        await update.message.reply_text("⚠️ Використання: /settp 3")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_state["sl"] = float(context.args[0])
        await update.message.reply_text(f"✅ SL: {user_state['sl']}%")
    except:
        await update.message.reply_text("⚠️ Використання: /setsl 2")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("⚠️ Використання: /buy BTCUSDT 0.1 65000")
        return
    market, amount, price = context.args[0], float(context.args[1]), float(context.args[2])
    res = await wb_place_order(market, "buy", amount, price)
    await update.message.reply_text(f"🟢 Buy: {res}")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("⚠️ Використання: /sell BTCUSDT 0.1 67000")
        return
    market, amount, price = context.args[0], float(context.args[1]), float(context.args[2])
    res = await wb_place_order(market, "sell", amount, price)
    await update.message.reply_text(f"🔴 Sell: {res}")

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    market = context.args[0].upper()
    res = await wb_get_price(market)
    await update.message.reply_text(f"💰 {market}: {res}")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = await wb_get_balance()
    await update.message.reply_text(f"💰 Баланс: {res}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(str(user_state))

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state["auto"] = True
    await update.message.reply_text("⚡ Автоторгівля увімкнена")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state["auto"] = False
    await update.message.reply_text("⏹ Автоторгівля зупинена")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Перезапуск...")
    os._exit(1)

# ==============================
# 🚀 Main
# ==============================
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("removemarket", removemarket))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("auto", auto))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("restart", restart))

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
