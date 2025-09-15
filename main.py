import os
import time
import json
import hmac
import base64
import hashlib
import requests
import sys
from typing import Dict, Any, Optional

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_PUBLIC = (os.getenv("API_PUBLIC_KEY") or "").strip()
API_SECRET = (os.getenv("API_SECRET_KEY") or "").strip()

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WB_PUBLIC = "https://whitebit.com/api/v4/public"
WB_PRIVATE = "https://whitebit.com/api/v4"

RUNNING = True
AUTO_TRADING = False
REAL_TRADING = False
TRADE_AMOUNT = 1.0
MARKETS = ["BTC_USDT"]
LAST_PRICES = {}

def log(msg: str):
    print(msg, flush=True)

def tg_send(chat_id: int, text: str):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception as e:
        log(f"[tg_send] {e}")

def clear_webhook():
    try:
        r = requests.get(f"{TG_API}/deleteWebhook", timeout=10)
        if r.status_code == 200:
            log("[INIT] Webhook cleared")
    except Exception as e:
        log(f"[INIT] Failed to clear webhook: {e}")

def make_signature_payload(path: str, data: Optional[Dict[str, Any]] = None):
    if data is None:
        data = {}
    body = dict(data)
    body["request"] = path
    body["nonce"] = str(int(time.time() * 1000))
    body_json = json.dumps(body, separators=(",", ":"))
    payload_b64 = base64.b64encode(body_json.encode()).decode()
    signature = hmac.new(API_SECRET.encode(), body_json.encode(), hashlib.sha512).hexdigest()
    return body_json, payload_b64, signature

def wb_private_post(path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body_json, payload_b64, signature = make_signature_payload(path, data)
    headers = {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_PUBLIC,
        "X-TXC-PAYLOAD": payload_b64,
        "X-TXC-SIGNATURE": signature,
    }
    r = requests.post(f"{WB_PRIVATE}{path}", data=body_json, headers=headers, timeout=30)
    log(f"[WB POST] {path} -> {r.status_code} {r.text[:150]}")
    r.raise_for_status()
    return r.json() if r.text else {}

def wb_price(market: str) -> Optional[float]:
    r = requests.get(f"{WB_PUBLIC}/ticker", timeout=15)
    r.raise_for_status()
    data = r.json()
    info = data.get(market.upper())
    return float(info.get("last_price")) if info else None

def wb_order_market(market: str, side: str, amount: float) -> Dict[str, Any]:
    payload = {"market": market.upper(), "side": side.lower(), "amount": str(amount)}
    return wb_private_post("/order/market", payload)

def normalize_market(s: str) -> str:
    return s.strip().upper() if "_" in s else f"{s.upper()}_USDT"

def auto_trade(chat_id: int):
    global LAST_PRICES
    for market in MARKETS:
        try:
            price = wb_price(market)
            if price is None:
                continue
            last_price = LAST_PRICES.get(market)
            LAST_PRICES[market] = price
            if not last_price:
                continue
            change = (price - last_price) / last_price * 100
            if change <= -1:
                action = "buy"
            elif change >= 1:
                action = "sell"
            else:
                continue
            tg_send(chat_id, f"[AUTO] {market}: {price:.2f} ({change:+.2f}%), дія: {action.upper()} {TRADE_AMOUNT} USDT")
            if REAL_TRADING:
                try:
                    res = wb_order_market(market, action, TRADE_AMOUNT)
                    tg_send(chat_id, f"✅ Ордер виконано: {res}")
                except Exception as e:
                    tg_send(chat_id, f"❌ Помилка ордера: {e}")
        except Exception as e:
            log(f"[auto_trade] {e}")

HELP = (
    "🤖 Бот WhiteBIT запущений!\n\n"
    "/price <ринок> — ціна\n"
    "/market <пара> — додати пару\n"
    "/remove <пара> — видалити пару\n"
    "/markets — поточні пари\n"
    "/amount <число> — встановити суму (USDT)\n"
    "/amounts — показати суму\n"
    "/autotrade on|off — автоторгівля\n"
    "/trade on|off — реальні угоди\n"
    "/status — показати всі налаштування\n"
    "/stop — зупинка бота\n"
    "/restart — перезапуск бота"
)

def run_bot():
    global RUNNING, AUTO_TRADING, REAL_TRADING, TRADE_AMOUNT, MARKETS
    if not BOT_TOKEN:
        log("BOT_TOKEN відсутній.")
        return
    clear_webhook()
    log("Bot is up. Waiting for updates...")
    offset = None
    last_auto = 0
    main_chat_id = None
    while RUNNING:
        try:
            resp = requests.get(f"{TG_API}/getUpdates", params={"timeout": 50, "offset": offset}, timeout=80)
            resp.raise_for_status()
            updates = resp.json().get("result", [])
            for u in updates:
                offset = max(offset or 0, u["update_id"] + 1)
                msg = u.get("message")
                if not msg or "text" not in msg:
                    continue
                chat_id = msg["chat"]["id"]
                main_chat_id = chat_id
                text = msg["text"].strip()
                parts = text.split()
                cmd = parts[0].lower()

                if cmd in ("/start", "/help"):
                    tg_send(chat_id, HELP)
                elif cmd == "/stop":
                    tg_send(chat_id, "⏹ Бот зупинено.")
                    RUNNING = False
                    sys.exit(0)
                elif cmd == "/restart":
                    tg_send(chat_id, "🔄 Перезапуск...")
                    os.execv(sys.executable, ["python"] + sys.argv)
                elif cmd == "/price":
                    market = normalize_market(parts[1]) if len(parts) > 1 else MARKETS[0]
                    try:
                        p = wb_price(market)
                        tg_send(chat_id, f"{market}: {p}" if p else f"Ринок {market} не знайдено.")
                    except Exception as e:
                        tg_send(chat_id, f"Помилка: {e}")
                elif cmd == "/market":
                    if len(parts) >= 2:
                        m = normalize_market(parts[1])
                        if m not in MARKETS:
                            MARKETS.append(m)
                        tg_send(chat_id, f"✅ Додано {m}. Поточні: {', '.join(MARKETS)}")
                elif cmd == "/remove":
                    if len(parts) >= 2:
                        m = normalize_market(parts[1])
                        if m in MARKETS:
                            MARKETS.remove(m)
                            tg_send(chat_id, f"❌ Видалено {m}. Поточні: {', '.join(MARKETS)}")
                        else:
                            tg_send(chat_id, f"{m} не знайдено у списку.")
                elif cmd == "/markets":
                    tg_send(chat_id, f"📊 Параметри: {', '.join(MARKETS)}")
                elif cmd == "/amount":
                    if len(parts) >= 2:
                        try:
                            TRADE_AMOUNT = float(parts[1])
                            tg_send(chat_id, f"✅ Нова сума: {TRADE_AMOUNT} USDT")
                        except:
                            tg_send(chat_id, "Помилка: введи число")
                elif cmd == "/amounts":
                    tg_send(chat_id, f"Поточна сума: {TRADE_AMOUNT} USDT")
                elif cmd == "/autotrade":
                    AUTO_TRADING = parts[1].lower() == "on" if len(parts) >= 2 else AUTO_TRADING
                    tg_send(chat_id, f"Автоторгівля {'увімкнена' if AUTO_TRADING else 'вимкнена'}.")
                elif cmd == "/trade":
                    if len(parts) >= 2:
                        REAL_TRADING = parts[1].lower() == "on"
                    tg_send(chat_id, f"Реальна торгівля {'увімкнена' if REAL_TRADING else 'вимкнена'}.")
                elif cmd == "/status":
                    tg_send(chat_id, f"📋 Параметри:\nПари: {', '.join(MARKETS)}\nСума: {TRADE_AMOUNT} USDT\nАвтоторгівля: {'ON' if AUTO_TRADING else 'OFF'}\nРеальна торгівля: {'ON' if REAL_TRADING else 'OFF'}")

            if AUTO_TRADING and main_chat_id and (time.time() - last_auto > 60):
                auto_trade(main_chat_id)
                last_auto = time.time()
        except Exception as e:
            log(f"[loop] {e}")
            time.sleep(3)

if __name__ == "__main__":
    run_bot()
