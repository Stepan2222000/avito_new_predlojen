#!/usr/bin/env python3
"""
Скрипт загрузки групп из groups.json в БД + автогенерация задач
Использование:
    POSTGRES_HOST=... POSTGRES_PORT=... POSTGRES_DB=... \
    POSTGRES_USER=... POSTGRES_PASSWORD=... \
    python scripts/load_groups.py
"""

import sys
import os
import asyncio
import logging
import json
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List

# Добавляем текущую директорию в sys.path для импортов
sys.path.insert(0, os.path.dirname(__file__))

import db_utils
from url_builder import generate_task_urls


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def validate_group(group: Dict[str, Any], group_idx: int) -> None:
    """
    Валидация структуры группы.

    Args:
        group: Словарь группы из groups.json
        group_idx: Индекс группы (для ошибок)

    Raises:
        ValueError: Если структура невалидна
    """
    required_fields = [
        'name', 'enabled', 'category', 'region_slug', 'brands', 'models',
        'all_russia', 'enrich_q', 'blocklist_mode'
    ]

    for field in required_fields:
        if field not in group:
            raise ValueError(
                f"Group {group_idx}: Missing required field '{field}'"
            )

    # Валидация типов
    if not isinstance(group['name'], str) or not group['name']:
        raise ValueError(f"Group {group_idx}: 'name' must be non-empty string")

    if not isinstance(group['enabled'], bool):
        raise ValueError(f"Group {group_idx}: 'enabled' must be boolean")

    if not isinstance(group['brands'], list):
        raise ValueError(f"Group {group_idx}: 'brands' must be list")

    if not isinstance(group['models'], dict):
        raise ValueError(f"Group {group_idx}: 'models' must be dict")

    if group['blocklist_mode'] not in ['global', 'local']:
        raise ValueError(
            f"Group {group_idx}: 'blocklist_mode' must be 'global' or 'local'"
        )

    # Поддержка telegram_chat_id (старый формат) и telegram_chat_ids (новый формат)
    if 'telegram_chat_id' in group:
        # Старый формат: конвертируем int в list
        if not isinstance(group['telegram_chat_id'], int):
            raise ValueError(f"Group {group_idx}: 'telegram_chat_id' must be integer")
        group['telegram_chat_ids'] = [group['telegram_chat_id']]
        del group['telegram_chat_id']
    elif 'telegram_chat_ids' in group:
        # Новый формат: проверяем list[int]
        if isinstance(group['telegram_chat_ids'], int):
            # Автоматическая конвертация int → list
            group['telegram_chat_ids'] = [group['telegram_chat_ids']]
        elif isinstance(group['telegram_chat_ids'], list):
            if not all(isinstance(cid, int) for cid in group['telegram_chat_ids']):
                raise ValueError(f"Group {group_idx}: 'telegram_chat_ids' must be list of integers")
            if len(group['telegram_chat_ids']) == 0:
                raise ValueError(f"Group {group_idx}: 'telegram_chat_ids' cannot be empty")
        else:
            raise ValueError(f"Group {group_idx}: 'telegram_chat_ids' must be integer or list of integers")
    else:
        raise ValueError(f"Group {group_idx}: missing 'telegram_chat_id' or 'telegram_chat_ids'")


def extract_search_query(url: str) -> str:
    """
    Извлечение параметра q из URL для поля search_query.

    Args:
        url: URL задачи

    Returns:
        Значение параметра q или None
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    return params.get('q', [None])[0]


async def process_group(
    conn,
    group: Dict[str, Any],
    overwrite: bool
) -> None:
    """
    Обработка одной группы: вставка в БД + генерация задач.

    Args:
        conn: Подключение к БД
        group: Словарь группы
        overwrite: Режим перезаписи (True) или добавления (False)
    """
    group_name = group['name']

    logger.info(f"Processing group '{group_name}'")

    # 1. Удаление старых задач если режим перезаписи
    if overwrite:
        await db_utils.delete_tasks_for_group(conn, group_name)

    # 2. Вставка/обновление группы в БД
    await db_utils.upsert_group(conn, group)

    # 3. Генерация URLs через url_builder
    try:
        urls = generate_task_urls(group)
        logger.info(f"Generated {len(urls)} URLs for group '{group_name}'")
    except Exception as e:
        raise ValueError(
            f"Failed to generate URLs for group '{group_name}': {type(e).__name__}: {e}"
        )

    if not urls:
        logger.warning(f"No URLs generated for group '{group_name}'")
        return

    # 4. Извлечение search_query из каждого URL
    task_data = []
    for idx, url in enumerate(urls, 1):
        search_query = extract_search_query(url)
        task_data.append({
            'url': url,
            'search_query': search_query
        })
        logger.debug(
            f"URL {idx}/{len(urls)}: {url[:80]}... (q={search_query})"
        )

    # 5. Создание задач в БД
    await db_utils.create_tasks_for_group(conn, group_name, task_data)


async def main():
    """Главная функция скрипта"""

    # 1. Путь к файлу с группами
    groups_file = os.path.join(
        os.path.dirname(__file__),
        'data',
        'groups.json'
    )

    if not os.path.exists(groups_file):
        logger.error(f"File not found: {groups_file}")
        sys.exit(1)

    # 2. Чтение файла
    logger.info(f"Reading groups from {groups_file}")

    try:
        with open(groups_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to read file: {type(e).__name__}: {e}")
        sys.exit(1)

    # 3. Валидация структуры
    if 'groups' not in data:
        logger.error("Missing 'groups' key in JSON")
        sys.exit(1)

    groups = data['groups']

    if not isinstance(groups, list):
        logger.error("'groups' must be a list")
        sys.exit(1)

    if not groups:
        logger.error("No groups found in file")
        sys.exit(1)

    logger.info(f"Read {len(groups)} group(s) from file")

    # 4. Валидация каждой группы (Fail-fast)
    for idx, group in enumerate(groups, 1):
        try:
            validate_group(group, idx)
            logger.debug(f"Validated group {idx}: '{group['name']}'")
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)

    logger.info(f"Successfully validated {len(groups)} group(s)")

    # 5. Интерактивный выбор: перезаписать или добавить
    print("\n" + "="*60)
    print(f"Found {len(groups)} group(s):")
    for group in groups:
        print(f"  - {group['name']} ({group['category']}, {group['region_slug']})")
    print("="*60)

    while True:
        choice = input("Overwrite groups and tasks in database? (y/n): ").strip().lower()
        if choice in ['y', 'n']:
            break
        print("Please enter 'y' or 'n'")

    overwrite = (choice == 'y')

    # 6. Подключение к БД
    try:
        conn = await db_utils.connect_db()
    except Exception as e:
        logger.error(f"Database connection failed: {type(e).__name__}: {e}")
        sys.exit(1)

    try:
        # 7. Если режим перезаписи - удаляем все группы
        if overwrite:
            logger.info("Mode: OVERWRITE - deleting all existing groups")
            await conn.execute("DELETE FROM groups")
            logger.info("All groups deleted")

        # 8. Обработка каждой группы
        total_tasks = 0
        for group in groups:
            try:
                await process_group(conn, group, overwrite)
                # Подсчет задач для группы
                count = await db_utils.count_tasks_for_group(conn, group['name'])
                total_tasks += count
            except Exception as e:
                logger.error(
                    f"Failed to process group '{group['name']}': {type(e).__name__}: {e}",
                    exc_info=True
                )
                sys.exit(1)

        logger.info("="*60)
        logger.info(f"SUCCESS: Loaded {len(groups)} group(s), created {total_tasks} task(s)")
        logger.info("="*60)

    except Exception as e:
        logger.error(f"Failed to load groups: {type(e).__name__}: {e}", exc_info=True)
        sys.exit(1)

    finally:
        await conn.close()
        logger.info("Database connection closed")


if __name__ == "__main__":
    asyncio.run(main())
