import os
import asyncio
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

        lines.append(f"https://www.avito.ru/{listing['item_id']}")

        message = "\n".join(lines)

        await bot.send_message(chat_id=chat_id, text=message)

    except Exception:
        # Re-raise для retry на уровне worker
        raise


async def send_notification_to_multiple(chat_ids: list[int], listing: dict) -> dict[int, bool]:
    """
    Отправка уведомления нескольким получателям параллельно.

    Возвращает словарь {chat_id: success_status}
    """
    async def send_to_one(chat_id: int) -> tuple[int, bool]:
        try:
            await send_notification(chat_id, listing)
            return (chat_id, True)
        except Exception:
            return (chat_id, False)

    # Параллельная отправка всем получателям
    results = await asyncio.gather(*[send_to_one(cid) for cid in chat_ids])

    return dict(results)
