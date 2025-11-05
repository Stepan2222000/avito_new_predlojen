"""
Avito Parser Main Orchestration Module
Принцип: KISS - простая оркестрация 15 async воркеров + фоновая очистка
"""

import asyncio
import os
import sys
import logging
import signal
import random
from typing import List, Optional

import db
from worker import run_worker


# Глобальные переменные для graceful shutdown
shutdown_event = asyncio.Event()
worker_tasks: List[asyncio.Task] = []
cleanup_task: Optional[asyncio.Task] = None
db_pool = None


# ======================================
# 1. Фоновая очистка зависших ресурсов
# ======================================
async def periodic_cleanup(pool):
    """
    Периодическая очистка зависших ресурсов (каждые 60 сек)
    - Задачи locked > 10 минут
    - Прокси locked > 30 минут
    """
    while not shutdown_event.is_set():
        await asyncio.sleep(60)

        # cleanup_stale_resources() уже логирует результаты
        await db.cleanup_stale_resources(pool)


# ======================================
# 2. Signal handlers для graceful shutdown
# ======================================
def handle_shutdown_signal(signum, frame):
    """
    Минимальный signal handler - только устанавливает флаг shutdown.
    Отмена задач происходит в async контексте (main finally блок).
    """
    logging.info(f"Received signal {signum}, initiating shutdown...")
    shutdown_event.set()


# ======================================
# 3. Главная функция
# ======================================
async def main():
    """
    Оркестрация:
    - 15 async воркеров (по умолчанию)
    - 1 фоновая задача cleanup
    - Graceful shutdown по SIGTERM/SIGINT
    """
    global worker_tasks, cleanup_task, db_pool

    # Настройка логирования (централизованно)
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(levelname)s] %(name)s: %(message)s',
        stream=sys.stdout
    )
    logger = logging.getLogger(__name__)

    # Чтение конфигурации
    try:
        worker_count = int(os.getenv("WORKER_COUNT", "15"))
        if not 1 <= worker_count <= 50:
            logger.warning(f"WORKER_COUNT={worker_count} out of range [1-50], using default 15")
            worker_count = 15
    except ValueError:
        logger.warning(f"Invalid WORKER_COUNT value, using default 15")
        worker_count = 15

    logger.info(f"Starting Avito Parser with {worker_count} workers")
    logger.info(f"Database: {os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5432')}")

    # Создание БД pool с retry логикой и exponential backoff
    max_retries = 10
    base_delay = 2  # начальная задержка в секундах

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Attempting to connect to database (attempt {attempt}/{max_retries})...")
            db_pool = await db.create_pool()
            logger.info("Database connection pool created successfully")
            break
        except Exception as e:
            logger.error(f"Database connection failed: {type(e).__name__}: {e}")

            if attempt == max_retries:
                logger.critical("Max retry attempts reached, exiting...")
                sys.exit(1)

            # Exponential backoff: 2, 4, 8, 16, 32, 64... (max 60s)
            delay = min(base_delay * (2 ** (attempt - 1)), 60)
            # Добавляем jitter (случайное отклонение ±10%)
            jitter = random.uniform(-0.1 * delay, 0.1 * delay)
            total_delay = delay + jitter

            logger.info(f"Retrying in {total_delay:.1f} seconds (exponential backoff)...")
            await asyncio.sleep(total_delay)

    # Setup signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    try:
        # Запуск фоновой задачи cleanup
        cleanup_task = asyncio.create_task(periodic_cleanup(db_pool))
        logger.info("Cleanup task started")

        # Запуск воркеров с передачей shutdown_event
        worker_tasks = [
            asyncio.create_task(run_worker(worker_id, db_pool, shutdown_event))
            for worker_id in range(1, worker_count + 1)
        ]
        logger.info(f"Started {worker_count} worker tasks")

        # Ожидание завершения всех задач (работают бесконечно до shutdown)
        await asyncio.gather(*worker_tasks, cleanup_task, return_exceptions=True)

    except asyncio.CancelledError:
        logger.info("Tasks cancelled, shutting down...")

    except Exception as e:
        logger.critical(f"Critical error in main: {type(e).__name__}: {e}", exc_info=True)

    finally:
        # Graceful shutdown: отмена задач в async контексте
        logger.info("Shutting down tasks...")

        # Отмена всех worker задач
        if worker_tasks:
            for task in worker_tasks:
                if not task.done():
                    task.cancel()

        # Отмена cleanup задачи
        if cleanup_task and not cleanup_task.done():
            cleanup_task.cancel()

        # Ожидание завершения всех отмен
        if worker_tasks or cleanup_task:
            all_tasks = worker_tasks + ([cleanup_task] if cleanup_task else [])
            await asyncio.gather(*all_tasks, return_exceptions=True)
            logger.info("All tasks cancelled")

        # Закрытие БД pool
        if db_pool:
            await db_pool.close()
            logger.info("Database pool closed")

        logger.info("Shutdown complete")


# ======================================
# 4. Entry point
# ======================================
if __name__ == "__main__":
    asyncio.run(main())
