#!/usr/bin/env python3
"""
Миграция: добавление CASCADE к foreign keys и оптимизационных индексов
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
    logger.info("MIGRATION: Add CASCADE rules and optimization indexes")
    logger.info("="*60)

    try:
        conn = await db_utils.connect_db()
    except Exception as e:
        logger.error(f"Database connection failed: {type(e).__name__}: {e}")
        sys.exit(1)

    try:
        # Шаг 1: Добавление CASCADE к tasks.group_name
        logger.info("Step 1: Adding CASCADE to tasks.group_name foreign key...")

        await conn.execute("""
            ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_group_name_fkey;
            ALTER TABLE tasks ADD CONSTRAINT tasks_group_name_fkey
                FOREIGN KEY (group_name) REFERENCES groups(name) ON DELETE CASCADE;
        """)
        logger.info("✓ CASCADE added to tasks.group_name")

        # Шаг 2: Добавление CASCADE к parsed_items.group_name
        logger.info("Step 2: Adding CASCADE to parsed_items.group_name foreign key...")

        await conn.execute("""
            ALTER TABLE parsed_items DROP CONSTRAINT IF EXISTS parsed_items_group_name_fkey;
            ALTER TABLE parsed_items ADD CONSTRAINT parsed_items_group_name_fkey
                FOREIGN KEY (group_name) REFERENCES groups(name) ON DELETE CASCADE;
        """)
        logger.info("✓ CASCADE added to parsed_items.group_name")

        # Шаг 3: Добавление CASCADE к blocklist_items_local.group_name
        logger.info("Step 3: Adding CASCADE to blocklist_items_local.group_name foreign key...")

        await conn.execute("""
            ALTER TABLE blocklist_items_local DROP CONSTRAINT IF EXISTS blocklist_items_local_group_name_fkey;
            ALTER TABLE blocklist_items_local ADD CONSTRAINT blocklist_items_local_group_name_fkey
                FOREIGN KEY (group_name) REFERENCES groups(name) ON DELETE CASCADE;
        """)
        logger.info("✓ CASCADE added to blocklist_items_local.group_name")

        # Шаг 4: Добавление оптимизационных индексов
        logger.info("Step 4: Adding optimization indexes...")

        # Composite index для queue priority
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_queue_priority
                ON tasks(status, successful_parses ASC, created_at ASC)
                WHERE status = 'pending';
        """)
        logger.info("✓ Created idx_tasks_queue_priority")

        # Index для parsed_items(group_name)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_parsed_items_group_name
                ON parsed_items(group_name);
        """)
        logger.info("✓ Created idx_parsed_items_group_name")

        # Indexes для locked_by
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_locked_by
                ON tasks(locked_by) WHERE locked_by IS NOT NULL;
        """)
        logger.info("✓ Created idx_tasks_locked_by")

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_proxies_locked_by
                ON proxies(locked_by) WHERE locked_by IS NOT NULL;
        """)
        logger.info("✓ Created idx_proxies_locked_by")

        # Шаг 5: Проверка результата
        logger.info("Step 5: Verifying changes...")

        # Проверка CASCADE
        cascade_count = await conn.fetchval("""
            SELECT COUNT(*)
            FROM information_schema.referential_constraints
            WHERE constraint_schema = 'public'
            AND delete_rule = 'CASCADE'
            AND constraint_name IN (
                'tasks_group_name_fkey',
                'parsed_items_group_name_fkey',
                'blocklist_items_local_group_name_fkey'
            )
        """)

        if cascade_count == 3:
            logger.info(f"✓ All 3 CASCADE rules verified")
        else:
            logger.warning(f"⚠ Only {cascade_count}/3 CASCADE rules found")

        # Проверка индексов
        index_count = await conn.fetchval("""
            SELECT COUNT(*)
            FROM pg_indexes
            WHERE schemaname = 'public'
            AND indexname IN (
                'idx_tasks_queue_priority',
                'idx_parsed_items_group_name',
                'idx_tasks_locked_by',
                'idx_proxies_locked_by'
            )
        """)

        if index_count == 4:
            logger.info(f"✓ All 4 indexes verified")
        else:
            logger.warning(f"⚠ Only {index_count}/4 indexes found")

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
