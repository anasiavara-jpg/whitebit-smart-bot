
import os
import sys
import time
import json
import hmac
import base64
import hashlib
import logging
import requests
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

# --- Логування ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("bot")

# --- Глобальні змінні ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_PUBLIC = (os.getenv("API_PUBLIC_KEY") or "").strip()
API_SECRET = (os.getenv("API_SECRET_KEY") or "").strip()
TRADING_ENABLED = (os.getenv("TRADING_ENABLED", "false").lower() in ["1", "true", "yes"])

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WB_PUBLIC = "https://whitebit.com/api/v4/public"
WB_PRIVATE = "https://whitebit.com/api/v4"

MARKETS = []
DEFAULT_AMOUNT = {}
TP_MAP = {}
SL_MAP = {}
AUTO_TRADE = False
VALID_QUOTE_ASSETS = {"USDT", "USDC", "BTC", "ETH"}
last_report_time = datetime.utcnow()

# --- Перевірка токена ---
if not BOT_TOKEN:
    log.error("BOT_TOKEN не знайдений! Додай його в Environment.")
    sys.exit(1)

# --- Перевірка дубля ---
LOCK_FILE = "/tmp/whitebit_bot.lock"
if os.path.exists(LOCK_FILE):
    log.error("⚠️ Бот уже запущений. Завершую.")
    sys.exit(1)
open(LOCK_FILE, "w").close()

def tg_send(chat_id: int, text: str):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception as e:
        log.error(f"[tg_send] {e}")

def make_signature_payload(path: str, data: Optional[Dict[str, Any]] = None):
    if data is None:
        data = {}
    data["request"] = path
    data["nonce"] = str(int(time.time() * 1000))
    body_json = json.dumps(data, separators=(",", ":"))
    payload_b64 = base64.b64encode(body_json.encode()).decode()
    signature = hmac.new(API_SECRET.encode(), body_json.encode(), hashlib.sha512).hexdigest()
    return body_json, payload_b64, signature, path

def wb_private_post(path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        body_json, payload_b64, signature, _ = make_signature_payload(path, data)
        headers = {
            "Content-Type": "application/json",
            "X-TXC-APIKEY": API_PUBLIC,
            "X-TXC-PAYLOAD": payload_b64,
            "X-TXC-SIGNATURE": signature,
        }
        r = requests.post(f"{WB_PRIVATE}{path}", data=body_json, headers=headers, timeout=30)
        log.info(f"[WB POST] {path} -> {r.status_code}")
        r.raise_for_status()
        return r.json() if r.text else {}
    except Exception as e:
        log.error(f"Помилка запиту: {e}")
        tg_send(get_main_chat_id(), f"❌ Помилка запиту: {e}")
        return {}

def wb_price(market: str) -> Optional[float]:
    try:
        r = requests.get(f"{WB_PUBLIC}/ticker", timeout=15)
        r.raise_for_status()
        data = r.json()
        info = data.get(market.upper())
        return float(info["last_price"]) if info else None
    except Exception as e:
        log.error(f"Помилка отримання ціни {market}: {e}")
        tg_send(get_main_chat_id(), f"⚠️ Не вдалося отримати ціну для {market}")
        return None

def is_valid_market(m: str) -> bool:
    if "_" not in m:
        return False
    base, quote = m.split("_", 1)
    return bool(base) and quote in VALID_QUOTE_ASSETS

def auto_report():
    global last_report_time
    while True:
        if datetime.utcnow() - last_report_time >= timedelta(hours=1):
            report_text = "📊 Щогодинний звіт:
"
            for m in MARKETS:
                if not is_valid_market(m): continue
                price = wb_price(m) or "N/A"
                report_text += f"{m}: TP={TP_MAP.get(m, '—')} SL={SL_MAP.get(m, '—')} Сума={DEFAULT_AMOUNT.get(m, '—')} Ціна={price}
"
            tg_send(get_main_chat_id(), report_text)
            last_report_time = datetime.utcnow()
        time.sleep(60)

def get_main_chat_id() -> int:
    return int(os.getenv("MAIN_CHAT_ID", "0"))

def run_bot():
    log.info("✅ Бот запущено. Очікування команд...")
    threading.Thread(target=auto_report, daemon=True).start()
    while True:
        try:
            resp = requests.get(f"{TG_API}/getUpdates", timeout=50)
            updates = resp.json().get("result", [])
            for u in updates:
                msg = u.get("message") or u.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = msg["text"].strip()
                if text.startswith("/restart"):
                    tg_send(chat_id, "♻️ Перезапуск...")
                    os.remove(LOCK_FILE)
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                elif text.startswith("/removemarket"):
                    parts = text.split()
                    if len(parts) < 2:
                        tg_send(chat_id, "Приклад: /removemarket BTC_USDT")
                        continue
                    m = parts[1].upper()
                    if m in MARKETS:
                        MARKETS.remove(m)
                        tg_send(chat_id, f"🗑 Видалено {m}")
                    else:
                        tg_send(chat_id, f"{m} не знайдено")
                # ... інші команди ...
        except Exception as e:
            log.error(f"[loop] {e}")
            time.sleep(3)

if __name__ == "__main__":
    try:
        run_bot()
    finally:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
