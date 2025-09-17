# -*- coding: utf-8 -*-
"""
Telegram → WhiteBIT trading bot
- Реальна торгівля (якщо TRADING_ENABLED=true і є ключі)
- Керування через команди в Telegram
- Автоторгівля з TP/SL і повідомленнями
- Захист від подвійного запуску (file lock)
- Щогодинний звіт
"""

import os
import sys
import json
import hmac
import time
import fcntl
import base64
import hashlib
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue

# ========= Env & Logging =========

load_dotenv()
BOT_TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
API_PUBLIC = (os.getenv("API_PUBLIC_KEY") or os.getenv("API_PUBLIC") or "").strip()
API_SECRET = (os.getenv("API_SECRET_KEY") or os.getenv("API_SECRET") or "").strip()
TRADING_ENABLED = (os.getenv("TRADING_ENABLED", "false").lower() in ["1", "true", "yes"])
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip() or None  # можна не задавати — візьмемо з /start

if not BOT_TOKEN:
    print("🔴 BOT_TOKEN порожній. Додай BOT_TOKEN у Environment.")
    sys.exit(0)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("tg-wb-bot")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WB_PUBLIC = "https://whitebit.com/api/v4/public"
WB_PRIVATE = "https://whitebit.com/api/v4"

# ========= Runtime State =========

VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}

MARKETS = []  # наприклад: ["BTC_USDT", "DOGE_USDT"]
DEFAULT_AMOUNT: Dict[str, float] = {}  # сума в quote для buy
TP_MAP: Dict[str, float] = {}          # %
SL_MAP: Dict[str, float] = {}          # %

AUTO_TRADE = True  # за замовчуванням автоторгівля увімкнена
LAST_PRICES: Dict[str, float] = {}
OPEN_POS: Dict[str, Dict[str, float]] = {}  # market -> {"buy_price": float, "qty": float}

# тригер покупки, якщо ціна впала на X% від попередньої спостереженої
DEFAULT_BUY_TRIGGER_PCT = float(os.getenv("BUY_TRIGGER_PCT", "0.4"))  # 0.4% за замовчуванням

# ========= Helpers =========

def is_valid_market(m: str) -> bool:
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

def normalize_market(s: str) -> str:
    s = s.strip().upper()
    if "_" not in s:
        s = f"{s}_USDT"
    return s

def tg_send(chat_id: int, text: str):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)
    except Exception as e:
        log.error(f"[tg_send] {e}")

def price_fetch(market: str) -> Optional[float]:
    try:
        r = requests.get(f"{WB_PUBLIC}/ticker", params={"market": market}, timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        if market in data and "last_price" in data[market]:
            return float(data[market]["last_price"])
        return None
    except Exception as e:
        log.error(f"[price] {market}: {e}")
        return None

# ========= WhiteBIT private =========

def validate_keys() -> Optional[str]:
    problems = []
    if not API_PUBLIC:
        problems.append("API_PUBLIC_KEY порожній")
    if not API_SECRET:
        problems.append("API_SECRET_KEY порожній")
    if problems:
        return "; ".join(problems)
    return None

def make_signature_payload(path: str, data: Optional[Dict[str, Any]] = None):
    if data is None:
        data = {}
    d = dict(data)
    d["request"] = path
    d["nonce"] = str(int(time.time() * 1000))
    body_json = json.dumps(d, separators=(",", ":"))
    payload_b64 = base64.b64encode(body_json.encode()).decode()
    # Захист від NoneType.encode
    if not API_SECRET:
        raise ValueError("API_SECRET відсутній")
    signature = hmac.new(API_SECRET.encode(), body_json.encode(), hashlib.sha512).hexdigest()
    return body_json, payload_b64, signature

def wb_private_post(path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        body_json, payload_b64, signature = make_signature_payload(path, payload)
    except Exception as e:
        log.error(f"[WB SIGN] {e}")
        return {}
    headers = {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_PUBLIC,
        "X-TXC-PAYLOAD": payload_b64,
        "X-TXC-SIGNATURE": signature,
    }
    try:
        r = requests.post(f"{WB_PRIVATE}{path}", data=body_json, headers=headers, timeout=30)
        log.info(f"[WB POST] {path} -> {r.status_code}")
        if r.text:
            log.debug(f"[WB RESP] {r.text[:500]}")
        r.raise_for_status()
        return r.json() if r.text else {}
    except Exception as e:
        log.error(f"[WB POST] {path} error: {e}")
        return {}

def wb_order_market(market: str, side: str, amount: float) -> Dict[str, Any]:
    # BUY: 'amount' у WhiteBIT для market buy — це обсяг у quote (USDT)
    # SELL: 'amount' — це кількість base
    payload = {"market": market.upper(), "side": side.lower(), "amount": str(amount)}
    return wb_private_post("/api/v4/order/market", payload)

def wb_balance(ticker: Optional[str] = None) -> Dict[str, str]:
    payload = {}
    if ticker:
        payload["ticker"] = ticker.upper()
    data = wb_private_post("/api/v4/main-account/balance", payload)
    # повернемо тільки позитивні баланси
    res = {}
    for k, v in (data or {}).items():
        bal = v.get("main_balance") or "0"
        try:
            if float(bal) > 0:
                res[k] = bal
        except:
            pass
    return res

# ========= File lock (single instance) =========

LOCK_PATH = "/tmp/tg_wb_bot.lock"
def acquire_lock_or_exit():
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, str(os.getpid()).encode())
    except OSError:
        log.error("⚠️ Інший інстанс бота вже працює (lock). Вихід без падіння.")
        sys.exit(0)

# ========= Telegram commands =========

HELP = (
    "🤖 WhiteBIT бот\n\n"
    "/price <ринок> — ціна (напр. /price BTC_USDT)\n"
    "/balance [тикер] — баланс(и)\n"
    "/buy <ринок> <сума_quote> — ринкова покупка\n"
    "/sell <ринок> <кількість_base> — ринковий продаж\n"
    "/market <ринок> — додати ринок у список\n"
    "/removemarket <ринок> — видалити ринок\n"
    "/setamount <ринок> <сума_quote> — дефолтна сума для buy\n"
    "/settp <ринок> <pct> — TP у %\n"
    "/setsl <ринок> <pct> — SL у %\n"
    "/auto on|off — увімк/вимк автоторгівлю\n"
    "/status — статус параметрів\n"
    "/restart — перезапуск процесу\n"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_CHAT_ID
    chat_id = update.effective_chat.id
    if not ADMIN_CHAT_ID:
        ADMIN_CHAT_ID = str(chat_id)
    await update.message.reply_text("✅ Бот запущено. Напиши /help для списку команд.")
    await update.message.reply_text(HELP)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)

async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /price BTC_USDT")
        return
    m = normalize_market(context.args[0])
    p = price_fetch(m)
    await update.message.reply_text(f"{m}: {p}" if p else f"Не вдалося отримати ціну для {m}")

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker = context.args[0] if context.args else None
    if not TRADING_ENABLED:
        await update.message.reply_text("Баланс недоступний: TRADING_ENABLED=false")
        return
    problem = validate_keys()
    if problem:
        await update.message.reply_text(f"❌ {problem}")
        return
    bals = wb_balance(ticker)
    if not bals:
        await update.message.reply_text("Немає позитивних балансів або помилка API.")
    else:
        lines = [f"{k}: {v}" for k, v in bals.items()]
        await update.message.reply_text("Баланс:\n" + "\n".join(lines))

async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /buy BTC_USDT 5  (5 USDT)")
        return
    market = normalize_market(context.args[0])
    amount_q = float(context.args[1])
    if not TRADING_ENABLED:
        await update.message.reply_text(f"[SIM] Покупка {market} на {amount_q} (TRADING_DISABLED)")
        return
    prob = validate_keys()
    if prob:
        await update.message.reply_text(f"❌ {prob}")
        return
    try:
        res = wb_order_market(market, "buy", amount_q)
        await update.message.reply_text(f"✅ BUY {market} {amount_q} (quote). Відповідь: {res}")
        # нотифікація про угоду
        if ADMIN_CHAT_ID:
            tg_send(int(ADMIN_CHAT_ID), f"🟢 BUY {market} {amount_q} (quote)")
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка BUY: {e}")

async def sell_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /sell BTC_USDT 0.001  (0.001 BTC)")
        return
    market = normalize_market(context.args[0])
    qty_base = float(context.args[1])
    if not TRADING_ENABLED:
        await update.message.reply_text(f"[SIM] Продаж {market} {qty_base} (TRADING_DISABLED)")
        return
    prob = validate_keys()
    if prob:
        await update.message.reply_text(f"❌ {prob}")
        return
    try:
        res = wb_order_market(market, "sell", qty_base)
        await update.message.reply_text(f"✅ SELL {market} {qty_base} base. Відповідь: {res}")
        if ADMIN_CHAT_ID:
            tg_send(int(ADMIN_CHAT_ID), f"🔴 SELL {market} {qty_base} base")
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка SELL: {e}")

async def market_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /market BTC_USDT")
        return
    m = normalize_market(context.args[0])
    if not is_valid_market(m):
        await update.message.reply_text("❌ Невалідний ринок. Дозволені quote: USDT/USDC/BTC/ETH")
        return
    if m not in MARKETS:
        MARKETS.append(m)
    await update.message.reply_text(f"✅ Додано {m}. Поточні: {', '.join(MARKETS)}")

async def removemarket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Приклад: /removemarket BTC_USDT")
        return
    m = normalize_market(context.args[0])
    removed = False
    if m in MARKETS:
        MARKETS.remove(m); removed = True
    for mp in (TP_MAP, SL_MAP, DEFAULT_AMOUNT):
        mp.pop(m, None)
    await update.message.reply_text("🗑 Видалено " + m if removed else f"ℹ️ {m} не у списку")

async def setamount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setamount BTC_USDT 10")
        return
    m = normalize_market(context.args[0])
    try:
        amt = float(context.args[1])
        DEFAULT_AMOUNT[m] = amt
        await update.message.reply_text(f"✅ {m}: сума quote = {amt}")
    except:
        await update.message.reply_text("❌ Вкажи число для суми")

async def settp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /settp BTC_USDT 0.5")
        return
    m = normalize_market(context.args[0])
    try:
        TP_MAP[m] = float(context.args[1])
        await update.message.reply_text(f"✅ {m}: TP = {TP_MAP[m]}%")
    except:
        await update.message.reply_text("❌ Вкажи число для TP")

async def setsl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Приклад: /setsl BTC_USDT 0.3")
        return
    m = normalize_market(context.args[0])
    try:
        SL_MAP[m] = float(context.args[1])
        await update.message.reply_text(f"✅ {m}: SL = {SL_MAP[m]}%")
    except:
        await update.message.reply_text("❌ Вкажи число для SL")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE
    if not context.args:
        await update.message.reply_text(f"Автоторгівля: {'ON' if AUTO_TRADE else 'OFF'}")
        return
    v = context.args[0].lower()
    if v == "on":
        AUTO_TRADE = True; await update.message.reply_text("✅ AUTO ON")
    elif v == "off":
        AUTO_TRADE = False; await update.message.reply_text("⏸ AUTO OFF")
    else:
        await update.message.reply_text("Вкажи on|off")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valid = [m for m in MARKETS if is_valid_market(m)]
    if not valid:
        await update.message.reply_text("Список ринків порожній.")
        return
    lines = ["📋 Статус:"]
    for m in valid:
        lines.append(f"{m}: Amt={DEFAULT_AMOUNT.get(m,'-')} TP={TP_MAP.get(m,'-')} SL={SL_MAP.get(m,'-')}")
    await update.message.reply_text("\n".join(lines))

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("♻️ Перезапуск...")
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await update.message.reply_text(f"❌ Не вдалось перезапустити: {e}")

# ========= Jobs =========

async def hourly_report_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else None
    lines = ["⏳ Щогодинний звіт:"]
    for m in [x for x in MARKETS if is_valid_market(x)]:
        p = price_fetch(m)
        lines.append(f"{m}: Price={p} Amt={DEFAULT_AMOUNT.get(m,'-')} TP={TP_MAP.get(m,'-')} SL={SL_MAP.get(m,'-')}")
    txt = "\n".join(lines)
    if chat_id:
        try:
            await context.bot.send_message(chat_id=chat_id, text=txt)
        except Exception as e:
            log.error(f"[hourly_report] {e}")
    else:
        log.info(txt)

def pct_change(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old * 100.0

async def auto_trade_job(context: ContextTypes.DEFAULT_TYPE):
    if not AUTO_TRADE:
        return
    problem = validate_keys() if TRADING_ENABLED else None
    # Не блокуємо торговий цикл для відсутніх ключів, просто симулюємо
    for m in [x for x in MARKETS if is_valid_market(x)]:
        try:
            p = price_fetch(m)
            if p is None:
                continue
            prev = LAST_PRICES.get(m, p)
            LAST_PRICES[m] = p
            # Відкриття позиції при падінні >= DEFAULT_BUY_TRIGGER_PCT
            if m not in OPEN_POS:
                drop = pct_change(p, prev)
                if drop <= -DEFAULT_BUY_TRIGGER_PCT:
                    amt_q = float(DEFAULT_AMOUNT.get(m, 0))
                    if amt_q > 0:
                        if TRADING_ENABLED and not problem:
                            res = wb_order_market(m, "buy", amt_q)
                            log.info(f"[BUY] {m} {amt_q} -> {res}")
                        else:
                            log.info(f"[SIM BUY] {m} {amt_q}")
                        qty = amt_q / p
                        OPEN_POS[m] = {"buy_price": p, "qty": qty}
                        # нотифікація
                        chat_id = int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else None
                        if chat_id:
                            await context.bot.send_message(chat_id, f"🟢 BUY {m} @ {p:.6f} qty≈{qty:.6f}")
            else:
                # Закриття по TP / SL
                buy_p = OPEN_POS[m]["buy_price"]
                qty = OPEN_POS[m]["qty"]
                tp_pct = float(TP_MAP.get(m, 0) or 0)
                sl_pct = float(SL_MAP.get(m, 0) or 0)
                up = pct_change(p, buy_p)
                down = -up
                do_sell = False
                reason = ""
                if tp_pct > 0 and up >= tp_pct:
                    do_sell = True; reason = f"TP {tp_pct}%"
                if not do_sell and sl_pct > 0 and down >= sl_pct:
                    do_sell = True; reason = f"SL {sl_pct}%"
                if do_sell:
                    if TRADING_ENABLED and not problem:
                        res = wb_order_market(m, "sell", qty)
                        log.info(f"[SELL] {m} {qty} -> {res}")
                    else:
                        log.info(f"[SIM SELL] {m} {qty} ({reason})")
                    chat_id = int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else None
                    if chat_id:
                        await context.bot.send_message(chat_id, f"🔴 SELL {m} @ {p:.6f} qty={qty:.6f} ({reason})")
                    OPEN_POS.pop(m, None)
        except Exception as e:
            # не валимо весь цикл — лише пропускаємо проблемну пару
            log.error(f"[AUTO LOOP] {m}: {e}")
            chat_id = int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else None
            if chat_id:
                try:
                    await context.bot.send_message(chat_id, f"⚠️ Помилка на {m}: {e}. Пара пропущена, інші працюють.")
                except Exception as ee:
                    log.error(f"[notify error] {ee}")

# ========= App bootstrap =========

def main():
    acquire_lock_or_exit()

    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("price", price_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("sell", sell_cmd))
    app.add_handler(CommandHandler("market", market_cmd))
    app.add_handler(CommandHandler("removemarket", removemarket_cmd))
    app.add_handler(CommandHandler("setamount", setamount_cmd))
    app.add_handler(CommandHandler("settp", settp_cmd))
    app.add_handler(CommandHandler("setsl", setsl_cmd))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))

    # Jobs
    jq: JobQueue = app.job_queue
    jq.run_repeating(hourly_report_job, interval=3600, first=30, name="hourly_report")
    jq.run_repeating(auto_trade_job, interval=5, first=5, name="auto_trade")

    # Якщо ключі відсутні — попереджаємо, але працюємо в симуляції
    prob = validate_keys() if TRADING_ENABLED else None
    if prob and ADMIN_CHAT_ID:
        try:
            import asyncio
            asyncio.get_event_loop().create_task(app.bot.send_message(int(ADMIN_CHAT_ID), f"⚠️ {prob}. Торгівля у SIM режимі."))
        except Exception:
            pass

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
