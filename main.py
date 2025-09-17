import os
import sys
import json
import time
import hmac
import base64
import hashlib
import logging
import requests
import threading
from typing import Dict, Any, Optional

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_PUBLIC = (os.getenv("API_PUBLIC_KEY") or "").strip()
API_SECRET = (os.getenv("API_SECRET_KEY") or "").strip()
TRADING_ENABLED = (os.getenv("TRADING_ENABLED", "false").lower() in ["1", "true", "yes"])

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WB_PUBLIC = "https://whitebit.com/api/v4/public"
WB_PRIVATE = "https://whitebit.com/api/v4"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

MARKETS = []
DEFAULT_AMOUNT = {}
TP_MAP = {}
SL_MAP = {}
AUTO_TRADE = False

VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}

def log(msg: str):
    logging.info(msg)

def tg_send(chat_id: int, text: str):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception as e:
        log(f"[tg_send] {e}")

def is_valid_market(m: str) -> bool:
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

def make_signature_payload(path: str, data: Optional[Dict[str, Any]] = None):
    data = data or {}
    data["request"] = path
    data["nonce"] = str(int(time.time() * 1000))
    body_json = json.dumps(data, separators=(",", ":"))
    payload_b64 = base64.b64encode(body_json.encode()).decode()
    signature = hmac.new(API_SECRET.encode(), body_json.encode(), hashlib.sha512).hexdigest()
    return body_json, payload_b64, signature

def wb_private_post(path: str, data: Optional[Dict[str, Any]] = None):
    try:
        body_json, payload_b64, signature = make_signature_payload(path, data)
        headers = {
            "Content-Type": "application/json",
            "X-TXC-APIKEY": API_PUBLIC,
            "X-TXC-PAYLOAD": payload_b64,
            "X-TXC-SIGNATURE": signature,
        }
        r = requests.post(f"{WB_PRIVATE}{path}", data=body_json, headers=headers, timeout=30)
        log(f"[WB POST] {path} -> {r.status_code} {r.text[:100]}")
        r.raise_for_status()
        return r.json() if r.text else {}
    except Exception as e:
        log(f"[wb_private_post] {e}")
        return {}

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

def wb_balance():
    return wb_private_post("/api/v4/main-account/balance")

def hourly_report():
    while True:
        try:
            if MARKETS:
                status_lines = []
                for m in MARKETS:
                    if not is_valid_market(m):
                        continue
                    price = wb_price(m)
                    status_lines.append(f"{m}: price={price}, amount={DEFAULT_AMOUNT.get(m,'-')}, TP={TP_MAP.get(m,'-')} SL={SL_MAP.get(m,'-')}")
                if status_lines:
                    tg_send(chat_id=CHAT_ID, text="Hourly report:\n" + "\n".join(status_lines))
        except Exception as e:
            log(f"[hourly_report] {e}")
        time.sleep(3600)

def run_bot():
    global CHAT_ID
    offset = None
    log("Bot started.")
    threading.Thread(target=hourly_report, daemon=True).start()
    while True:
        try:
            resp = requests.get(f"{TG_API}/getUpdates", params={"timeout": 50, "offset": offset}, timeout=80)
            updates = resp.json().get("result", [])
            for u in updates:
                offset = max(offset or 0, u["update_id"] + 1)
                msg = u.get("message") or u.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                CHAT_ID = msg["chat"]["id"]
                text = msg["text"].strip()
                parts = text.split()
                cmd = parts[0].lower()

                if cmd == "/start":
                    tg_send(CHAT_ID, "Bot запущено і автоторгівля активна.")
                elif cmd == "/removemarket":
                    if len(parts) > 1:
                        try:
                            MARKETS.remove(parts[1].upper())
                            tg_send(CHAT_ID, f"Ринок {parts[1]} видалено.")
                        except ValueError:
                            tg_send(CHAT_ID, f"Ринок {parts[1]} відсутній.")
                elif cmd == "/restart":
                    tg_send(CHAT_ID, "Перезапуск...")
                    os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            log(f"[loop] {e}")
            time.sleep(3)

if __name__ == "__main__":
    if not BOT_TOKEN:
        log("BOT_TOKEN відсутній.")
        sys.exit(1)
    run_bot()
