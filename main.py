import os
import sys
import json
import time
import hmac
import base64
import hashlib
import asyncio
import logging
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# === Налаштування ===
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_PUBLIC = os.getenv("API_PUBLIC_KEY", "")
API_SECRET = os.getenv("API_SECRET_KEY", "")
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "false").lower() in ["true", "1", "yes"]
VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}

if not BOT_TOKEN:
    print("[ERROR] BOT_TOKEN відсутній.")
    sys.exit(1)

# Логування
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# Глобальні змінні
AUTO_TRADE = True
MARKETS = []
DEFAULT_AMOUNT = {}
TP_MAP = {}
SL_MAP = {}

# === Утиліти ===
def is_valid_market(m):
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

def log_and_notify(app, msg):
    logging.info(msg)
    try:
        asyncio.create_task(app.bot.send_message(chat_id=ADMIN_CHAT, text=msg))
    except:
        pass

# === API WhiteBIT ===
def make_signature(path, data=None):
    if data is None:
        data = {}
    data["request"] = path
    data["nonce"] = str(int(time.time() * 1000))
    body = json.dumps(data, separators=(",", ":"))
    payload = base64.b64encode(body.encode()).decode()
    signature = hmac.new(API_SECRET.encode(), body.encode(), hashlib.sha512).hexdigest()
    return body, payload, signature

def wb_private_post(path, data=None):
    if not (API_PUBLIC and API_SECRET):
        return {}
    body, payload, signature = make_signature(path, data)
    headers = {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_PUBLIC,
        "X-TXC-PAYLOAD": payload,
        "X-TXC-SIGNATURE": signature,
    }
    r = requests.post(f"https://whitebit.com/api/v4{path}", data=body, headers=headers, timeout=30)
    if r.status_code != 200:
        logging.error(f"WhiteBIT API error: {r.status_code} {r.text}")
    return r.json() if r.text else {}

def wb_price(market):
    try:
        r = requests.get(f"https://whitebit.com/api/v4/public/ticker?market={market}", timeout=10)
        data = r.json()
        return float(data[market]["last_price"]) if market in data else None
    except:
        return None

# === Telegram команди ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Бот запущено. Автоторгівля увімкнена.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏹ Бот зупиняється...")
    await context.application.stop()

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Перезапуск бота...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bals = wb_private_post("/api/v4/main-account/balance")
    if not bals:
        await update.message.reply_text("Баланс порожній або API ключі не налаштовані.")
        return
    lines = [f"{k}: {v['main_balance']}" for k, v in bals.items() if float(v['main_balance']) > 0]
    await update.message.reply_text("\n".join(lines))

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /price BTC_USDT")
        return
    market = context.args[0].upper()
    p = wb_price(market)
    await update.message.reply_text(f"{market}: {p}" if p else f"Ринок {market} не знайдено.")

async def add_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    market = context.args[0].upper()
    if is_valid_market(market):
        MARKETS.append(market)
        await update.message.reply_text(f"✅ Додано {market}")
    else:
        await update.message.reply_text("❌ Невалідний ринок")

async def remove_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /removemarket BTC_USDT")
        return
    market = context.args[0].upper()
    if market in MARKETS:
        MARKETS.remove(market)
        await update.message.reply_text(f"🗑 Видалено {market}")
    else:
        await update.message.reply_text("Ринок не знайдено у списку.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valid = [m for m in MARKETS if is_valid_market(m)]
    lines = [f"{m} | сума: {DEFAULT_AMOUNT.get(m, 'auto')} | TP: {TP_MAP.get(m, '-')} | SL: {SL_MAP.get(m, '-')}" for m in valid]
    await update.message.reply_text("\n".join(lines) if lines else "Немає активних ринків.")

# === Основна логіка ===
async def auto_trade_loop(app):
    while True:
        try:
            for market in [m for m in MARKETS if is_valid_market(m)]:
                p = wb_price(market)
                if not p:
                    continue
                # тут має бути логіка перевірки TP/SL і створення ордерів
            await asyncio.sleep(60)
        except Exception as e:
            logging.error(f"[AUTO LOOP ERROR] {e}")
            await asyncio.sleep(5)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("market", add_market))
    app.add_handler(CommandHandler("removemarket", remove_market))
    app.add_handler(CommandHandler("status", status))
    asyncio.get_event_loop().create_task(auto_trade_loop(app))
    app.run_polling()

if __name__ == "__main__":
    main()
