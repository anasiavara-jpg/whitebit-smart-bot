import os
import json
import time
import hmac
import hashlib
import logging
import requests
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# === ENVIRONMENT ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_PUBLIC = os.getenv("API_PUBLIC")
API_SECRET = os.getenv("API_SECRET")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не знайдений у Environment Variables")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# === GLOBAL STATE ===
AUTO_TRADE = False
MARKETS = []
DEFAULT_AMOUNT = {}
TP = 0.0
SL = 0.0
TRAILING = False
LAST_PRICES = {}

TG_CHAT_ID = None

WB_PUBLIC = "https://whitebit.com/api/v4/public/ticker?market="
WB_ORDER = "https://whitebit.com/api/v4/order/market"
WB_BALANCE = "https://whitebit.com/api/v4/main-account/balance"

# === HELPERS ===
def sign_request(payload: dict):
    data = json.dumps(payload, separators=(',', ':'))
    signature = hmac.new(API_SECRET.encode(), data.encode(), hashlib.sha512).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_PUBLIC,
        "X-TXC-PAYLOAD": data,
        "X-TXC-SIGNATURE": signature
    }

def get_price(market: str) -> float:
    r = requests.get(WB_PUBLIC + market, timeout=10)
    r.raise_for_status()
    data = r.json()
    return float(data.get(market, {}).get("last_price", 0))

def create_order(market: str, side: str, amount: float):
    payload = {
        "market": market,
        "side": side,
        "amount": str(amount),
        "type": "market",
        "clientOrderId": str(int(time.time()))
    }
    headers = sign_request(payload)
    r = requests.post(WB_ORDER, headers=headers, data=json.dumps(payload), timeout=15)
    logging.info(f"Order {side} {market}: {r.text}")
    return r.json()

def get_balances():
    payload = {"ticker": ""}
    headers = sign_request(payload)
    r = requests.post(WB_BALANCE, headers=headers, data=json.dumps(payload), timeout=15)
    return r.json()

async def notify(context, msg):
    global TG_CHAT_ID
    if TG_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=TG_CHAT_ID, text=msg)
        except Exception as e:
            logging.error(f"[NOTIFY ERROR] {e}")

# === COMMANDS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TG_CHAT_ID
    TG_CHAT_ID = update.effective_chat.id
    await update.message.reply_text("Привіт! Автоторгівля готова. Використай /help для команд.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/market <ринок> — додати ринок\n"
        "/remove <ринок> — прибрати ринок\n"
        "/markets — показати активні ринки\n"
        "/setamount <ринок> <сума> — задати суму покупки\n"
        "/settp <відсоток> — встановити TP\n"
        "/setsl <відсоток> — встановити SL\n"
        "/trailing on|off — увімк/вимк трейлінг стоп\n"
        "/auto on|off — увімк/вимк автоторгівлю\n"
        "/price <ринок> — показати ціну\n"
        "/balance — показати баланс\n"
        "/buy <ринок> <сума> — купити\n"
        "/sell <ринок> <сума> — продати\n"
        "/status — показати статус бота\n"
        "/stop — зупинити бота"
    )

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    m = context.args[0].upper()
    if m not in MARKETS:
        MARKETS.append(m)
    await update.message.reply_text(f"Додано ринок: {m}. Поточні: {', '.join(MARKETS)}")

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /remove BTC_USDT")
        return
    m = context.args[0].upper()
    if m in MARKETS:
        MARKETS.remove(m)
        await update.message.reply_text(f"{m} видалено.")

async def markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Активні ринки: {', '.join(MARKETS) if MARKETS else 'Немає'}")

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setamount BTC_USDT 10")
        return
    market, amount = context.args[0].upper(), float(context.args[1])
    DEFAULT_AMOUNT[market] = amount
    await update.message.reply_text(f"Сума для {market} встановлена: {amount}")

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TP
    TP = float(context.args[0])
    await update.message.reply_text(f"TP встановлено: {TP}%")

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SL
    SL = float(context.args[0])
    await update.message.reply_text(f"SL встановлено: {SL}%")

async def trailing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TRAILING
    TRAILING = context.args[0].lower() == "on"
    await update.message.reply_text(f"Trailing stop {'увімкнено' if TRAILING else 'вимкнено'}")

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    AUTO_TRADE = context.args[0].lower() == "on"
    await update.message.reply_text(f"Автоторгівля {'УВІМКНЕНА ✅' if AUTO_TRADE else 'ВИМКНЕНА ❌'}")

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    market = context.args[0].upper()
    p = get_price(market)
    await update.message.reply_text(f"{market}: {p}")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bals = get_balances()
    lines = [f"{k}: {v['main_balance']}" for k,v in bals.items() if float(v['main_balance']) > 0]
    await update.message.reply_text("Баланс:\n" + "\n".join(lines))

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    market, amount = context.args[0].upper(), float(context.args[1])
    res = create_order(market, "buy", amount)
    await update.message.reply_text(f"BUY {market}: {res}")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    market, amount = context.args[0].upper(), float(context.args[1])
    res = create_order(market, "sell", amount)
    await update.message.reply_text(f"SELL {market}: {res}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Автоторгівля: {'ON' if AUTO_TRADE else 'OFF'}\n"
        f"TP: {TP}% SL: {SL}%\n"
        f"Trailing: {'ON' if TRAILING else 'OFF'}\n"
        f"Ринки: {', '.join(MARKETS) if MARKETS else 'Немає'}"
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот зупиняється...")
    await context.application.stop()

# === AUTO TRADING LOOP ===
async def auto_trade_loop(application):
    global LAST_PRICES
    while True:
        if AUTO_TRADE and MARKETS:
            for m in MARKETS:
                try:
                    price = get_price(m)
                    last = LAST_PRICES.get(m, price)
                    LAST_PRICES[m] = price
                    # BUY/SELL LOGIC
                    if TP > 0 and price >= last * (1 + TP/100):
                        res = create_order(m, "sell", DEFAULT_AMOUNT.get(m, 0.001))
                        await notify(application, f"TP SELL {m} @ {price}: {res}")
                    elif SL > 0 and price <= last * (1 - SL/100):
                        res = create_order(m, "sell", DEFAULT_AMOUNT.get(m, 0.001))
                        await notify(application, f"SL SELL {m} @ {price}: {res}")
                except Exception as e:
                    logging.error(f"[AUTO ERROR] {e}")
                    await notify(application, f"Помилка автоторгівлі: {e}")
        await asyncio.sleep(10)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("markets", markets))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.add_handler(CommandHandler("trailing", trailing))
    app.add_handler(CommandHandler("auto", auto))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop))
    loop = asyncio.get_event_loop()
    loop.create_task(auto_trade_loop(app))
    app.run_polling()

if __name__ == "__main__":
    main()
