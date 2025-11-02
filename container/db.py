"""
Avito Parser Database Module
Принцип: KISS - прямые SQL запросы через asyncpg, никаких ORM
"""

import os
import asyncio
import logging
from typing import Optional, Dict, Any, Set

import asyncpg


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


# ======================================
# 1. Создание пула подключений
# ======================================
async def create_pool() -> asyncpg.Pool:
    """
    Создает пул подключений к PostgreSQL с auto-reconnection.
    Параметры подключения читаются из переменных окружения.

    Returns:
        asyncpg.Pool: Пул подключений к БД
    """
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    database = os.getenv("POSTGRES_DB", "avito_parser")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")

    logger.info(f"Connecting to PostgreSQL: {user}@{host}:{port}/{database}")

    try:
        pool = await asyncpg.create_pool(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            min_size=5,
            max_size=20,
            command_timeout=60,  # таймаут для команд
        )
        logger.info("PostgreSQL connection pool created successfully")
        return pool
    except Exception as e:
        logger.error(f"Failed to create PostgreSQL pool: {e}")
        raise


# ======================================
# 2. Работа с задачами
# ======================================
async def get_pending_task(pool: asyncpg.Pool, worker_id: int) -> Optional[Dict[str, Any]]:
    """
    Атомарный захват первой pending задачи (FOR UPDATE SKIP LOCKED).
    Обновляет статус на 'in_progress' и блокирует воркером.

    Args:
        pool: Пул подключений
        worker_id: ID воркера (1-15)

    Returns:
        Dict с данными задачи или None если нет pending задач
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Атомарный захват pending задачи
            row = await conn.fetchrow("""
                SELECT id, group_name, url, search_query, status, created_at
                FROM tasks
                WHERE status = 'pending'
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """)

            if not row:
                return None

            # Обновляем статус и блокируем
            await conn.execute("""
                UPDATE tasks
                SET status = 'in_progress',
                    locked_at = NOW(),
                    locked_by = $1,
                    updated_at = NOW()
                WHERE id = $2
            """, worker_id, row['id'])

            logger.info(f"Worker {worker_id} acquired task {row['id']} (group: {row['group_name']})")

            return dict(row)


async def complete_task(pool: asyncpg.Pool, task_id: int) -> None:
    """
    Завершает задачу и планирует её возврат в pending через 1 секунду.

    Args:
        pool: Пул подключений
        task_id: ID задачи
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tasks
            SET status = 'completed',
                updated_at = NOW()
            WHERE id = $1
        """, task_id)

    logger.info(f"Task {task_id} marked as completed")

    # Планируем возврат в pending через 1 секунду
    await asyncio.sleep(1)
    await reset_completed_to_pending(pool, task_id)


async def reset_completed_to_pending(pool: asyncpg.Pool, task_id: int) -> None:
    """
    Возвращает completed задачу обратно в pending (циклический режим).

    Args:
        pool: Пул подключений
        task_id: ID задачи
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tasks
            SET status = 'pending',
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = $1 AND status = 'completed'
        """, task_id)

    logger.info(f"Task {task_id} reset to pending (circular mode)")


async def fail_task(pool: asyncpg.Pool, task_id: int) -> None:
    """
    Помечает задачу как failed и освобождает блокировку.

    Args:
        pool: Пул подключений
        task_id: ID задачи
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tasks
            SET status = 'failed',
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = $1
        """, task_id)

    logger.warning(f"Task {task_id} marked as failed")


# ======================================
# 3. Работа с распарсенными объявлениями
# ======================================
async def save_item(pool: asyncpg.Pool, item_data: Dict[str, Any]) -> None:
    """
    Сохраняет распарсенное объявление в базу (для истории).

    Args:
        pool: Пул подключений
        item_data: Словарь с данными объявления (item_id, group_name, title, price, etc)
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO parsed_items (
                item_id, group_name, title, price, currency,
                seller_name, location, published, url
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (item_id) DO NOTHING
        """,
            item_data['item_id'],
            item_data['group_name'],
            item_data['title'],
            item_data.get('price'),
            item_data.get('currency'),
            item_data.get('seller_name'),
            item_data.get('location'),
            item_data.get('published'),
            item_data['url']
        )

    logger.info(f"Saved item {item_data['item_id']} for group {item_data['group_name']}")


# ======================================
# 4. Работа с блоклистами (чтение)
# ======================================
async def get_blocked_items_global(pool: asyncpg.Pool) -> Set[str]:
    """
    Получает все item_id из глобального блоклиста.

    Args:
        pool: Пул подключений

    Returns:
        Set[str]: Множество заблокированных item_id
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT item_id FROM blocklist_items_global")

    return {row['item_id'] for row in rows}


async def get_blocked_items_local(pool: asyncpg.Pool, group_name: str) -> Set[str]:
    """
    Получает все item_id из локального блоклиста для конкретной группы.

    Args:
        pool: Пул подключений
        group_name: Имя группы

    Returns:
        Set[str]: Множество заблокированных item_id для группы
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT item_id FROM blocklist_items_local
            WHERE group_name = $1
        """, group_name)

    return {row['item_id'] for row in rows}


async def get_blocked_sellers(pool: asyncpg.Pool) -> Set[str]:
    """
    Получает все имена продавцов из глобального блоклиста.

    Args:
        pool: Пул подключений

    Returns:
        Set[str]: Множество заблокированных seller_name
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT seller_name FROM blocklist_sellers")

    return {row['seller_name'] for row in rows}


# ======================================
# 5. Работа с блоклистами (добавление)
# ======================================
async def add_to_blocklist_global(pool: asyncpg.Pool, item_id: str) -> None:
    """
    Добавляет item_id в глобальный блоклист.
    Вызывается ТОЛЬКО после успешной отправки в Telegram.

    Args:
        pool: Пул подключений
        item_id: ID объявления
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO blocklist_items_global (item_id)
            VALUES ($1)
            ON CONFLICT (item_id) DO NOTHING
        """, item_id)

    logger.info(f"Added item {item_id} to global blocklist")


async def add_to_blocklist_local(pool: asyncpg.Pool, item_id: str, group_name: str) -> None:
    """
    Добавляет item_id в локальный блоклист группы.
    Вызывается ТОЛЬКО после успешной отправки в Telegram.

    Args:
        pool: Пул подключений
        item_id: ID объявления
        group_name: Имя группы
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO blocklist_items_local (item_id, group_name)
            VALUES ($1, $2)
            ON CONFLICT (item_id, group_name) DO NOTHING
        """, item_id, group_name)

    logger.info(f"Added item {item_id} to local blocklist for group {group_name}")


async def add_to_blocklist_sellers(pool: asyncpg.Pool, seller_name: str) -> None:
    """
    Добавляет продавца в глобальный блоклист.

    Args:
        pool: Пул подключений
        seller_name: Имя продавца
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO blocklist_sellers (seller_name)
            VALUES ($1)
            ON CONFLICT (seller_name) DO NOTHING
        """, seller_name)

    logger.info(f"Added seller '{seller_name}' to blocklist")


# ======================================
# 6. Работа с прокси
# ======================================
async def get_free_proxy(pool: asyncpg.Pool, worker_id: int) -> Optional[str]:
    """
    Атомарный захват свободного прокси (FOR UPDATE SKIP LOCKED).
    Блокирует прокси для воркера.

    Args:
        pool: Пул подключений
        worker_id: ID воркера (1-15)

    Returns:
        proxy_url или None если нет свободных прокси
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Атомарный захват свободного прокси
            row = await conn.fetchrow("""
                SELECT id, proxy_url
                FROM proxies
                WHERE is_banned = FALSE
                  AND locked_at IS NULL
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """)

            if not row:
                logger.warning(f"Worker {worker_id}: No free proxies available")
                return None

            # Блокируем прокси
            await conn.execute("""
                UPDATE proxies
                SET locked_at = NOW(),
                    locked_by = $1
                WHERE id = $2
            """, worker_id, row['id'])

            logger.info(f"Worker {worker_id} acquired proxy {row['proxy_url']}")
            return row['proxy_url']


async def ban_proxy(pool: asyncpg.Pool, proxy_url: str) -> None:
    """
    Банит прокси навсегда (403/407 ошибки) и освобождает блокировку.

    Args:
        pool: Пул подключений
        proxy_url: URL прокси
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE proxies
            SET is_banned = TRUE,
                locked_at = NULL,
                locked_by = NULL
            WHERE proxy_url = $1
        """, proxy_url)

    logger.warning(f"Proxy {proxy_url} has been banned permanently (403/407)")


async def release_proxy(pool: asyncpg.Pool, worker_id: int) -> None:
    """
    Освобождает прокси, заблокированный воркером.

    Args:
        pool: Пул подключений
        worker_id: ID воркера (1-15)
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE proxies
            SET locked_at = NULL,
                locked_by = NULL
            WHERE locked_by = $1
        """, worker_id)

    logger.info(f"Worker {worker_id} released proxy")


# ======================================
# 7. Очистка зависших ресурсов
# ======================================
async def cleanup_stale_resources(pool: asyncpg.Pool) -> None:
    """
    Освобождает зависшие ресурсы:
    - Задачи locked_at > 10 минут
    - Прокси locked_at > 30 минут

    Args:
        pool: Пул подключений
    """
    async with pool.acquire() as conn:
        # Освобождаем зависшие задачи (>10 минут)
        tasks_freed = await conn.execute("""
            UPDATE tasks
            SET status = 'pending',
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE locked_at < NOW() - INTERVAL '10 minutes'
              AND status = 'in_progress'
        """)

        # Освобождаем зависшие прокси (>30 минут)
        proxies_freed = await conn.execute("""
            UPDATE proxies
            SET locked_at = NULL,
                locked_by = NULL
            WHERE locked_at < NOW() - INTERVAL '30 minutes'
        """)

        if tasks_freed != 'UPDATE 0':
            logger.info(f"Cleanup: freed {tasks_freed.split()[1]} stale tasks")
        if proxies_freed != 'UPDATE 0':
            logger.info(f"Cleanup: freed {proxies_freed.split()[1]} stale proxies")


async def release_worker_resources(pool: asyncpg.Pool, worker_id: int) -> None:
    """
    Освобождает все ресурсы, захваченные воркером (при краше/остановке).

    Args:
        pool: Пул подключений
        worker_id: ID воркера (1-15)
    """
    async with pool.acquire() as conn:
        # Освобождаем задачи воркера
        await conn.execute("""
            UPDATE tasks
            SET status = 'pending',
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE locked_by = $1
        """, worker_id)

        # Освобождаем прокси воркера
        await conn.execute("""
            UPDATE proxies
            SET locked_at = NULL,
                locked_by = NULL
            WHERE locked_by = $1
        """, worker_id)

    logger.info(f"Released all resources for worker {worker_id}")
