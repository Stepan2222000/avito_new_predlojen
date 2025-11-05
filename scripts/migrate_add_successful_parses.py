#!/usr/bin/env python3
"""
Миграция: добавление поля successful_parses в таблицу tasks
Отслеживает количество успешных парсингов для каждой задачи
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
    logger.info("MIGRATION: Add successful_parses field to tasks table")
    logger.info("="*60)

    # Подключение к БД
    try:
        conn = await db_utils.connect_db()
    except Exception as e:
        logger.error(f"Database connection failed: {type(e).__name__}: {e}")
        sys.exit(1)

    try:
        # Шаг 1: Проверка существования поля
        logger.info("Step 1: Checking if field already exists...")

        existing = await conn.fetchval("""
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_name = 'tasks'
            AND column_name = 'successful_parses'
        """)

        if existing > 0:
            logger.info("Field 'successful_parses' already exists, skipping migration")
            return

        # Шаг 2: Добавление поля
        logger.info("Step 2: Adding successful_parses field...")

        await conn.execute("""
            ALTER TABLE tasks
            ADD COLUMN successful_parses INTEGER NOT NULL DEFAULT 0
        """)

        logger.info("Field added successfully")

        # Шаг 3: Проверка результата
        logger.info("Step 3: Verifying field...")

        field_check = await conn.fetchval("""
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_name = 'tasks'
            AND column_name = 'successful_parses'
        """)

        if field_check > 0:
            logger.info("✓ Field verified successfully")
        else:
            logger.error("✗ Field verification failed")
            sys.exit(1)

        # Шаг 4: Статистика
        logger.info("Step 4: Checking current data...")

        tasks_count = await conn.fetchval("SELECT COUNT(*) FROM tasks")
        logger.info(f"Total tasks: {tasks_count}")
        logger.info(f"All tasks initialized with successful_parses = 0")

        # Финальное сообщение
        logger.info("="*60)
        logger.info("MIGRATION COMPLETED SUCCESSFULLY")
        logger.info("="*60)

    except Exception as e:
        logger.error(f"Migration failed: {type(e).__name__}: {e}", exc_info=True)
        sys.exit(1)

    finally:
        await conn.close()
        logger.info("Database connection closed")


if __name__ == "__main__":
    asyncio.run(main())
