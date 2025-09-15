import os
import time
import json
import hmac
import base64
import hashlib
import requests
from typing import Dict, Any, Optional

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_PUBLIC = (os.getenv("API_PUBLIC_KEY") or os.getenv("API_PUBLIC") or os.getenv("WB_PUBLIC_KEY") or os.getenv("API_KEY") or "").strip()
API_SECRET = (os.getenv("API_SECRET_KEY") or os.getenv("API_SECRET") or os.getenv("WB_SECRET_KEY") or "").strip()
TRADING_ENABLED = (os.getenv("TRADING_ENABLED", "false").lower() in ["1", "true", "yes"])

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WB_PUBLIC = "https://whitebit.com/api/v4/public"
WB_PRIVATE = "https://whitebit.com/api/v4"

def log(msg: str):
    print(msg, flush=True)

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
    log(f"[WB POST] {path} -> {r.status_code} {r.text[:500]}")
    r.raise_for_status()
    return r.json() if r.text else {}

def wb_price(market: str) -> Optional[float]:
    r = requests.get(f"{WB_PUBLIC}/ticker", timeout=15)
    r.raise_for_status()
    data = r.json()
    info = data.get(market.upper())
    if not info:
        return None
    return float(info["last_price"])

def wb_balance(ticker: Optional[str] = None) -> Dict[str, str]:
    payload = {}
    if ticker:
        payload["ticker"] = ticker.upper()
    data = wb_private_post("/api/v4/main-account/balance", payload)
    return {k: v.get("main_balance", "0") for k, v in data.items() if float(v.get("main_balance", "0") or 0) > 0}

def wb_order_market(market: str, side: str, amount: str) -> Dict[str, Any]:
    payload = {"market": market.upper(), "side": side.lower(), "amount": str(amount)}
    return wb_private_post("/api/v4/order/market", payload)

def normalize_market(s: str) -> str:
    s = s.strip().upper()
    if "_" in s:
        return s
    return f"{s}_USDT"

HELP = (
    "Привіт! Я бот для WhiteBIT.\n\n"
    "Команди:\n"
    "/price <ринок> — ціна (напр. /price BTC_USDT)\n"
    "/balance [тикер] — баланс (напр. /balance або /balance USDT)\n"
    "/buy <ринок> <сума_в_quote> — ринкова покупка (напр. /buy BTC_USDT 5)\n"
    "/sell <ринок> <кількість_base> — ринковий продаж (напр. /sell BTC_USDT 0.001)\n\n"
    "⚠️ Торгівля: "
    + ("УВІМКНЕНА" if TRADING_ENABLED else "ВИМКНЕНА (додай TRADING_ENABLED=true у Environment).")
)

def run_bot():
    if not BOT_TOKEN:
        log("BOT_TOKEN відсутній.")
        return
    if not API_PUBLIC or not API_SECRET:
        log("API ключі WhiteBIT не знайдені. Переконайся, що API_PUBLIC_KEY та API_SECRET_KEY додані.")
    offset = None
    log("Bot is up. Waiting for updates...")
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
                    tg_send(chat_id, HELP)
                elif cmd == "/price":
                    if len(parts) < 2:
                        tg_send(chat_id, "Приклад: /price BTC_USDT")
                        continue
                    market = normalize_market(parts[1])
                    try:
                        p = wb_price(market)
                        tg_send(chat_id, f"{market}: {p}" if p else f"Ринок {market} не знайдено.")
                    except Exception as e:
                        tg_send(chat_id, f"Помилка ціни: {e}")
                elif cmd == "/balance":
                    ticker = parts[1] if len(parts) > 1 else None
                    try:
                        bals = wb_balance(ticker)
                        if not bals:
                            tg_send(chat_id, "Баланс порожній або 0.")
                        else:
                            lines = [f"{k}: {v}" for k, v in bals.items()]
                            tg_send(chat_id, "Баланс:\n" + "\n".join(lines))
                    except Exception as e:
                        tg_send(chat_id, f"Помилка балансу: {e}")
                elif cmd in ("/buy", "/sell"):
                    if len(parts) < 3:
                        tg_send(chat_id, f"Приклад: {cmd} BTC_USDT 5")
                        continue
                    if not TRADING_ENABLED:
                        tg_send(chat_id, "Торгівля вимкнена. Додай TRADING_ENABLED=true і перезапусти.")
                        continue
                    market = normalize_market(parts[1])
                    amount = parts[2]
                    side = "buy" if cmd == "/buy" else "sell"
                    try:
                        res = wb_order_market(market, side, amount)
                        tg_send(chat_id, f"Ордер {side} {market} OK.\nID: {res.get('orderId')}\nСтатус: {res.get('status')}")
                    except Exception as e:
                        tg_send(chat_id, f"Помилка ордера: {e}")
                else:
                    tg_send(chat_id, "Невідома команда. Напиши /help")
        except Exception as e:
            log(f"[loop] {e}")
            time.sleep(3)

if __name__ == "__main__":
    run_bot()
