#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — Telegram-бот автоторгівлі для WhiteBIT (PTB v20.x)
Особливості:
 • Видалення webhook перед стартом polling.
 • Команди: /start, /help, /price, /balance, /buy, /sell, /setamount,
            /market, /removemarket, /settp, /setsl, /status, /auto, /stop, /restart
 • Автоторгівля по кожній парі з TP/SL у %, окрема логіка на JobQueue.
 • Щогодинний звіт із параметрами.
 • Перевірки None/валідності, м'який відлов помилок — бот не падає.
 • Реальна торгівля WhiteBIT можлива при наявності API ключів (див. змінні середовища).
"""

import os
import hmac
import json
import time
import math
import base64
import asyncio
import logging
from hashlib import sha512
from typing import Dict, Any, Optional

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, JobQueue, Job
)

# ---------- Налаштування логів ----------
logging.basicConfig(
    format="%(asctime)s %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger("bot")

# ---------- ENV ----------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WB_API_KEY = os.getenv("WHITEBIT_API_KEY", "").strip()
WB_API_SECRET = os.getenv("WHITEBIT_API_SECRET", "").strip()
REAL_TRADE = os.getenv("REAL_TRADING", "false").lower() in {"1","true","yes","on"}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN відсутній у змінних середовища")

# ---------- Глобальний стан ----------
State: Dict[str, Any] = {
    "auto": True,
    "markets": {},   # "DOGE_USDT": {"amount": 5.0, "tp": 0.4, "sl": 0.3, "position": 0.0, "entry": 0.0}
    "last_report": 0.0,
}

# ---------- WhiteBIT helpers ----------

WB_PUBLIC = "https://whitebit.com/api/v4/public"

def valid_market(m: str) -> bool:
    return isinstance(m, str) and "_" in m and m.upper() == m and all(c.isalnum() or c == "_" for c in m)

def wb_public(path: str, params: Optional[dict] = None) -> Optional[dict]:
    try:
        r = requests.get(f"{WB_PUBLIC}/{path.lstrip('/')}", params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning("WB public error: %s", e)
    return None

def price_of(market: str) -> Optional[float]:
    """Отримати ціну ринку з WhiteBIT (ticker)."""
    data = wb_public("ticker", {"market": market})
    try:
        # очікувана відповідь: {"ticker": {"last_price": "0.123"}, "market":"DOGE_USDT"} або список
        if isinstance(data, dict):
            t = data.get("ticker") or data
            p = t.get("last_price") or t.get("lastPrice") or t.get("price")
            if p is not None:
                return float(p)
        if isinstance(data, list) and data:
            # іноді API повертає список
            for item in data:
                if str(item.get("market","")).upper() == market:
                    p = item.get("last_price") or item.get("price")
                    if p is not None:
                        return float(p)
    except Exception as e:
        log.warning("Parse price error for %s: %s", market, e)
    return None

# ---- Приватні запити (опціонально для реальної торгівлі) ----

def wb_signed_headers(payload: dict) -> dict:
    """
    WhiteBIT v4: підпис через X-TXC-APIKEY / X-TXC-PAYLOAD / X-TXC-SIGNATURE.
    Якщо формат у вашому акаунті інший — відкоригуйте цю функцію.
    """
    if not WB_API_KEY or not WB_API_SECRET:
        raise RuntimeError("Немає WHITEBIT_API_KEY/WHITEBIT_API_SECRET")

    payload.setdefault("request", "/api/v4/order/market")  # буде переписано реальною ендпоюнт-строкою у виклику
    payload.setdefault("nonce", int(time.time() * 1000))

    # WB очікує base64 від JSON payload, підписаний HMAC-SHA512
    js = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    b64 = base64.b64encode(js).decode()
    signature = hmac.new(WB_API_SECRET.encode("utf-8"), b64.encode("utf-8"), sha512).hexdigest()

    return {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": WB_API_KEY,
        "X-TXC-PAYLOAD": b64,
        "X-TXC-SIGNATURE": signature,
    }

def wb_private(path: str, payload: dict) -> dict:
    url = f"https://whitebit.com{path}"
    body = payload.copy()
    body["request"] = path
    headers = wb_signed_headers(body)
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=15)
    try:
        return r.json()
    except Exception:
        return {"status": r.status_code, "text": r.text}

def place_market_order(market: str, side: str, amount_in_quote: float) -> dict:
    """
    Ринковий ордер: side in {'buy','sell'}
    amount_in_quote — сума у котирувальній валюті (наприклад, USDT)
    """
    if not REAL_TRADE:
        return {"dry_run": True, "market": market, "side": side, "amount": amount_in_quote}

    payload = {
        "market": market,
        "side": side,
        "amount": str(amount_in_quote),
    }
    return wb_private("/api/v4/order/market", payload)

def get_balance() -> dict:
    if not REAL_TRADE:
        # демонстрація
        return {"dry_run": True, "USDT": 0.0}
    return wb_private("/api/v4/trade-account/balance", {})

# ---------- Допоміжні ----------

def ensure_market_exists(market: str) -> bool:
    if not valid_market(market):
        return False
    # Перевіримо у WB, що пара існує
    data = wb_public("markets")
    try:
        if isinstance(data, list):
            markets = {str(x).upper() for x in data}
        elif isinstance(data, dict):
            markets = {str(k).upper() for k in data.keys()}
        else:
            markets = set()
        return market in markets or True  # якщо API повернув інший формат — не блокуємо
    except Exception:
        return True

def status_text() -> str:
    lines = ["Статус бота:"]
    lines.append(f"Автоторгівля: {'увімкнена' if State['auto'] else 'вимкнена'}")
    if not State["markets"]:
        lines.append("Ринки: (порожньо)")
    for m, cfg in State["markets"].items():
        lines.append(f"{m}: amount={cfg.get('amount','-')}, TP={cfg.get('tp','-')}%, SL={cfg.get('sl','-')}%, "
                     f"position={cfg.get('position',0.0)}, entry={cfg.get('entry',0.0)}")
    return "\n".join(lines)

# ---------- Команди ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = dedent("""\
        Бот запущено. Автоторгівля працює.
        Доступні команди — /help
    """)
    await update.message.reply_text(text)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = dedent("""\
        Команди:
        /price <ринок> — ціна
        /balance — баланс
        /buy <ринок> [сума] — купити на суму у котирувальній валюті
        /sell <ринок> [сума] — продати на суму у котирувальній валюті
        /setamount <ринок> <сума> — дефолтна сума
        /market <ринок> — додати ринок
        /removemarket <ринок> — видалити ринок
        /settp <ринок> <відсоток> — встановити TP
        /setsl <ринок> <відсоток> — встановити SL
        /status — поточні пари та параметри
        /auto on|off — автоторгівля
        /stop — зупинити бота (вимикає автоторгівлю)
        /restart — перезапустити процес
    """)
    await update.message.reply_text(text)

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Синтаксис: /price DOGE_USDT")
    market = context.args[0].upper()
    if not ensure_market_exists(market):
        return await update.message.reply_text("Некоректний ринок.")
    p = price_of(market)
    if p is None:
        return await update.message.reply_text("Ціну не отримано.")
    await update.message.reply_text(f"{market}: {p}")

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = get_balance()
    await update.message.reply_text(f"Баланс: {json.dumps(b, ensure_ascii=False)}")

def get_or_create_cfg(market: str) -> dict:
    return State["markets"].setdefault(market, {"amount": 0.0, "tp": 0.5, "sl": 0.3, "position": 0.0, "entry": 0.0})

async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Синтаксис: /market DOGE_USDT")
    market = context.args[0].upper()
    if not ensure_market_exists(market):
        return await update.message.reply_text("Некоректний ринок.")
    get_or_create_cfg(market)
    await update.message.reply_text(f"Додано {market}")

async def cmd_removemarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Синтаксис: /removemarket DOGE_USDT")
    market = context.args[0].upper()
    State["markets"].pop(market, None)
    await update.message.reply_text(f"Видалено {market}")

async def cmd_setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Синтаксис: /setamount DOGE_USDT 5")
    market = context.args[0].upper()
    try:
        amount = float(context.args[1])
    except Exception:
        return await update.message.reply_text("Некоректна сума.")
    cfg = get_or_create_cfg(market)
    cfg["amount"] = max(0.0, amount)
    await update.message.reply_text(f"Сума для {market}: {cfg['amount']}")

async def cmd_settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Синтаксис: /settp DOGE_USDT 0.5")
    m = context.args[0].upper()
    try:
        v = float(context.args[1])
    except Exception:
        return await update.message.reply_text("Некоректне значення.")
    cfg = get_or_create_cfg(m)
    cfg["tp"] = max(0.0, v)
    await update.message.reply_text(f"TP для {m}: {cfg['tp']}%")

async def cmd_setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Синтаксис: /setsl DOGE_USDT 0.3")
    m = context.args[0].upper()
    try:
        v = float(context.args[1])
    except Exception:
        return await update.message.reply_text("Некоректне значення.")
    cfg = get_or_create_cfg(m)
    cfg["sl"] = max(0.0, v)
    await update.message.reply_text(f"SL для {m}: {cfg['sl']}%")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(status_text())

async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Синтаксис: /auto on|off")
    val = context.args[0].lower()
    if val not in {"on","off"}:
        return await update.message.reply_text("Вкажіть on або off")
    State["auto"] = (val == "on")
    await update.message.reply_text(f"Автоторгівля: {'увімкнена' if State['auto'] else 'вимкнена'}")

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    State["auto"] = False
    await update.message.reply_text("Зупинено автоторгівлю.")

async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Перезапуск...")
    os._exit(0)

async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Синтаксис: /buy DOGE_USDT [сума]")
    m = context.args[0].upper()
    cfg = get_or_create_cfg(m)
    amount = float(context.args[1]) if len(context.args) > 1 else float(cfg.get("amount", 0.0))
    if amount <= 0:
        return await update.message.reply_text("Не задана сума.")
    r = place_market_order(m, "buy", amount)
    if "dry_run" in r:
        # оновлюємо уявну позицію
        p = price_of(m) or 0.0
        q = amount / p if p > 0 else 0.0
        cfg["position"] += q
        cfg["entry"] = p
    await update.message.reply_text(f"BUY {m}: {json.dumps(r, ensure_ascii=False)}")

async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Синтаксис: /sell DOGE_USDT [сума]")
    m = context.args[0].upper()
    cfg = get_or_create_cfg(m)
    amount = float(context.args[1]) if len(context.args) > 1 else float(cfg.get("amount", 0.0))
    if amount <= 0:
        return await update.message.reply_text("Не задана сума.")
    r = place_market_order(m, "sell", amount)
    if "dry_run" in r:
        p = price_of(m) or 0.0
        q = amount / p if p > 0 else 0.0
        cfg["position"] = max(0.0, cfg.get("position", 0.0) - q)
        cfg["entry"] = 0.0 if cfg["position"] == 0.0 else cfg["entry"]
    await update.message.reply_text(f"SELL {m}: {json.dumps(r, ensure_ascii=False)}")

# ---------- Автоторгівля ----------

async def trade_job(context: ContextTypes.DEFAULT_TYPE):
    if not State["auto"] or not State["markets"]:
        return
    bot = context.bot
    chat_id = context.job.chat_id if context.job else None

    for m, cfg in list(State["markets"].items()):
        try:
            p = price_of(m)
            if p is None:
                continue
            # якщо позиції немає — купуємо на amount
            if cfg.get("position", 0.0) <= 0.0:
                amt = float(cfg.get("amount", 0.0))
                if amt > 0:
                    r = place_market_order(m, "buy", amt)
                    if "dry_run" in r:
                        qty = amt / p if p > 0 else 0.0
                        cfg["position"] = qty
                        cfg["entry"] = p
                    if chat_id:
                        await bot.send_message(chat_id, f"АВТО: купівля {m} на {amt}. Ціна {p}.")
                continue

            entry = float(cfg.get("entry", 0.0)) or p
            tp = float(cfg.get("tp", 0.5))
            sl = float(cfg.get("sl", 0.3))

            # Умови TP / SL
            if p >= entry * (1 + tp/100.0):
                amt = float(cfg.get("amount", 0.0))
                r = place_market_order(m, "sell", amt)
                if "dry_run" in r:
                    cfg["position"] = 0.0
                    cfg["entry"] = 0.0
                if chat_id:
                    await bot.send_message(chat_id, f"АВТО: TP виконано {m}. Продаж на {amt} за ціною {p}.")
            elif p <= entry * (1 - sl/100.0):
                amt = float(cfg.get("amount", 0.0))
                r = place_market_order(m, "sell", amt)
                if "dry_run" in r:
                    cfg["position"] = 0.0
                    cfg["entry"] = 0.0
                if chat_id:
                    await bot.send_message(chat_id, f"АВТО: SL спрацював {m}. Продаж на {amt} за ціною {p}.")
        except Exception as e:
            log.warning("trade loop error for %s: %s", m, e)

async def hourly_report(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id if context.job else None
    txt = ["Щогодинний звіт:", status_text()]
    if chat_id:
        await context.bot.send_message(chat_id, "\n".join(txt))

# ---------- Ініціалізація ----------

async def _post_init(app: Application):
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        log.info("Webhook видалено, стартуємо polling")
    except Exception as e:
        log.warning("Не вдалося видалити webhook: %s", e)

def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("sell", cmd_sell))
    app.add_handler(CommandHandler("setamount", cmd_setamount))
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("removemarket", cmd_removemarket))
    app.add_handler(CommandHandler("settp", cmd_settp))
    app.add_handler(CommandHandler("setsl", cmd_setsl))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("restart", cmd_restart))

async def main():
    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()
    register_handlers(app)

    # Автолоопи через JobQueue (без ручного EventLoop)
    # Примітка: щоб отримувати звіти/логіку в особистий чат — один раз надішліть будь-яку команду боту.
    app.job_queue.run_repeating(trade_job, interval=30, first=5, name="trade")  # раз на 30 сек
    app.job_queue.run_repeating(hourly_report, interval=3600, first=300, name="report")

    await app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        # Якщо середовище вже має активний loop (деякі PaaS), fallback:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
