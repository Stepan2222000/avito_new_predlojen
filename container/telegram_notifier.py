import os
from aiogram import Bot

# Глобальный экземпляр бота (один для всех воркеров)
token = os.getenv("TELEGRAM_BOT_TOKEN")
if not token:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
bot = Bot(token=token)


async def send_notification(chat_id: int, listing: dict):
    """Отправка уведомления о новом объявлении."""
    try:
        lines = [listing['title']]

        # Цена (если есть)
        if listing.get('price') is not None and listing.get('currency') is not None:
            lines.append(f"{listing['price']} {listing['currency']}")

        if listing.get('location'):
            lines.append(listing['location'])

        lines.append(f"https://www.avito.ru/{listing['item_id']}")

        message = "\n".join(lines)

        await bot.send_message(chat_id=chat_id, text=message)

    except Exception:
        # Re-raise для retry на уровне worker
        raise
