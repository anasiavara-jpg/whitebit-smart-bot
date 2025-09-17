import os
import sys
import time
import json
import hmac
import base64
import hashlib
import requests
import threading
import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Завантаження змінних оточення
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_PUBLIC = (os.getenv("API_PUBLIC_KEY") or "").strip()
API_SECRET = (os.getenv("API_SECRET_KEY") or "").strip()
TRADING_ENABLED = (os.getenv("TRADING_ENABLED", "true").lower() in ["1", "true", "yes"])

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WB_PUBLIC = "https://whitebit.com/api/v4/public"
WB_PRIVATE = "https://whitebit.com/api/v4"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

MARKETS = []
DEFAULT_AMOUNT = {}
TP_MAP = {}
SL_MAP = {}
AUTO_TRADE = True

VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}

def log(msg: str):
    logging.info(msg)

def is_valid_market(m: str) -> bool:
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

def tg_send(chat_id: int, text: str):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception as e:
        log(f"[tg_send] {e}")

def make_signature_payload(path: str, data: Optional[Dict[str, Any]] = None):
    if data is None:
        data = {}
    data = dict(data)
    data["request"] = path
    data["nonce"] = str(int(time.time() * 1000))
    body_json = json.dumps(data, separators=(",", ":"))
    payload_b64 = base64.b64encode(body_json.encode()).decode()
    signature = hmac.new(API_SECRET.encode(), body_json.encode(), hashlib.sha512).hexdigest()
    return body_json, payload_b64, signature, path

def wb_private_post(path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body_json, payload_b64, signature, _ = make_signature_payload(path, data)
    headers = {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_PUBLIC,
        "X-TXC-PAYLOAD": payload_b64,
        "X-TXC-SIGNATURE": signature,
    }
    r = requests.post(f"{WB_PRIVATE}{path}", data=body_json, headers=headers, timeout=30)
    log(f"[WB POST] {path} -> {r.status_code} {r.text[:200]}")
    r.raise_for_status()
    return r.json() if r.text else {}

def wb_price(market: str) -> Optional[float]:
    try:
        r = requests.get(f"{WB_PUBLIC}/ticker", timeout=15)
        r.raise_for_status()
        data = r.json()
        info = data.get(market.upper())
        return float(info["last_price"]) if info else None
    except Exception as e:
        log(f"[wb_price] {e}")
        return None

def wb_balance(ticker: Optional[str] = None) -> Dict[str, str]:
    try:
        payload = {}
        if ticker:
            payload["ticker"] = ticker.upper()
        data = wb_private_post("/api/v4/main-account/balance", payload)
        return {k: v.get("main_balance", "0") for k, v in data.items() if float(v.get("main_balance", "0") or 0) > 0}
    except Exception as e:
        log(f"[wb_balance] {e}")
        return {}

def wb_order_market(market: str, side: str, amount: str) -> Dict[str, Any]:
    payload = {"market": market.upper(), "side": side.lower(), "amount": str(amount)}
    try:
        res = wb_private_post("/api/v4/order/market", payload)
        log(f"[ORDER] {side} {market} amount={amount}")
        return res
    except Exception as e:
        log(f"[wb_order_market] {e}")
        return {}

def auto_trade_loop(chat_id: Optional[int] = None):
    global AUTO_TRADE
    while AUTO_TRADE:
        try:
            for market in [m for m in MARKETS if is_valid_market(m)]:
                p = wb_price(market)
                if p:
                    log(f"[AUTO] {market} price={p}")
            time.sleep(60)
        except Exception as e:
            log(f"[auto_trade_loop] {e}")
            time.sleep(5)

def hourly_report(chat_id: Optional[int] = None):
    while True:
        try:
            report_lines = []
            for m in sorted([x for x in MARKETS if is_valid_market(x)]):
                price = wb_price(m) or "N/A"
                report_lines.append(f"{m}: TP={TP_MAP.get(m,'-')} SL={SL_MAP.get(m,'-')} AMT={DEFAULT_AMOUNT.get(m,'-')} PRICE={price}")
            text = "📊 Щогодинний звіт:
" + "
".join(report_lines)
            if chat_id:
                tg_send(chat_id, text)
            else:
                log(text)
        except Exception as e:
            log(f"[hourly_report] {e}")
        time.sleep(3600)

def run_bot():
    if not BOT_TOKEN:
        log("BOT_TOKEN відсутній.")
        return
    log("Bot is up. Waiting for updates...")

    chat_id = None
    threading.Thread(target=hourly_report, args=(chat_id,), daemon=True).start()
    threading.Thread(target=auto_trade_loop, args=(chat_id,), daemon=True).start()

    offset = None
    while True:
        try:
            resp = requests.get(f"{TG_API}/getUpdates", params={"timeout": 50, "offset": offset}, timeout=80)
            resp.raise_for_status()
            updates = resp.json().get("result", [])
            for u in updates:
                offset = max(offset or 0, u["update_id"] + 1)
                msg = u.get("message") or u.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = msg["text"].strip()
                parts = text.split()
                cmd = parts[0].lower()

                if cmd in ("/start", "/help"):
                    tg_send(chat_id, "✅ Бот запущений. Автоторгівля увімкнена.")
                elif cmd == "/removemarket":
                    if len(parts) < 2:
                        tg_send(chat_id, "Приклад: /removemarket BTC_USDT")
                        continue
                    m = parts[1].upper()
                    if m in MARKETS:
                        MARKETS.remove(m)
                        tg_send(chat_id, f"{m} видалено зі списку.")
                    else:
                        tg_send(chat_id, f"{m} немає у списку.")
                elif cmd == "/restart":
                    tg_send(chat_id, "♻️ Перезапуск бота...")
                    os.execv(sys.executable, ["python"] + sys.argv)
                elif cmd == "/price":
                    if len(parts) < 2:
                        tg_send(chat_id, "Приклад: /price BTC_USDT")
                        continue
                    market = parts[1].upper()
                    p = wb_price(market)
                    tg_send(chat_id, f"{market}: {p}" if p else f"Ринок {market} не знайдено.")
                elif cmd == "/balance":
                    ticker = parts[1] if len(parts) > 1 else None
                    bals = wb_balance(ticker)
                    if not bals:
                        tg_send(chat_id, "Баланс порожній або 0.")
                    else:
                        lines = [f"{k}: {v}" for k, v in bals.items()]
                        tg_send(chat_id, "Баланс:
" + "\n".join(lines))
                elif cmd in ("/buy", "/sell"):
                    if len(parts) < 3:
                        tg_send(chat_id, f"Приклад: {cmd} BTC_USDT 5")
                        continue
                    market = parts[1].upper()
                    amount = parts[2]
                    side = "buy" if cmd == "/buy" else "sell"
                    res = wb_order_market(market, side, amount)
                    tg_send(chat_id, f"Ордер {side} {market} -> {res}")
                else:
                    tg_send(chat_id, "Невідома команда.")

        except Exception as e:
            log(f"[loop] {e}")
            time.sleep(3)

if __name__ == "__main__":
    run_bot()
