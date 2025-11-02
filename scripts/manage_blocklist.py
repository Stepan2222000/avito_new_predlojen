#!/usr/bin/env python3
"""
Скрипт управления блоклистом продавцов
Использование:
    POSTGRES_HOST=... POSTGRES_PORT=... POSTGRES_DB=... \
    POSTGRES_USER=... POSTGRES_PASSWORD=... \
    python scripts/manage_blocklist.py
"""

import sys
import os
import asyncio
import logging

# Добавляем текущую директорию в sys.path для импорта db_utils
sys.path.insert(0, os.path.dirname(__file__))

import db_utils


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


async def main():
    """Главная функция скрипта"""

    # 1. Путь к файлу с блоклистом продавцов
    blocklist_file = os.path.join(
        os.path.dirname(__file__),
        'data',
        'sellers_blocklist.txt'
    )

    if not os.path.exists(blocklist_file):
        logger.error(f"File not found: {blocklist_file}")
        sys.exit(1)

    # 2. Чтение файла
    logger.info(f"Reading sellers blocklist from {blocklist_file}")

    try:
        with open(blocklist_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Failed to read file: {type(e).__name__}: {e}")
        sys.exit(1)

    logger.info(f"Read {len(lines)} lines from file")

    # 3. Парсинг продавцов (пропускаем пустые строки)
    sellers = []
    for line_num, line in enumerate(lines, 1):
        seller_name = line.strip()

        if not seller_name:
            logger.debug(f"Line {line_num}: empty, skipping")
            continue

        sellers.append(seller_name)
        logger.debug(f"Parsed seller {len(sellers)}/{len(lines)}: '{seller_name}'")

    if not sellers:
        logger.warning("No sellers found in file")
        print("\n" + "="*60)
        print("No sellers to load. Exiting.")
        print("="*60)
        return

    logger.info(f"Successfully parsed {len(sellers)} seller(s)")

    # 4. Интерактивный выбор: перезаписать или добавить
    print("\n" + "="*60)
    print(f"Found {len(sellers)} seller(s) in blocklist:")
    for idx, seller in enumerate(sellers[:5], 1):  # Показываем первые 5
        print(f"  {idx}. {seller}")
    if len(sellers) > 5:
        print(f"  ... and {len(sellers) - 5} more")
    print("="*60)

    while True:
        choice = input("Overwrite all sellers in blocklist? (y/n): ").strip().lower()
        if choice in ['y', 'n']:
            break
        print("Please enter 'y' or 'n'")

    overwrite = (choice == 'y')

    # 5. Подключение к БД
    try:
        conn = await db_utils.connect_db()
    except Exception as e:
        logger.error(f"Database connection failed: {type(e).__name__}: {e}")
        sys.exit(1)

    try:
        # 6. Выполнение операций
        if overwrite:
            logger.info("Mode: OVERWRITE - clearing existing sellers blocklist")
            await db_utils.clear_sellers_blocklist(conn)

        logger.info("Loading sellers to blocklist...")
        await db_utils.load_sellers_batch(conn, sellers)

        logger.info("="*60)
        logger.info(f"SUCCESS: Loaded {len(sellers)} seller(s) to blocklist")
        logger.info("="*60)

    except Exception as e:
        logger.error(f"Failed to load blocklist: {type(e).__name__}: {e}", exc_info=True)
        sys.exit(1)

    finally:
        await conn.close()
        logger.info("Database connection closed")


if __name__ == "__main__":
    asyncio.run(main())
