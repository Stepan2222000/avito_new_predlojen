"""
Database utilities for management scripts
Принцип: KISS - простые подключения asyncpg, прямые SQL запросы
"""

import json
import logging
from typing import List, Dict, Any, Optional

import asyncpg


logger = logging.getLogger(__name__)


# ======================================
# 1. Подключение к БД
# ======================================
async def connect_db() -> asyncpg.Connection:
    """
    Создание простого подключения к PostgreSQL.
    Использует фиксированные настройки БД.

    Returns:
        asyncpg.Connection: Подключение к БД
    """
    host = "81.30.105.134"
    port = 5415
    database = "avito_new_predlojen"
    user = "admin"
    password = "Password123"

    logger.info(f"Connecting to PostgreSQL: {user}@{host}:{port}/{database}")

    try:
        conn = await asyncpg.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            timeout=30
        )
        logger.info("Database connection established")
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {type(e).__name__}: {e}")
        raise


# ======================================
# 2. Работа с прокси
# ======================================
async def load_proxies_batch(conn: asyncpg.Connection, proxy_list: List[str]) -> None:
    """
    Массовая вставка прокси в БД.

    Args:
        conn: Подключение к БД
        proxy_list: Список прокси в формате host:port:user:pass
    """
    logger.info(f"Inserting {len(proxy_list)} proxies...")

    for idx, proxy_url in enumerate(proxy_list, 1):
        await conn.execute("""
            INSERT INTO proxies (proxy_url, is_banned)
            VALUES ($1, FALSE)
            ON CONFLICT (proxy_url) DO NOTHING
        """, proxy_url)
        logger.debug(f"Inserted proxy {idx}/{len(proxy_list)}: {proxy_url}")

    logger.info(f"Successfully inserted {len(proxy_list)} proxies")


async def clear_all_proxies(conn: asyncpg.Connection) -> None:
    """
    Удаление всех прокси из БД.

    Args:
        conn: Подключение к БД
    """
    logger.info("Clearing all proxies from database...")

    result = await conn.execute("DELETE FROM proxies")
    count = int(result.split()[-1])

    logger.info(f"Deleted {count} proxies")


# ======================================
# 3. Работа с группами
# ======================================
async def upsert_group(conn: asyncpg.Connection, group_dict: Dict[str, Any]) -> None:
    """
    Вставка или обновление группы в БД.

    Args:
        conn: Подключение к БД
        group_dict: Словарь с данными группы из groups.json
    """
    logger.info(f"Upserting group '{group_dict['name']}'...")

    await conn.execute("""
        INSERT INTO groups (
            name, enabled, category, region_slug, brands, models,
            all_russia, enrich_q, blocklist_mode, telegram_chat_ids
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (name) DO UPDATE SET
            enabled = EXCLUDED.enabled,
            category = EXCLUDED.category,
            region_slug = EXCLUDED.region_slug,
            brands = EXCLUDED.brands,
            models = EXCLUDED.models,
            all_russia = EXCLUDED.all_russia,
            enrich_q = EXCLUDED.enrich_q,
            blocklist_mode = EXCLUDED.blocklist_mode,
            telegram_chat_ids = EXCLUDED.telegram_chat_ids
    """,
        group_dict['name'],
        group_dict['enabled'],
        group_dict['category'],
        group_dict['region_slug'],
        group_dict['brands'],
        json.dumps(group_dict['models']),  # JSONB требует JSON строку
        group_dict['all_russia'],
        group_dict['enrich_q'],
        group_dict['blocklist_mode'],
        group_dict['telegram_chat_ids']
    )

    logger.info(f"Successfully upserted group '{group_dict['name']}'")


async def delete_group(conn: asyncpg.Connection, group_name: str) -> None:
    """
    Удаление группы из БД (CASCADE удалит связанные задачи).

    Args:
        conn: Подключение к БД
        group_name: Имя группы
    """
    logger.info(f"Deleting group '{group_name}'...")

    result = await conn.execute("""
        DELETE FROM groups WHERE name = $1
    """, group_name)

    if result == "DELETE 0":
        logger.warning(f"Group '{group_name}' not found")
    else:
        logger.info(f"Successfully deleted group '{group_name}'")


async def get_all_groups(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """
    Получение всех групп из БД.

    Args:
        conn: Подключение к БД

    Returns:
        Список словарей с данными групп
    """
    rows = await conn.fetch("SELECT * FROM groups ORDER BY name")
    return [dict(row) for row in rows]


async def group_exists(conn: asyncpg.Connection, group_name: str) -> bool:
    """
    Проверка существования группы.

    Args:
        conn: Подключение к БД
        group_name: Имя группы

    Returns:
        True если группа существует
    """
    row = await conn.fetchrow("""
        SELECT name FROM groups WHERE name = $1
    """, group_name)

    return row is not None


# ======================================
# 4. Работа с задачами
# ======================================
async def create_tasks_for_group(
    conn: asyncpg.Connection,
    group_name: str,
    task_data: List[Dict[str, str]]
) -> None:
    """
    Создание задач для группы.

    Args:
        conn: Подключение к БД
        group_name: Имя группы
        task_data: Список словарей с url и search_query
    """
    logger.info(f"Creating {len(task_data)} tasks for group '{group_name}'...")

    for idx, task in enumerate(task_data, 1):
        await conn.execute("""
            INSERT INTO tasks (group_name, url, search_query, status)
            VALUES ($1, $2, $3, 'pending')
            ON CONFLICT DO NOTHING
        """, group_name, task['url'], task.get('search_query'))

        logger.debug(f"Created task {idx}/{len(task_data)}: {task['url']}")

    logger.info(f"Successfully created {len(task_data)} tasks for group '{group_name}'")


async def delete_tasks_for_group(conn: asyncpg.Connection, group_name: str) -> None:
    """
    Удаление всех задач для группы.

    Args:
        conn: Подключение к БД
        group_name: Имя группы
    """
    logger.info(f"Deleting all tasks for group '{group_name}'...")

    result = await conn.execute("""
        DELETE FROM tasks WHERE group_name = $1
    """, group_name)

    count = int(result.split()[-1])
    logger.info(f"Deleted {count} tasks for group '{group_name}'")


async def count_tasks_for_group(conn: asyncpg.Connection, group_name: str) -> int:
    """
    Подсчет количества задач для группы.

    Args:
        conn: Подк��ючение к БД
        group_name: Имя группы

    Returns:
        Количество задач
    """
    row = await conn.fetchrow("""
        SELECT COUNT(*) as count FROM tasks WHERE group_name = $1
    """, group_name)

    return row['count']


# ======================================
# 5. Работа с блоклистами
# ======================================
async def load_sellers_batch(conn: asyncpg.Connection, sellers: List[str]) -> None:
    """
    Массовая вставка продавцов в блоклист.

    Args:
        conn: Подключение к БД
        sellers: Список имен продавцов
    """
    logger.info(f"Inserting {len(sellers)} sellers to blocklist...")

    for idx, seller_name in enumerate(sellers, 1):
        if not seller_name.strip():  # Пропускаем пустые строки
            continue

        await conn.execute("""
            INSERT INTO blocklist_sellers (seller_name)
            VALUES ($1)
            ON CONFLICT (seller_name) DO NOTHING
        """, seller_name.strip())

        logger.debug(f"Inserted seller {idx}/{len(sellers)}: '{seller_name}'")

    logger.info(f"Successfully inserted {len(sellers)} sellers to blocklist")


async def clear_sellers_blocklist(conn: asyncpg.Connection) -> None:
    """
    Удаление всех продавцов из блоклиста.

    Args:
        conn: Подключение к БД
    """
    logger.info("Clearing all sellers from blocklist...")

    result = await conn.execute("DELETE FROM blocklist_sellers")
    count = int(result.split()[-1])

    logger.info(f"Deleted {count} sellers from blocklist")
