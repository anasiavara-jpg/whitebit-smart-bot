import asyncio
import logging
import os
import sys
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from datetime import datetime, timedelta

# --- Налаштування ---
API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    print("❌ Помилка: BOT_TOKEN не знайдено у змінних середовища.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# --- Змінні ---
markets = {}
auto_trading = True
last_report = datetime.utcnow()

# --- Допоміжні функції ---
async def send_message(chat_id, text):
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        logging.error(f"Помилка надсилання повідомлення: {e}")

# Перевірка API-відповідей перед .encode()
def safe_str(value):
    return str(value) if value is not None else ""

# --- Основна логіка автоторгівлі ---
async def auto_trade():
    global last_report
    while auto_trading:
        for market, data in markets.items():
            try:
                price = get_price_from_api(market)
                if price is None:
                    logging.warning(f"[{market}] Дані не отримані, пропуск циклу.")
                    continue
                # Тут логіка купівлі/продажу
            except Exception as e:
                logging.error(f"[AUTO LOOP] {market}: {e}")
                continue

        # Щогодинний звіт
        if datetime.utcnow() - last_report >= timedelta(hours=1):
            report = "📊 Щогодинний звіт:
"
            for m, d in markets.items():
                report += f"{m}: TP={d.get('tp')} SL={d.get('sl')} Amt={d.get('amount')}
"
            await send_message(admin_chat_id, report)
            last_report = datetime.utcnow()

        await asyncio.sleep(10)

# --- Команди ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    await message.answer("✅ Бот запущено. Автоторгівля УВІМКНЕНА.")
    asyncio.create_task(auto_trade())

@dp.message_handler(commands=['restart'])
async def restart_cmd(message: types.Message):
    await message.answer("🔄 Перезапуск бота...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

@dp.message_handler(commands=['removemarket'])
async def remove_market(message: types.Message):
    args = message.get_args().upper()
    if args in markets:
        del markets[args]
        await message.answer(f"❌ Видалено {args}")
    else:
        await message.answer("⚠️ Ринок не знайдено.")

# --- Функція-заглушка для ціни (замінити реальною) ---
def get_price_from_api(market):
    return 0.1  # тестове значення

if __name__ == '__main__':
    try:
        executor.start_polling(dp, skip_updates=True)
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот зупинено.")
