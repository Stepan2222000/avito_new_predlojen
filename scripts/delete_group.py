#!/usr/bin/env python3
"""
Скрипт удаления группы и её задач из БД
Использование:
    POSTGRES_HOST=... POSTGRES_PORT=... POSTGRES_DB=... \
    POSTGRES_USER=... POSTGRES_PASSWORD=... \
    python scripts/delete_group.py
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

    # 1. Подключение к БД
    try:
        conn = await db_utils.connect_db()
    except Exception as e:
        logger.error(f"Database connection failed: {type(e).__name__}: {e}")
        sys.exit(1)

    try:
        # 2. Показать доступные группы
        groups = await db_utils.get_all_groups(conn)

        if not groups:
            print("\n" + "="*60)
            print("No groups found in database")
            print("="*60)
            return

        print("\n" + "="*60)
        print(f"Available groups ({len(groups)}):")
        for idx, group in enumerate(groups, 1):
            print(f"  {idx}. {group['name']} ({group['category']}, {group['region_slug']})")
        print("="*60)

        # 3. Интерактивный ввод имени группы
        group_name = input("\nEnter group name to delete: ").strip()

        if not group_name:
            logger.error("Group name cannot be empty")
            sys.exit(1)

        # 4. Проверка существования группы
        logger.info(f"Checking if group '{group_name}' exists...")

        exists = await db_utils.group_exists(conn, group_name)

        if not exists:
            logger.error(f"Group '{group_name}' not found in database")
            sys.exit(1)

        # 5. Подсчет связанных задач
        task_count = await db_utils.count_tasks_for_group(conn, group_name)

        logger.info(f"Found group '{group_name}' with {task_count} task(s)")

        # 6. Подтверждение удаления
        print("\n" + "="*60)
        print(f"WARNING: This will delete:")
        print(f"  - Group: {group_name}")
        print(f"  - Tasks: {task_count}")
        print("="*60)

        while True:
            choice = input(f"Delete group '{group_name}' and all its tasks? (y/n): ").strip().lower()
            if choice in ['y', 'n']:
                break
            print("Please enter 'y' or 'n'")

        if choice == 'n':
            logger.info("Deletion cancelled by user")
            return

        # 7. Удаление группы (CASCADE автоматически удалит задачи)
        logger.info(f"Deleting group '{group_name}'...")

        await db_utils.delete_group(conn, group_name)

        logger.info("="*60)
        logger.info(f"SUCCESS: Deleted group '{group_name}' and {task_count} task(s)")
        logger.info("="*60)

    except Exception as e:
        logger.error(f"Failed to delete group: {type(e).__name__}: {e}", exc_info=True)
        sys.exit(1)

    finally:
        await conn.close()
        logger.info("Database connection closed")


if __name__ == "__main__":
    asyncio.run(main())
