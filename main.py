#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhiteBIT Smart Bot — Telegram control
"""
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

import aiohttp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, AIORateLimiter,
)

# ---------- Configuration ----------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # optional
REPORT_EVERY_SECONDS = int(os.getenv("REPORT_EVERY_SECONDS", "3600"))

WB_API_KEY = os.getenv("WHITEBIT_API_KEY")
WB_API_SECRET = os.getenv("WHITEBIT_API_SECRET")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("smart-bot")

@dataclass
class PairConfig:
    amount: float = 5.0
    tp: float = 0.5
    sl: float = 0.3

@dataclass
class State:
    auto: bool = True
    pairs: Dict[str, PairConfig] = field(default_factory=dict)

state = State()

VALID_RE = re.compile(r"^[A-Z0-9_]{3,25}$")

def is_valid_pair(symbol: str) -> bool:
    if not symbol:
        return False
    s = symbol.upper().strip()
    if not VALID_RE.match(s):
        return False
    if "_" not in s:
        return False
    base, quote = s.split("_", 1)
    return bool(base and quote)

async def http_json(session: aiohttp.ClientSession, method: str, url: str, **kw):
    try:
        async with session.request(method, url, timeout=aiohttp.ClientTimeout(total=15), **kw) as r:
            r.raise_for_status()
            return await r.json(content_type=None)
    except Exception as e:
        log.error("HTTP fail %s %s: %s", method, url, e)
        return None

async def wb_ticker(session: aiohttp.ClientSession, pair: str):
    url = f"https://whitebit.com/api/v4/public/ticker?market={pair}"
    data = await http_json(session, "GET", url)
    if isinstance(data, dict) and pair in data:
        d = data[pair]
        try:
            return float(d.get("last_price") or d.get("last") or d.get("price") or 0.0)
        except Exception:
            return None
    if isinstance(data, list) and data:
        d = data[0]
        try:
            return float(d.get("last_price") or d.get("last") or d.get("price") or 0.0)
        except Exception:
            return None
    return None

def format_status() -> str:
    lines = ["Статус:"]
    for m, cfg in state.pairs.items():
        lines.append(f"{m}: TP={cfg.tp}% SL={cfg.sl}% Amt={cfg.amount}")
    if len(lines) == 1:
        lines.append("— Порожньо. Додай ринок через /market <ринок>.")
    return "\n".join(lines)

async def tg_send(context: ContextTypes.DEFAULT_TYPE, text: str, chat_id: Optional[int|str]=None):
    try:
        if not text:
            return
        cid = chat_id or ADMIN_CHAT_ID
        if cid:
            await context.bot.send_message(chat_id=cid, text=text)
    except Exception as e:
        log.warning("tg_send failed: %s", e)

# ---------- Commands ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state.auto = True
    await update.message.reply_text("✅ Бот запущено. Автоторгівля УВІМКНЕНА.")
    await cmd_help(update, context)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "/price <ринок>\n"
        "/balance\n"
        "/buy <ринок> <сума>\n"
        "/sell <ринок> <кількість>\n"
        "/market <ринок>\n"
        "/removemarket <ринок>\n"
        "/setamount <ринок> <сума>\n"
        "/settp <ринок> <відсоток>\n"
        "/setsl <ринок> <відсоток>\n"
        "/auto on|off\n"
        "/status\n"
        "/restart\n"
        "/stop"
    )
    await update.message.reply_text(txt)

async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Вкажи ринок: /market BTC_USDT")
    pair = context.args[0].upper()
    if not is_valid_pair(pair):
        return await update.message.reply_text("Невалідний ринок. Приклад: BTC_USDT")
    state.pairs.setdefault(pair, PairConfig())
    await update.message.reply_text(f"✅ Додано {pair}")

async def cmd_remove_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Вкажи ринок: /removemarket BTC_USDT")
    pair = context.args[0].upper()
    if state.pairs.pop(pair, None) is not None:
        await update.message.reply_text(f"🗑 Видалено {pair}")
    else:
        await update.message.reply_text("Немає такого ринку в списку.")

async def cmd_setamount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Приклад: /setamount BTC_USDT 5")
    pair, amount_s = context.args[0].upper(), context.args[1]
    if not is_valid_pair(pair):
        return await update.message.reply_text("Невалідний ринок.")
    try:
        amt = float(amount_s)
    except Exception:
        return await update.message.reply_text("Сума має бути числом.")
    state.pairs.setdefault(pair, PairConfig()).amount = amt
    await update.message.reply_text(f"Сума для {pair}: {amt}")

async def cmd_settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Приклад: /settp BTC_USDT 0.5")
    pair, p = context.args[0].upper(), context.args[1]
    try:
        val = float(p)
    except Exception:
        return await update.message.reply_text("Відсоток має бути числом.")
    state.pairs.setdefault(pair, PairConfig()).tp = val
    await update.message.reply_text(f"TP для {pair}: {val}%")

async def cmd_setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Приклад: /setsl BTC_USDT 0.3")
    pair, p = context.args[0].upper(), context.args[1]
    try:
        val = float(p)
    except Exception:
        return await update.message.reply_text("Відсоток має бути числом.")
    state.pairs.setdefault(pair, PairConfig()).sl = val
    await update.message.reply_text(f"SL для {pair}: {val}%")

async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Вкажи on або off: /auto on")
    state.auto = (context.args[0].lower() == "on")
    await update.message.reply_text(f"Автоторгівля: {'увімкнена' if state.auto else 'вимкнена'}")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_status())

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (WB_API_KEY and WB_API_SECRET):
        return await update.message.reply_text("Баланс недоступний: не налаштовані WHITEBIT_API_KEY/SECRET.")
    await update.message.reply_text("Баланс (демо).")

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Приклад: /price BTC_USDT")
    pair = context.args[0].upper()
    async with aiohttp.ClientSession() as s:
        price = await wb_ticker(s, pair)
    await update.message.reply_text(f"{pair}: {price if price is not None else '—'}")

async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Купівля (демо).")

async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Продаж (демо).")

async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("♻️ Перезапуск...")
    # Render will restart the process after exit code 0
    os._exit(0)

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔️ Зупинка...")
    os._exit(0)

# ---------- Jobs ----------
async def hourly_report(context: ContextTypes.DEFAULT_TYPE):
    await tg_send(context, "📊 Щогодинний звіт:\n" + format_status())

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    if not state.auto or not state.pairs:
        return
    async with aiohttp.ClientSession() as s:
        for pair, cfg in list(state.pairs.items()):
            if not is_valid_pair(pair):
                continue
            try:
                price = await wb_ticker(s, pair)
                if price is None:
                    continue
                log.info("Auto %s price=%.8f tp=%.3f sl=%.3f amt=%.4f", pair, price, cfg.tp, cfg.sl, cfg.amount)
            except Exception as e:
                log.error("[AUTO LOOP] %s: %s", pair, e)

def build_app():
    if not BOT_TOKEN:
        log.error("❗️ BOT_TOKEN відсутній у змінних середовища.")
    app = ApplicationBuilder().token(BOT_TOKEN).rate_limiter(AIORateLimiter()).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("removemarket", cmd_remove_market))
    app.add_handler(CommandHandler("setamount", cmd_setamount))
    app.add_handler(CommandHandler("settp", cmd_settp))
    app.add_handler(CommandHandler("setsl", cmd_setsl))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("sell", cmd_sell))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("stop", cmd_stop))

    jq = app.job_queue
    jq.run_repeating(hourly_report, interval=REPORT_EVERY_SECONDS, first=30, name="hourly_report")
    jq.run_repeating(auto_job, interval=10, first=10, name="auto_job")

    async def on_startup(app):
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            log.warning("delete_webhook failed: %s", e)
    app.post_init = on_startup
    return app

if __name__ == "__main__":
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
