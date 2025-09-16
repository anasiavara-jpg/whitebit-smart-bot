import os
import time
import logging
import requests
import telebot

# === CONFIG ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
WHITEBIT_API_KEY = os.getenv("WHITEBIT_API_KEY", "YOUR_WHITEBIT_API_KEY")
WHITEBIT_API_SECRET = os.getenv("WHITEBIT_API_SECRET", "YOUR_WHITEBIT_API_SECRET")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
logging.basicConfig(level=logging.INFO)

# === GLOBAL STATE ===
markets = []
default_amounts = {}
auto_trading = False
tp_percent = 1.0
sl_percent = 1.0

def get_balance():
    url = "https://whitebit.com/api/v4/main-account/balance"
    headers = {"X-TXC-APIKEY": WHITEBIT_API_KEY}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.error(f"Balance error: {e}")
        return {"error": str(e)}

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Привіт! Я бот для WhiteBIT.
"
                          "Команди:
"
                          "/price <ринок>
"
                          "/balance [тікер]
"
                          "/buy <ринок> [сума]
"
                          "/sell <ринок> [сума]
"
                          "/setamount <ринок> <сума>
"
                          "/settp <відсоток>
"
                          "/setsl <відсоток>
"
                          "/market <ринок>
"
                          "/auto on|off
"
                          "/status
"
                          "/stop")

@bot.message_handler(commands=['status'])
def status(message):
    text = f"📊 Статус:
"
    text += f"Ринки: {', '.join(markets) if markets else 'не задано'}
"
    text += f"TP: {tp_percent}%, SL: {sl_percent}%
"
    text += f"Автоторгівля: {'УВІМКНЕНА' if auto_trading else 'ВИМКНЕНА'}"
    bot.reply_to(message, text)

@bot.message_handler(commands=['market'])
def add_market(message):
    global markets
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Приклад: /market BTC_USDT")
        return
    market = parts[1].upper()
    if market not in markets:
        markets.append(market)
    bot.reply_to(message, f"✅ Додано {market}. Поточні: {', '.join(markets)}")

@bot.message_handler(commands=['settp'])
def set_tp(message):
    global tp_percent
    try:
        tp_percent = float(message.text.split()[1])
        bot.reply_to(message, f"✅ TP встановлено: {tp_percent}%")
    except:
        bot.reply_to(message, "Вкажи число у відсотках.")

@bot.message_handler(commands=['setsl'])
def set_sl(message):
    global sl_percent
    try:
        sl_percent = float(message.text.split()[1])
        bot.reply_to(message, f"✅ SL встановлено: {sl_percent}%")
    except:
        bot.reply_to(message, "Вкажи число у відсотках.")

@bot.message_handler(commands=['auto'])
def auto(message):
    global auto_trading
    if "on" in message.text:
        auto_trading = True
        bot.reply_to(message, "Автоторгівля увімкнена.")
    elif "off" in message.text:
        auto_trading = False
        bot.reply_to(message, "Автоторгівля вимкнена.")

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global auto_trading
    auto_trading = False
    bot.reply_to(message, "⏹ Бот зупинений.")

def auto_loop():
    while True:
        if auto_trading and markets:
            logging.info("Auto-trading check...")
            # тут буде логіка перевірки і виставлення ордерів
        time.sleep(10)

if __name__ == "__main__":
    logging.info("Бот запущено.")
    bot.polling(none_stop=True)
