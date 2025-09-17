import os
import sys
import json
import hmac
import time
import base64
import hashlib
import logging
import requests
import threading
from typing import Dict, Any, Optional

# --- Налаштування ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_PUBLIC = (os.getenv("API_PUBLIC_KEY") or os.getenv("API_PUBLIC") or "").strip()
API_SECRET = (os.getenv("API_SECRET_KEY") or os.getenv("API_SECRET") or "").strip()
TRADING_ENABLED = (os.getenv("TRADING_ENABLED", "false").lower() in ["1", "true", "yes"])

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WB_PUBLIC = "https://whitebit.com/api/v4/public"
WB_PRIVATE = "https://whitebit.com/api/v4"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# --- Глобальні змінні ---
MARKETS = []
DEFAULT_AMOUNT = {}
TP_MAP = {}
SL_MAP = {}
AUTO_TRADE = False
VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}

def is_valid_market(m: str) -> bool:
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

# --- Утиліти ---
def tg_send(chat_id: int, text: str):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception as e:
        log.error(f"[tg_send] {e}")

def make_signature_payload(path: str, data: Optional[Dict[str, Any]] = None):
    data = data or {}
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
    log.info(f"[WB POST] {path} -> {r.status_code}")
    if r.status_code != 200:
        log.error(f"WhiteBIT error: {r.text}")
    return r.json() if r.text else {}

def wb_price(market: str) -> Optional[float]:
    try:
        r = requests.get(f"{WB_PUBLIC}/ticker", timeout=15)
        data = r.json()
        info = data.get(market.upper())
        return float(info["last_price"]) if info else None
    except Exception as e:
        log.error(f"[wb_price] {e}")
        return None

def wb_balance() -> Dict[str, str]:
    try:
        data = wb_private_post("/api/v4/main-account/balance")
        return {k: v.get("main_balance", "0") for k, v in data.items() if float(v.get("main_balance", "0") or 0) > 0}
    except Exception as e:
        log.error(f"[wb_balance] {e}")
        return {}

def wb_order_market(market: str, side: str, amount: str) -> Dict[str, Any]:
    try:
        payload = {"market": market.upper(), "side": side.lower(), "amount": str(amount)}
        return wb_private_post("/api/v4/order/market", payload)
    except Exception as e:
        log.error(f"[wb_order_market] {e}")
        return {"error": str(e)}

# --- Основна логіка ---
def auto_trade_loop():
    while AUTO_TRADE:
        try:
            for market in [m for m in MARKETS if is_valid_market(m)]:
                price = wb_price(market)
                if not price:
                    continue
                tp = TP_MAP.get(market)
                sl = SL_MAP.get(market)
                amount = DEFAULT_AMOUNT.get(market, 0)
                log.info(f"[AUTO] {market} price={price}, TP={tp}, SL={sl}, amount={amount}")
                # тут може бути логіка купівлі/продажу при досягненні TP/SL
            time.sleep(60)
        except Exception as e:
            log.error(f"[auto_trade_loop] {e}")
            time.sleep(5)

def hourly_report(chat_id: int):
    while True:
        try:
            bals = wb_balance()
            prices = [f"{m}: {wb_price(m)}" for m in MARKETS if is_valid_market(m)]
            tg_send(chat_id, "📊 Звіт:
" + "
".join(prices) + f"
Баланс: {bals}")
            time.sleep(3600)
        except Exception as e:
            log.error(f"[hourly_report] {e}")
            time.sleep(3600)

def start_bot():
    global AUTO_TRADE
    AUTO_TRADE = True
    threading.Thread(target=auto_trade_loop, daemon=True).start()
    log.info("Bot started. Autotrading ON.")

def stop_bot():
    global AUTO_TRADE
    AUTO_TRADE = False
    log.info("Bot stopped.")

if __name__ == "__main__":
    if not BOT_TOKEN:
        log.error("BOT_TOKEN не знайдений.")
        sys.exit(1)
    log.info("Bot is up and running.")
    start_bot()
