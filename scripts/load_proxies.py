#!/usr/bin/env python3
"""
Скрипт загрузки прокси из файла в БД
Использование:
    POSTGRES_HOST=... POSTGRES_PORT=... POSTGRES_DB=... \
    POSTGRES_USER=... POSTGRES_PASSWORD=... \
    python scripts/load_proxies.py
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


def parse_proxy_line(line: str, line_num: int) -> str:
    """
    Парсинг одной строки прокси.

    Args:
        line: Строка из файла
        line_num: Номер строки (для ошибок)

    Returns:
        Прокси в формате host:port:user:pass

    Raises:
        ValueError: Если формат неверный
    """
    line = line.strip()

    if not line:
        return None

    parts = line.split(':')

    if len(parts) != 4:
        raise ValueError(
            f"Line {line_num}: Invalid proxy format. Expected 'host:port:user:pass', got '{line}'"
        )

    host, port, user, password = parts

    # Валидация порта
    try:
        port_int = int(port)
        if not (1 <= port_int <= 65535):
            raise ValueError()
    except ValueError:
        raise ValueError(
            f"Line {line_num}: Invalid port '{port}'. Must be integer 1-65535"
        )

    # Возвращаем в исходном формате host:port:user:pass
    return line


async def main():
    """Главная функция скрипта"""

    # 1. Путь к файлу с прокси
    proxies_file = os.path.join(
        os.path.dirname(__file__),
        'data',
        'proxies.txt'
    )

    if not os.path.exists(proxies_file):
        logger.error(f"File not found: {proxies_file}")
        sys.exit(1)

    # 2. Чтение файла
    logger.info(f"Reading proxies from {proxies_file}")

    try:
        with open(proxies_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Failed to read file: {type(e).__name__}: {e}")
        sys.exit(1)

    logger.info(f"Read {len(lines)} lines from file")

    # 3. Парсинг прокси (Fail-fast)
    proxies = []
    for line_num, line in enumerate(lines, 1):
        try:
            proxy = parse_proxy_line(line, line_num)
            if proxy:
                proxies.append(proxy)
                logger.debug(f"Parsed proxy {len(proxies)}/{len(lines)}: {proxy}")
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)

    if not proxies:
        logger.error("No valid proxies found in file")
        sys.exit(1)

    logger.info(f"Successfully parsed {len(proxies)} proxies")

    # 4. Интерактивный выбор: перезаписать или добавить
    print("\n" + "="*60)
    print(f"Found {len(proxies)} proxies")
    print("="*60)

    while True:
        choice = input("Overwrite all proxies in database? (y/n): ").strip().lower()
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
            logger.info("Mode: OVERWRITE - deleting all existing proxies")
            await db_utils.clear_all_proxies(conn)

        logger.info("Loading proxies to database...")
        await db_utils.load_proxies_batch(conn, proxies)

        logger.info("="*60)
        logger.info(f"SUCCESS: Loaded {len(proxies)} proxies to database")
        logger.info("="*60)

    except Exception as e:
        logger.error(f"Failed to load proxies: {type(e).__name__}: {e}", exc_info=True)
        sys.exit(1)

    finally:
        await conn.close()
        logger.info("Database connection closed")


if __name__ == "__main__":
    asyncio.run(main())
