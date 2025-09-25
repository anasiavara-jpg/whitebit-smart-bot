# WhiteBIT Smart Bot

Telegram-бот для торгівлі на WhiteBIT (Spot API) з підтримкою:
- Автотрейд (циклічна торгівля з TP/SL)
- Баланс, статуси, збереження параметрів у JSON
- Запуск через Render (Background Worker)

## Команди
- /start, /help
- /balance
- /market BTC/USDT
- /settp BTC/USDT 5
- /setsl BTC/USDT 2
- /setbuy BTC/USDT 30
- /buy BTC/USDT
- /status
- /stop
- /removemarket BTC/USDT
- /restart
- /autotrade BTC/USDT on|off

## Запуск локально
```bash
pip install -r requirements.txt
python main.py
```
