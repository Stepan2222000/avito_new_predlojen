#!/usr/bin/env python3
"""
Скрипт для очистки всех таблиц в базе данных
"""
import asyncio
import asyncpg
import os


async def clear_all_tables():
    """Очистка всех таблиц в базе данных"""

    # Параметры подключения
    conn = await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "81.30.105.134"),
        port=int(os.getenv("POSTGRES_PORT", "5415")),
        database=os.getenv("POSTGRES_DB", "avito_new_predlojen"),
        user=os.getenv("POSTGRES_USER", "admin"),
        password=os.getenv("POSTGRES_PASSWORD", "Password123")
    )

    try:
        print("🗑️  Очистка всех таблиц...")

        # Очистка всех таблиц в правильном порядке
        tables = [
            "tasks",
            "parsed_items",
            "blocklist_items_local",
            "blocklist_items_global",
            "blocklist_sellers",
            "proxies",
            "groups"
        ]

        for table in tables:
            await conn.execute(f"TRUNCATE TABLE {table} CASCADE")
            print(f"✅ Таблица {table} очищена")

        print("\n✨ Все таблицы успешно очищены!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(clear_all_tables())
