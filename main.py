
import os
import sys
import time
import json
import hmac
import base64
import hashlib
import asyncio
import logging
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Завантаження змінних оточення
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
API_PUBLIC = os.getenv("API_PUBLIC_KEY", "")
API_SECRET = os.getenv("API_SECRET_KEY", "")

if not TOKEN:
    print("⚠️ BOT_TOKEN відсутній. Додайте його в Environment.")
    sys.exit(1)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
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


def build_hourly_report(pairs_info):
    lines = ["📊 Щогодинний звіт:"]
    for pair, v in pairs_info.items():
        tp = v.get("tp", "-")
        sl = v.get("sl", "-")
        amt = v.get("amt", "-")
        lines.append(f"{pair}: TP={tp} SL={sl} Amt={amt}")
    return "\n".join(lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущений і готовий до торгівлі.")
    asyncio.create_task(auto_trade_loop(context))


def build_hourly_report(pairs_info):
    lines = ["📊 Щогодинний звіт:"]
    for pair, v in pairs_info.items():
        tp = v.get("tp", "-")
        sl = v.get("sl", "-")
        amt = v.get("amt", "-")
        lines.append(f"{pair}: TP={tp} SL={sl} Amt={amt}")
    return "\n".join(lines)

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("♻️ Перезапуск бота...")
    os.execv(sys.executable, ["python"] + sys.argv)


def build_hourly_report(pairs_info):
    lines = ["📊 Щогодинний звіт:"]
    for pair, v in pairs_info.items():
        tp = v.get("tp", "-")
        sl = v.get("sl", "-")
        amt = v.get("amt", "-")
        lines.append(f"{pair}: TP={tp} SL={sl} Amt={amt}")
    return "\n".join(lines)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/start — запуск автоторгівлі\n"
        "/restart — перезапустити бота\n"
        "/price <ринок> — показати ціну\n"
        "/balance — показати баланс\n"
        "/buy <ринок> <сума> — купити\n"
        "/sell <ринок> <кількість> — продати\n"
        "/setamount <ринок> <сума> — задати суму\n"
        "/auto on|off — увімк/вимк автоторгівлю\n"
        "/removemarket <ринок> — видалити ринок зі списку"
    )
    await update.message.reply_text(text)


def build_hourly_report(pairs_info):
    lines = ["📊 Щогодинний звіт:"]
    for pair, v in pairs_info.items():
        tp = v.get("tp", "-")
        sl = v.get("sl", "-")
        amt = v.get("amt", "-")
        lines.append(f"{pair}: TP={tp} SL={sl} Amt={amt}")
    return "\n".join(lines)

async def removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вкажіть ринок: /removemarket BTC_USDT")
        return
    market = context.args[0].upper()
    if market in MARKETS:
        MARKETS.remove(market)
        await update.message.reply_text(f"🗑 {market} видалено зі списку.")
    else:
        await update.message.reply_text(f"{market} не знайдено у списку.")


def build_hourly_report(pairs_info):
    lines = ["📊 Щогодинний звіт:"]
    for pair, v in pairs_info.items():
        tp = v.get("tp", "-")
        sl = v.get("sl", "-")
        amt = v.get("amt", "-")
        lines.append(f"{pair}: TP={tp} SL={sl} Amt={amt}")
    return "\n".join(lines)

async def auto_trade_loop(context):
    global AUTO_TRADE
    while True:
        if AUTO_TRADE:
            for market in [m for m in MARKETS if is_valid_market(m)]:
                try:
                    # Приклад логіки: запит ціни
                    r = requests.get(f"https://whitebit.com/api/v4/public/ticker?market={market}")
                    data = r.json()
                    if market not in data:
                        logging.warning(f"⛔ {market} недоступний.")
                        continue
                    last_price = float(data[market]["last_price"])
                    logging.info(f"[AUTO] {market} -> {last_price}")
                except Exception as e:
                    logging.error(f"[AUTO LOOP] {e}")
        await asyncio.sleep(60)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("removemarket", removemarket))

    app.run_polling()

if __name__ == "__main__":
    main()
