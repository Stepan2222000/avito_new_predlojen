"""
Скрипт для получения chat_id пользователя Telegram
Запусти, потом напиши боту /start или любое сообщение
"""

import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Токен бота
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8508454518:AAGc76GHWyNNgLbTHyVZFNGiDo26EPr9MsQ")

bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработка команды /start"""
    chat_id = message.chat.id
    username = message.from_user.username or "No username"
    first_name = message.from_user.first_name or "No name"

    response = f"""
✅ Информация получена!

👤 Имя: {first_name}
🔗 Username: @{username}
🆔 Chat ID: {chat_id}

Используй этот Chat ID в groups.json:
"telegram_chat_id": {chat_id}
"""

    await message.answer(response)
    print(f"\n{'='*50}")
    print(f"Chat ID: {chat_id}")
    print(f"Username: @{username}")
    print(f"Name: {first_name}")
    print(f"{'='*50}\n")


@dp.message()
async def any_message(message: types.Message):
    """Обработка любого сообщения"""
    chat_id = message.chat.id
    username = message.from_user.username or "No username"
    first_name = message.from_user.first_name or "No name"

    response = f"""
✅ Информация получена!

👤 Имя: {first_name}
🔗 Username: @{username}
🆔 Chat ID: {chat_id}

Используй этот Chat ID в groups.json:
"telegram_chat_id": {chat_id}
"""

    await message.answer(response)
    print(f"\n{'='*50}")
    print(f"Chat ID: {chat_id}")
    print(f"Username: @{username}")
    print(f"Name: {first_name}")
    print(f"{'='*50}\n")


async def main():
    print("Бот запущен!")
    print("Напиши боту @new_offer_sender_bot любое сообщение для получения chat_id")
    print("Нажми Ctrl+C для остановки")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
