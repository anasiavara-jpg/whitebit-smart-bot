import os
import time
import json
import hmac
import base64
import hashlib
import requests
import sys
from typing import Dict, Any, Optional, List

# ========= Config from ENV =========
BOT_TOKEN   = os.getenv("BOT_TOKEN", "").strip()
API_PUBLIC  = (os.getenv("API_PUBLIC_KEY") or os.getenv("API_PUBLIC") or os.getenv("WB_PUBLIC_KEY") or os.getenv("API_KEY") or "").strip()
API_SECRET  = (os.getenv("API_SECRET_KEY") or os.getenv("API_SECRET") or os.getenv("WB_SECRET_KEY") or "").strip()

# Runtime switches (can be changed via commands)
AUTO_TRADING  = False      # /auto on|off
REAL_TRADING  = False      # /trade on|off
TRADE_AMOUNT  = 1.0        # /amount <number>  (in USDT)
TAKE_PROFIT_P = 1.0        # /settp <pct>, e.g. 1.0 = +1%
STOP_LOSS_P   = 1.0        # /setsl <pct>, e.g. 1.0 = -1%

# ========= Endpoints =========
TG_API     = f"https://api.telegram.org/bot{BOT_TOKEN}"
WB_PUBLIC  = "https://whitebit.com/api/v4/public"
WB_PRIVATE = "https://whitebit.com/api/v4"

# ========= State =========
RUNNING: bool = True
MARKETS: List[str] = ["BTC_USDT"]   # /market add; /remove; /markets
LAST_PRICE: Dict[str, float] = {}   # last seen ticker price per market
ENTRY_PRICE: Dict[str, float] = {}  # price of last BUY per market (to evaluate TP/SL)
OVERRIDE_AMOUNT: Dict[str, float] = {}  # /setamount <MARKET> <amount>

def log(msg: str):
    print(msg, flush=True)

def tg_send(chat_id: int, text: str):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception as e:
        log(f"[tg_send] {e}")

# ===== WhiteBIT signing =====
def _make_signature_payload(path: str, data: Optional[Dict[str, Any]] = None):
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
    body_json, payload_b64, signature = _make_signature_payload(path, data)
    headers = {
        "Content-Type": "application/json",
        "X-TXC-APIKEY": API_PUBLIC,
        "X-TXC-PAYLOAD": payload_b64,
        "X-TXC-SIGNATURE": signature,
    }
    url = f"{WB_PRIVATE}{path}"
    r = requests.post(url, data=body_json, headers=headers, timeout=30)
    log(f"[WB POST] {path} -> {r.status_code} {r.text[:200]}")
    r.raise_for_status()
    return r.json() if r.text else {}

def wb_price(market: str) -> Optional[float]:
    r = requests.get(f"{WB_PUBLIC}/ticker", timeout=15)
    r.raise_for_status()
    data = r.json()
    info = data.get(market.upper())
    try:
        return float(info["last_price"]) if info else None
    except Exception:
        return None

def wb_balance(ticker: Optional[str] = None) -> Dict[str, str]:
    payload = {}
    data = wb_private_post("/main-account/balance", payload)
    if ticker:
        t = ticker.upper()
        v = data.get(t, {})
        bal = v.get("main_balance", "0") if isinstance(v, dict) else "0"
        return {t: bal}
    # all positive balances
    out = {}
    for k, v in data.items():
        try:
            bal = float(v.get("main_balance", "0") or 0)
            if bal > 0:
                out[k] = str(bal)
        except Exception:
            continue
    return out

def wb_order_market(market: str, side: str, amount_quote_usdt: float) -> Dict[str, Any]:
    # WhiteBIT market order expects:
    #   { market, side: "buy"/"sell", amount }
    # Here we pass "amount" as a string; their API interprets for market orders:
    # - for BUY: amount is in quote (USDT)
    # - for SELL: amount is in base (we convert quote->base using last price)
    m = market.upper()
    if side.lower() == "sell":
        price = wb_price(m) or 0.0
        if price <= 0:
            raise RuntimeError("Неможливо отримати ціну для перерахунку sell.")
        base_amount = amount_quote_usdt / price
        payload = {"market": m, "side": "sell", "amount": f"{base_amount:.8f}"}
    else:
        payload = {"market": m, "side": "buy", "amount": f"{amount_quote_usdt:.8f}"}
    return wb_private_post("/order/market", payload)

def normalize_market(s: str) -> str:
    s = s.strip().upper()
    return s if "_" in s else f"{s}_USDT"

# ====== Helper strings ======
HELP = (
    "🤖 WhiteBIT бот (автоторгівля + ручні команди)\n\n"
    "📈 Ручні:\n"
    "/price <пара> — ціна (напр. /price BTC_USDT)\n"
    "/balance [тикер] — баланс (напр. /balance або /balance USDT)\n"
    "/buy <пара> <сума_USDT> — ринкова покупка\n"
    "/sell <пара> <сума_USDT> — ринковий продаж (сума в USDT; конвертується у базу)\n\n"
    "🤖 Авто:\n"
    "/auto on|off — увімк/вимк автоторгівлю\n"
    "/trade on|off — реальні ордери чи лише сигнали\n"
    "/market <пара> — додати пару (можна кілька разів)\n"
    "/remove <пара> — прибрати пару\n"
    "/markets — показати всі пари\n"
    "/amount <USDT> — глобальна сума на угоду\n"
    "/setamount <пара> <USDT> — сума на конкретну пару\n"
    "/settp <pct> — take-profit у відсотках (напр. 1 = 1%)\n"
    "/setsl <pct> — stop-loss у відсотках (напр. 1 = 1%)\n"
    "/status — поточні налаштування\n\n"
    "⚙️ Керування:\n"
    "/stop — зупинити бота\n"
    "/restart — перезапуск процесу\n"
)

def clear_webhook_and_offset():
    # прибираємо потенційний конфлікт webhook vs getUpdates
    try:
        requests.get(f"{TG_API}/deleteWebhook", timeout=10)
        # скидаємо чергу апдейтів (почнемо з «поточного»)
        requests.get(f"{TG_API}/getUpdates", params={"offset": -1}, timeout=10)
        log("[INIT] Webhook cleared & offset reset")
    except Exception as e:
        log(f"[INIT] cleanup error: {e}")

# ====== Autotrade loop ======
def auto_trade_once(chat_id: int):
    global LAST_PRICE, ENTRY_PRICE
    for m in list(MARKETS):
        try:
            price = wb_price(m)
            if price is None:
                continue
            prev = LAST_PRICE.get(m)
            LAST_PRICE[m] = price

            # Якщо є відкрита «позиція» (ми купили раніше) — перевіряємо TP/SL
            entry = ENTRY_PRICE.get(m)
            if entry:
                chg_from_entry = (price - entry) / entry * 100.0
                if chg_from_entry >= TAKE_PROFIT_P:
                    # SELL сигнал
                    amount = OVERRIDE_AMOUNT.get(m, TRADE_AMOUNT)
                    tg_send(chat_id, f"[AUTO] {m}: TP {chg_from_entry:+.2f}% → SELL {amount} USDT @ {price:.4f}")
                    if REAL_TRADING:
                        try:
                            res = wb_order_market(m, "sell", amount)
                            tg_send(chat_id, f"✅ SELL виконано: {res}")
                            ENTRY_PRICE.pop(m, None)
                        except Exception as e:
                            tg_send(chat_id, f"❌ SELL помилка: {e}")
                    else:
                        ENTRY_PRICE.pop(m, None)
                    continue
                if chg_from_entry <= -STOP_LOSS_P:
                    # SELL (stop)
                    amount = OVERRIDE_AMOUNT.get(m, TRADE_AMOUNT)
                    tg_send(chat_id, f"[AUTO] {m}: SL {chg_from_entry:+.2f}% → SELL {amount} USDT @ {price:.4f}")
                    if REAL_TRADING:
                        try:
                            res = wb_order_market(m, "sell", amount)
                            tg_send(chat_id, f"✅ SELL виконано: {res}")
                            ENTRY_PRICE.pop(m, None)
                        except Exception as e:
                            tg_send(chat_id, f"❌ SELL помилка: {e}")
                    else:
                        ENTRY_PRICE.pop(m, None)
                    continue

            # Якщо позиції немає — шукаємо точку входу на відкаті / імпульсі
            if prev is None:
                continue
            chg = (price - prev) / prev * 100.0
            if chg <= -1.0:  # падіння ≥1% від попередньої ціни → BUY
                amount = OVERRIDE_AMOUNT.get(m, TRADE_AMOUNT)
                tg_send(chat_id, f"[AUTO] {m}: {chg:+.2f}% → BUY {amount} USDT @ {price:.4f}")
                if REAL_TRADING:
                    try:
                        res = wb_order_market(m, "buy", amount)
                        tg_send(chat_id, f"✅ BUY виконано: {res}")
                        ENTRY_PRICE[m] = price
                    except Exception as e:
                        tg_send(chat_id, f"❌ BUY помилка: {e}")
                else:
                    ENTRY_PRICE[m] = price
                continue
        except Exception as e:
            log(f"[auto] {m}: {e}")

# ====== Bot main loop ======
def run_bot():
    global RUNNING, AUTO_TRADING, REAL_TRADING, TRADE_AMOUNT, TAKE_PROFIT_P, STOP_LOSS_P
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        log("❌ BOT_TOKEN відсутній або неправильний.")
        return

    clear_webhook_and_offset()

    log("Bot is up. Waiting for updates...")
    offset = None
    last_auto = 0
    main_chat_id = None

    while RUNNING:
        try:
            # long polling
            resp = requests.get(f"{TG_API}/getUpdates", params={"timeout": 50, "offset": offset}, timeout=80)
            # handle token issues early
            if resp.status_code == 409:
                log("⚠️ 409 Conflict: бот уже запущений десь інакше. Зупини інші інстанси або перегенеруй токен.")
                time.sleep(5)
                continue
            if resp.status_code == 401:
                log("❌ 401 Unauthorized: невірний BOT_TOKEN.")
                break
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
                main_chat_id = chat_id

                if cmd in ("/start", "/help"):
                    tg_send(chat_id, HELP)

                elif cmd == "/stop":
                    tg_send(chat_id, "⏹ Зупиняю бота.")
                    RUNNING = False
                    sys.exit(0)

                elif cmd == "/restart":
                    tg_send(chat_id, "🔄 Перезапуск процесу...")
                    os.execv(sys.executable, ["python"] + sys.argv)

                elif cmd == "/price":
                    m = normalize_market(parts[1]) if len(parts) > 1 else MARKETS[0]
                    try:
                        p = wb_price(m)
                        tg_send(chat_id, f"{m}: {p}" if p else f"Ринок {m} не знайдено.")
                    except Exception as e:
                        tg_send(chat_id, f"Помилка ціни: {e}")

                elif cmd == "/balance":
                    t = parts[1] if len(parts) > 1 else None
                    try:
                        bals = wb_balance(t)
                        if not bals:
                            tg_send(chat_id, "Баланс порожній або 0.")
                        else:
                            lines = [f"{k}: {v}" for k, v in bals.items()]
                            tg_send(chat_id, "Баланс:\n" + "\n".join(lines))
                    except Exception as e:
                        tg_send(chat_id, f"Помилка балансу: {e}")

                elif cmd == "/buy" or cmd == "/sell":
                    if len(parts) < 3:
                        tg_send(chat_id, f"Приклад: {cmd} BTC_USDT 5")
                        continue
                    m = normalize_market(parts[1])
                    try:
                        amt = float(parts[2])
                    except:
                        tg_send(chat_id, "Сума має бути числом у USDT.")
                        continue
                    if not REAL_TRADING:
                        tg_send(chat_id, "⚠️ Реальна торгівля вимкнена. Увімкни: /trade on")
                        continue
                    try:
                        res = wb_order_market(m, "buy" if cmd == "/buy" else "sell", amt)
                        tg_send(chat_id, f"✅ Ордер {('BUY' if cmd == '/buy' else 'SELL')} {m} виконано: {res}")
                    except Exception as e:
                        tg_send(chat_id, f"❌ Помилка ордера: {e}")

                elif cmd == "/market":
                    if len(parts) < 2:
                        tg_send(chat_id, "Приклад: /market ETH_USDT")
                        continue
                    m = normalize_market(parts[1])
                    if m not in MARKETS:
                        MARKETS.append(m)
                    tg_send(chat_id, f"✅ Додано {m}. Поточні: {', '.join(MARKETS)}")

                elif cmd == "/remove":
                    if len(parts) < 2:
                        tg_send(chat_id, "Приклад: /remove ETH_USDT")
                        continue
                    m = normalize_market(parts[1])
                    if m in MARKETS:
                        MARKETS.remove(m)
                        ENTRY_PRICE.pop(m, None)
                        LAST_PRICE.pop(m, None)
                        OVERRIDE_AMOUNT.pop(m, None)
                        tg_send(chat_id, f"❌ Видалено {m}. Поточні: {', '.join(MARKETS) or '—'}")
                    else:
                        tg_send(chat_id, f"{m} не у списку.")

                elif cmd == "/markets":
                    tg_send(chat_id, f"📊 Парами слідкую: {', '.join(MARKETS)}")

                elif cmd == "/amount":
                    if len(parts) < 2:
                        tg_send(chat_id, f"Поточна глобальна сума: {TRADE_AMOUNT} USDT")
                        continue
                    try:
                        TRADE_AMOUNT = float(parts[1])
                        tg_send(chat_id, f"✅ Нова глобальна сума: {TRADE_AMOUNT} USDT")
                    except:
                        tg_send(chat_id, "Сума має бути числом.")

                elif cmd == "/setamount":
                    if len(parts) < 3:
                        tg_send(chat_id, "Приклад: /setamount BTC_USDT 5")
                        continue
                    m = normalize_market(parts[1])
                    try:
                        OVERRIDE_AMOUNT[m] = float(parts[2])
                        tg_send(chat_id, f"✅ Сума для {m}: {OVERRIDE_AMOUNT[m]} USDT")
                    except:
                        tg_send(chat_id, "Сума має бути числом.")

                elif cmd == "/settp":
                    if len(parts) < 2:
                        tg_send(chat_id, f"Поточний TP: {TAKE_PROFIT_P}%")
                        continue
                    try:
                        TAKE_PROFIT_P = float(parts[1])
                        tg_send(chat_id, f"✅ TP встановлено: {TAKE_PROFIT_P}%")
                    except:
                        tg_send(chat_id, "Вкажи число у відсотках.")

                elif cmd == "/setsl":
                    if len(parts) < 2:
                        tg_send(chat_id, f"Поточний SL: {STOP_LOSS_P}%")
                        continue
                    try:
                        STOP_LOSS_P = float(parts[1])
                        tg_send(chat_id, f"✅ SL встановлено: {STOP_LOSS_P}%")
                    except:
                        tg_send(chat_id, "Вкажи число у відсотках.")

                elif cmd == "/auto":
                    if len(parts) < 2:
                        tg_send(chat_id, f"Автоторгівля: {'ON' if AUTO_TRADING else 'OFF'}")
                    else:
                        AUTO_TRADING = (parts[1].lower() == "on")
                        tg_send(chat_id, f"Автоторгівля {'увімкнена' if AUTO_TRADING else 'вимкнена'}.")

                elif cmd == "/trade":
                    if len(parts) < 2:
                        tg_send(chat_id, f"Реальні ордери: {'ON' if REAL_TRADING else 'OFF'}")
                    else:
                        REAL_TRADING = (parts[1].lower() == "on")
                        tg_send(chat_id, f"Реальні ордери {'увімкнені' if REAL_TRADING else 'вимкнені'}.")

                elif cmd == "/status":
                    pairs = ", ".join(MARKETS)
                    amps = ", ".join([f"{k}:{v}USDT" for k,v in OVERRIDE_AMOUNT.items()]) or "—"
                    tg_send(chat_id,
                        f"📋 Статус:\n"
                        f"Пари: {pairs}\n"
                        f"Глобальна сума: {TRADE_AMOUNT} USDT\n"
                        f"Пер-пари суми: {amps}\n"
                        f"AUTO: {'ON' if AUTO_TRADING else 'OFF'} | TRADE: {'ON' if REAL_TRADING else 'OFF'}\n"
                        f"TP: {TAKE_PROFIT_P}% | SL: {STOP_LOSS_P}%"
                    )

                else:
                    tg_send(chat_id, "Невідома команда. Напиши /help")

            # run autotrade every 60s
            if AUTO_TRADING and main_chat_id and (time.time() - last_auto >= 60):
                auto_trade_once(main_chat_id)
                last_auto = time.time()

        except Exception as e:
            log(f"[loop] {e}")
            time.sleep(3)

if __name__ == "__main__":
    run_bot()
