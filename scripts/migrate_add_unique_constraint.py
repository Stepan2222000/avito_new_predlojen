#!/usr/bin/env python3
"""
Миграция: добавление UNIQUE constraint на tasks(group_name, url)
Удаляет дубликаты перед добавлением constraint
"""

import sys
import asyncio
import logging

import db_utils


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


async def main():
    """Применяет миграцию к БД"""

    logger.info("="*60)
    logger.info("MIGRATION: Add UNIQUE constraint on tasks(group_name, url)")
    logger.info("="*60)

    # Подключение к БД
    try:
        conn = await db_utils.connect_db()
    except Exception as e:
        logger.error(f"Database connection failed: {type(e).__name__}: {e}")
        sys.exit(1)

    try:
        # Шаг 1: Проверка существования constraint
        logger.info("Step 1: Checking if constraint already exists...")

        existing = await conn.fetchval("""
            SELECT COUNT(*)
            FROM pg_constraint
            WHERE conname = 'tasks_group_name_url_key'
        """)

        if existing > 0:
            logger.info("Constraint already exists, skipping migration")
            return

        # Шаг 2: Подсчет дубликатов
        logger.info("Step 2: Counting duplicate tasks...")

        duplicates = await conn.fetch("""
            SELECT group_name, url, COUNT(*) as cnt
            FROM tasks
            GROUP BY group_name, url
            HAVING COUNT(*) > 1
        """)

        if duplicates:
            logger.info(f"Found {len(duplicates)} duplicate task pairs:")
            for dup in duplicates:
                logger.info(f"  - {dup['group_name']}: {dup['url']} (x{dup['cnt']})")
        else:
            logger.info("No duplicates found")

        # Шаг 3: Удаление дубликатов (оставляем самую старую запись)
        if duplicates:
            logger.info("Step 3: Removing duplicates (keeping oldest by created_at)...")

            deleted_count = 0
            for dup in duplicates:
                result = await conn.execute("""
                    DELETE FROM tasks
                    WHERE id IN (
                        SELECT id FROM tasks
                        WHERE group_name = $1 AND url = $2
                        ORDER BY created_at DESC
                        OFFSET 1
                    )
                """, dup['group_name'], dup['url'])

                count = int(result.split()[-1])
                deleted_count += count
                logger.info(f"  Deleted {count} duplicates for: {dup['group_name']} / {dup['url']}")

            logger.info(f"Total deleted: {deleted_count} duplicate tasks")

        # Шаг 4: Добавление UNIQUE constraint
        logger.info("Step 4: Adding UNIQUE constraint...")

        await conn.execute("""
            ALTER TABLE tasks
            ADD CONSTRAINT tasks_group_name_url_key
            UNIQUE (group_name, url)
        """)

        logger.info("UNIQUE constraint added successfully")

        # Шаг 5: Проверка результата
        logger.info("Step 5: Verifying constraint...")

        constraint_check = await conn.fetchval("""
            SELECT COUNT(*)
            FROM pg_constraint
            WHERE conname = 'tasks_group_name_url_key'
        """)

        if constraint_check > 0:
            logger.info("✓ Constraint verified successfully")
        else:
            logger.error("✗ Constraint verification failed")
            sys.exit(1)

        # Финальная статистика
        logger.info("="*60)
        logger.info("MIGRATION COMPLETED SUCCESSFULLY")
        logger.info("="*60)

        tasks_count = await conn.fetchval("SELECT COUNT(*) FROM tasks")
        logger.info(f"Total tasks after migration: {tasks_count}")

    except Exception as e:
        logger.error(f"Migration failed: {type(e).__name__}: {e}", exc_info=True)
        sys.exit(1)

    finally:
        await conn.close()
        logger.info("Database connection closed")


if __name__ == "__main__":
    asyncio.run(main())
