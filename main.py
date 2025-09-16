import os, json, time, requests, traceback
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("WHITEBIT_API_KEY")
API_SECRET = os.getenv("WHITEBIT_API_SECRET")

STATE_FILE = "state.json"
STATE = {"markets": [], "amounts": {}, "tp": 1.0, "sl": 1.0, "auto": False, "positions": {}}
START_TIME = time.time()
CHAT_ID = None

BASE_URL = "https://whitebit.com/api/v4"

def load_state():
    global STATE
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                STATE = json.load(f)
        except:
            pass

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(STATE, f)

def get_price(market):
    try:
        r = requests.get(f"{BASE_URL}/public/ticker?market={market}", timeout=10)
        data = r.json()
        return float(data.get("result", {}).get(market, {}).get("last", 0))
    except:
        return None

def get_balance():
    try:
        r = requests.get(f"{BASE_URL}/main-account/balance", headers={"X-TXC-APIKEY": API_KEY}, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

async def send_status(context=None):
    global CHAT_ID
    if not CHAT_ID:
        return
    uptime = int(time.time() - START_TIME)
    uh, um = divmod(uptime // 60, 60)
    text = f"✅ {datetime.now().strftime('%H:%M')} | ⏱ {uh}h {um}m\n"
    for m in STATE["markets"]:
        price = get_price(m)
        amt = STATE["amounts"].get(m, '—')
        text += f"{m}={price or 'н/д'} ({amt})\n"
    text += f"TP={STATE['tp']}% SL={STATE['sl']}% | 🤖 {'ON' if STATE['auto'] else 'OFF'}\n"
    bals = get_balance()
    if bals and 'USDT' in bals:
        text += f"💵 {bals['USDT'].get('available')}"
    else:
        text += "💵 н/д (ключ?)"
    await Bot(BOT_TOKEN).send_message(chat_id=CHAT_ID, text=text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    await send_status()

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    m = context.args[0].upper()
    if m not in STATE["markets"]:
        STATE["markets"].append(m)
        save_state()
    await send_status()

async def setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setamount BTC_USDT 5")
        return
    STATE["amounts"][context.args[0].upper()] = context.args[1]
    save_state()
    await send_status()

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("on/off?")
        return
    STATE["auto"] = context.args[0].lower() == "on"
    save_state()
    await send_status()

async def settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["tp"] = float(context.args[0])
    save_state()
    await send_status()

async def setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["sl"] = float(context.args[0])
    save_state()
    await send_status()

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    await send_status()

def main():
    load_state()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("setamount", setamount))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.add_handler(CommandHandler("settp", settp))
    app.add_handler(CommandHandler("setsl", setsl))
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc().splitlines()[-3:]
        try:
            if CHAT_ID:
                Bot(BOT_TOKEN).send_message(chat_id=CHAT_ID, text="⚠️ Бот зупинено:\n" + "\n".join(err))
        except:
            pass
        raise
