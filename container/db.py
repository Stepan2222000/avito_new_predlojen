"""
Avito Parser Database Module
Принцип: KISS - прямые SQL запросы через asyncpg, никаких ORM
"""

import os
import asyncio
import logging
from typing import Optional, Dict, Any, List

import asyncpg


# Логгер модуля (настройка в main.py)
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

    # Рассчитываем pool size на основе количества воркеров
    worker_count = int(os.getenv("WORKER_COUNT", "10"))
    min_pool_size = worker_count + 5  # воркеры + cleanup + запас
    max_pool_size = (worker_count * 3) + 5  # 2-3 соединения на воркера пик

    logger.info(f"Connection pool sizing: min={min_pool_size}, max={max_pool_size} (based on {worker_count} workers)")

    try:
        pool = await asyncpg.create_pool(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            min_size=min_pool_size,
            max_size=max_pool_size,
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
    Возвращает данные задачи с JOIN к groups для получения telegram_chat_ids и blocklist_mode.

    Args:
        pool: Пул подключений
        worker_id: ID воркера (1-15)

    Returns:
        Dict с данными задачи или None если нет pending задач
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Атомарный захват pending задачи с JOIN к groups
            row = await conn.fetchrow("""
                WITH min_success AS (
                    SELECT MIN(t2.successful_parses) AS value
                    FROM tasks t2
                    JOIN groups g2 ON t2.group_name = g2.name
                    WHERE t2.status = 'pending' AND g2.enabled = TRUE
                )
                SELECT t.id, t.group_name, t.url, t.search_query, t.status, t.attempts, t.created_at,
                       g.telegram_chat_ids, g.blocklist_mode, g.min_price, g.max_price
                FROM tasks t
                JOIN groups g ON t.group_name = g.name
                JOIN min_success ms ON ms.value IS NOT NULL AND t.successful_parses = ms.value
                WHERE t.status = 'pending' AND g.enabled = TRUE
                ORDER BY t.created_at ASC
                LIMIT 1
                FOR UPDATE OF t SKIP LOCKED
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


async def complete_task(
    pool: asyncpg.Pool,
    task_id: int,
    cooldown_seconds: float = 1.0,
) -> None:
    """
    Завершает задачу, сразу возвращая её в pending.
    Инкрементирует successful_parses и делает короткий cooldown (по умолчанию 1 секунда).

    Args:
        pool: Пул подключений
        task_id: ID задачи
        cooldown_seconds: Пауза перед следующей задачей
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE tasks
            SET status = 'pending',
                successful_parses = successful_parses + 1,
                attempts = 0,
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = $1
            RETURNING successful_parses
        """, task_id)

    successful_count = row['successful_parses'] if row else 0
    logger.info(
        f"Task {task_id} completed (total successful parses: {successful_count})"
    )

    if cooldown_seconds and cooldown_seconds > 0:
        await asyncio.sleep(cooldown_seconds)


async def reset_all_completed_tasks(pool: asyncpg.Pool) -> None:
    """Возвращает все зависшие completed задачи обратно в pending."""

    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE tasks
            SET status = 'pending',
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE status = 'completed'
        """)

    if result != 'UPDATE 0':
        logger.info(f"Recovered completed tasks: {result}")


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


async def retry_task(pool: asyncpg.Pool, task_id: int) -> None:
    """
    Возвращает задачу в pending с увеличением счетчика попыток.
    Используется только для page.goto() ошибок с разными прокси.

    Args:
        pool: Пул подключений
        task_id: ID задачи
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tasks
            SET status = 'pending',
                attempts = attempts + 1,
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = $1
        """, task_id)

    logger.info(f"Task {task_id} scheduled for retry (attempts incremented)")


async def retry_task_no_increment(pool: asyncpg.Pool, task_id: int) -> None:
    """
    Возвращает задачу в pending БЕЗ увеличения счетчика попыток.
    Используется для infrastructure ошибок: капча, 429, NOT_DETECTED, Telegram, прокси.

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
            WHERE id = $1
        """, task_id)

    logger.info(f"Task {task_id} reset to pending (infrastructure issue, attempts NOT incremented)")


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
# 4. Batch-фильтрация listings через SQL
# ======================================
async def filter_listings_batch(
    pool: asyncpg.Pool,
    listings: List[Dict[str, Any]],
    blocklist_mode: str,
    group_name: str
) -> List[Dict[str, Any]]:
    """
    Batch-фильтрация listings через SQL (без загрузки блоклистов в память).
    Проверяет все listings одним запросом.

    Проверки:
    1. seller_name NOT IN blocklist_sellers
    2. item_id NOT IN blocklist (global или local в зависимости от режима)

    Args:
        pool: Пул подключений
        listings: Список объявлений для проверки
        blocklist_mode: 'global' или 'local'
        group_name: Имя группы (для local режима)

    Returns:
        Только незаблокированные listings
    """
    if not listings:
        return []

    # Извлекаем массивы item_id и seller_name
    item_ids = [listing['item_id'] for listing in listings]
    seller_names = [listing.get('seller_name') for listing in listings]

    async with pool.acquire() as conn:
        if blocklist_mode == 'global':
            # Global режим: проверяем blocklist_items_global
            rows = await conn.fetch("""
                WITH all_listings AS (
                    SELECT unnest($1::text[]) as item_id, unnest($2::text[]) as seller_name
                )
                SELECT * FROM all_listings
                WHERE item_id NOT IN (SELECT item_id FROM blocklist_items_global)
                  AND (
                    seller_name IS NULL
                    OR seller_name NOT IN (SELECT seller_name FROM blocklist_sellers)
                  )
            """, item_ids, seller_names)
        else:  # local
            # Local режим: проверяем blocklist_items_local для группы
            rows = await conn.fetch("""
                WITH all_listings AS (
                    SELECT unnest($1::text[]) as item_id, unnest($2::text[]) as seller_name
                )
                SELECT * FROM all_listings
                WHERE item_id NOT IN (SELECT item_id FROM blocklist_items_local WHERE group_name = $3)
                  AND (
                    seller_name IS NULL
                    OR seller_name NOT IN (SELECT seller_name FROM blocklist_sellers)
                  )
            """, item_ids, seller_names, group_name)

    # Создаем set незаблокированных item_id
    allowed_item_ids = {row['item_id'] for row in rows}

    # Фильтруем исходный список
    filtered = [listing for listing in listings if listing['item_id'] in allowed_item_ids]

    logger.info(f"Batch filter: {len(listings)} listings → {len(filtered)} after blocklist check")

    return filtered


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
            # Атомарный захват свободного прокси (рандомный выбор)
            row = await conn.fetchrow("""
                SELECT id, proxy_url
                FROM proxies
                WHERE is_banned = FALSE
                  AND locked_at IS NULL
                ORDER BY RANDOM()
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
