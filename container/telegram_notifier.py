import os
from aiogram import Bot

# Emoji для категорий
CATEGORY_EMOJI = {
    "avtomobili": "🚗",
    "mototsikly": "🏍",
    "snegohody": "🛷",
    "kvadrotsikly": "🏍",
    "gidrotsikly": "🛥",
    "katera_i_yahty": "⛵"
}

# Глобальный экземпляр бота (один для всех воркеров)
token = os.getenv("TELEGRAM_BOT_TOKEN")
if not token:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
bot = Bot(token=token)


async def send_notification(chat_id: int, listing: dict, category: str):
    """Отправка уведомления о новом объявлении."""
    try:
        emoji = CATEGORY_EMOJI.get(category, "📦")

        # Формирование сообщения
        lines = [f"{emoji} {listing['title']}"]

        # Цена (если есть)
        if listing.get('price') is not None and listing.get('currency') is not None:
            lines.append(f"💰 {listing['price']} {listing['currency']}")

        lines.append(f"📍 {listing['location']}")
        lines.append(f"🔗 https://www.avito.ru/{listing['item_id']}")

        message = "\n".join(lines)

        # Telegram гарантирует доставку только владельцу chat_id
        await bot.send_message(chat_id=chat_id, text=message)

    except Exception:
        # Re-raise для retry на уровне worker
        raise
