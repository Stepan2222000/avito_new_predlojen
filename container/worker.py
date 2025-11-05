"""
Воркер для парсинга Avito
Каждый воркер управляет своим браузером и обрабатывает задачи из БД
"""

import os
import asyncio
import logging
from typing import Optional, Dict, Any, List

from playwright.async_api import async_playwright, Browser, Page, Playwright
from avito_library import (
    detect_page_state,
    parse_catalog,
    resolve_captcha_flow,
)
from avito_library.detectors import (
    CAPTCHA_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
    CATALOG_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    PROXY_AUTH_DETECTOR_ID,
    NOT_DETECTED_STATE_ID,
)
from avito_library.parsers.catalog_parser import CatalogParseStatus

import db
from telegram_notifier import send_notification_to_multiple

# Константы из environment
CAPTCHA_MAX_ATTEMPTS = int(os.getenv("CAPTCHA_MAX_ATTEMPTS", "10"))
MAX_TASK_ATTEMPTS = int(os.getenv("MAX_TASK_ATTEMPTS", "5"))

# Поля для парсинга каталога (фиксированные)
CATALOG_FIELDS = ["item_id", "title", "price", "seller_name", "published"]

logger = logging.getLogger(__name__)


class WorkerBrowser:
    """Управление Playwright браузером для одного воркера"""

    def __init__(self, worker_id: int, display: str):
        self.worker_id = worker_id
        self.display = display  # DISPLAY для Xvfb (":1" до ":15")
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.current_proxy: Optional[str] = None

    async def start(self, proxy: str):
        """Запуск браузера с прокси"""
        # Закрыть предыдущий браузер если был открыт
        await self.close()

        # Парсинг прокси (формат: IP:PORT:USER:PASS)
        proxy_parts = proxy.split(":")
        server = f"http://{proxy_parts[0]}:{proxy_parts[1]}"
        username = proxy_parts[2]
        password = proxy_parts[3]

        # Запуск Playwright с chromium
        self.playwright = await async_playwright().start()

        # Настройка окружения с DISPLAY для Xvfb (только в Docker/Linux)
        import os
        browser_env = os.environ.copy()
        browser_env["DISPLAY"] = self.display

        self.browser = await self.playwright.chromium.launch(
            headless=False,  # ВАЖНО: headless=False + Xvfb
            proxy={
                "server": server,
                "username": username,
                "password": password,
            },
            env=browser_env,  # Передаем DISPLAY в процесс Chromium
        )

        # Создание контекста и долгоживущей page
        context = await self.browser.new_context()
        # Установка глобального таймаута для всех навигаций
        context.set_default_navigation_timeout(60000)  # 60 секунд
        self.page = await context.new_page()
        self.current_proxy = proxy

        logger.info(f"[Worker-{self.worker_id}] Browser started with proxy {server}")

    async def close(self):
        """Закрытие браузера и освобождение ресурсов"""
        if self.page:
            await self.page.close()
            self.page = None
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None


async def restart_browser_with_proxy(
    worker_browser: WorkerBrowser, pool, worker_id: int
) -> bool:
    """
    Полный перезапуск браузера с новым прокси

    Returns:
        True - браузер успешно перезапущен с новым прокси
        False - нет доступных прокси
    """
    # Закрытие текущего браузера
    await worker_browser.close()
    logger.info(f"[Worker-{worker_id}] Browser closed, getting new proxy...")

    # Получение нового прокси
    proxy = await db.get_free_proxy(pool, worker_id)

    if not proxy:
        logger.warning(f"[Worker-{worker_id}] No proxies available for restart")
        return False

    # Запуск браузера с новым прокси
    await worker_browser.start(proxy)
    return True


async def handle_page_state(
    state: str,
    page: Page,
    pool,
    worker_id: int,
    task: Dict,
    worker_browser: WorkerBrowser,
) -> tuple[str, bool]:
    """
    Обработка состояния страницы после детектора

    Returns:
        (new_state, should_retry) - новое состояние и флаг необходимости retry
    """

    # Капча, кнопка "Продолжить" или 429 - все решается через капчу
    if state in [CAPTCHA_DETECTOR_ID, CONTINUE_BUTTON_DETECTOR_ID, PROXY_BLOCK_429_DETECTOR_ID]:
        logger.info(f"[Worker-{worker_id}] Captcha/Continue/429 detected, resolving...")

        # Попытка решить капчу (max 10 попыток по умолчанию)
        html, solved = await resolve_captcha_flow(page, max_attempts=CAPTCHA_MAX_ATTEMPTS)

        if solved:
            # Капча решена, определяем новое состояние
            new_state = await detect_page_state(page)
            logger.info(f"[Worker-{worker_id}] Captcha solved, new state: {new_state}")
            return new_state, False
        else:
            # Капча не решилась - освобождаем прокси для смены
            logger.warning(
                f"[Worker-{worker_id}] Captcha unsolved after {CAPTCHA_MAX_ATTEMPTS} attempts"
            )
            await db.release_proxy(pool, worker_id)
            return state, True

    # Прокси блокировка 403/407 (постоянный бан)
    elif state in [PROXY_BLOCK_403_DETECTOR_ID, PROXY_AUTH_DETECTOR_ID]:
        logger.warning(f"[Worker-{worker_id}] Proxy blocked 403/407, banning proxy...")
        await db.ban_proxy(pool, worker_browser.current_proxy)
        await db.release_proxy(pool, worker_id)
        # Перезапуск браузера после бана прокси (safe close)
        try:
            await worker_browser.close()
        except Exception as e:
            logger.error(f"[Worker-{worker_id}] Failed to close browser after proxy ban: {e}")
        return state, True

    # Каталог - продолжаем нормальный парсинг
    elif state == CATALOG_DETECTOR_ID:
        return state, False

    # Неизвестное состояние
    elif state == NOT_DETECTED_STATE_ID:
        logger.error(f"[Worker-{worker_id}] NOT_DETECTED state")
        return state, True

    else:
        # Другие неожиданные состояния
        logger.error(f"[Worker-{worker_id}] Unexpected state: {state}")
        return state, True


async def process_task(
    task: Dict, pool, worker_id: int, worker_browser: WorkerBrowser
):
    """
    Обработка одной задачи парсинга

    Returns:
        True - задача успешно выполнена
        "navigation_error" - ошибка загрузки страницы (инкрементировать attempts)
        False - infrastructure issue: капча, 429, Telegram, прокси (НЕ инкрементировать attempts)
    """

    try:
        page = worker_browser.page

        # 1. Переход на URL каталога
        logger.info(f"[Worker-{worker_id}] Navigating to {task['url']}")

        try:
            response = await page.goto(
                task["url"], wait_until="domcontentloaded", timeout=60000
            )
        except Exception as nav_error:
            # Ошибка навигации (timeout, network error, etc) - это navigation_error
            error_type = type(nav_error).__name__

            # Специальная обработка TimeoutError - освобождаем прокси
            if "TimeoutError" in error_type or "Timeout" in str(nav_error):
                logger.warning(
                    f"[Worker-{worker_id}] Navigation timeout for task #{task['id']} (slow proxy?), "
                    f"releasing proxy and closing browser"
                )
                # Освобождаем прокси для смены (как при капче)
                await db.release_proxy(pool, worker_id)
                # Закрываем браузер для принудительной смены прокси при retry
                try:
                    await worker_browser.close()
                except Exception as close_err:
                    logger.error(f"[Worker-{worker_id}] Failed to close browser after timeout: {close_err}")
            else:
                logger.error(
                    f"[Worker-{worker_id}] Navigation error for task #{task['id']}: "
                    f"{error_type}: {nav_error}"
                )

            return "navigation_error"

        # 2. Детектирование состояния страницы
        state = await detect_page_state(page, last_response=response)
        logger.info(f"[Worker-{worker_id}] Page state: {state}")

        # 3. Обработка состояния (капча, блокировки)
        state, should_retry = await handle_page_state(
            state, page, pool, worker_id, task, worker_browser
        )

        if should_retry:
            return False

        # Должны быть в состоянии CATALOG для парсинга
        if state != CATALOG_DETECTOR_ID:
            logger.error(
                f"[Worker-{worker_id}] Not in catalog state after handling: {state}"
            )
            return False

        # 4. Парсинг каталога (только 1 страница, сортировка по дате)
        logger.info(f"[Worker-{worker_id}] Parsing catalog...")
        listings, meta = await parse_catalog(
            page,
            task["url"],
            fields=CATALOG_FIELDS,
            max_pages=1,
            sort_by_date=True,
            include_html=False,
            start_page=1,
        )

        logger.info(
            f"[Worker-{worker_id}] Parse result: {meta.status}, {len(listings)} listings"
        )

        # 5. Обработка статуса парсинга
        if meta.status == CatalogParseStatus.SUCCESS:
            pass  # Продолжаем с фильтрацией

        elif meta.status == CatalogParseStatus.EMPTY:
            # Каталог пустой - завершаем задачу успешно
            logger.info(f"[Worker-{worker_id}] Catalog empty, completing task")
            await db.complete_task(pool, task["id"])
            return True

        elif meta.status == CatalogParseStatus.CAPTCHA_UNSOLVED:
            # Капча не решилась внутри parse_catalog - пробуем еще раз
            logger.warning(
                f"[Worker-{worker_id}] Captcha unsolved in parse_catalog, trying resolve_captcha_flow..."
            )
            html, solved = await resolve_captcha_flow(page, max_attempts=CAPTCHA_MAX_ATTEMPTS)
            if not solved:
                # Капча все еще не решена - освобождаем прокси и retry
                await db.release_proxy(pool, worker_id)
            return False

        elif meta.status == CatalogParseStatus.RATE_LIMIT:
            # 429 - пробуем решить капчу еще раз
            logger.warning(f"[Worker-{worker_id}] Rate limit from parse_catalog, trying resolve_captcha_flow...")
            html, solved = await resolve_captcha_flow(page, max_attempts=CAPTCHA_MAX_ATTEMPTS)
            if not solved:
                # Капча не решена - освобождаем прокси и retry
                await db.release_proxy(pool, worker_id)
            return False

        elif meta.status == CatalogParseStatus.PROXY_BLOCKED:
            # 403/407 - бан прокси и перезапуск браузера
            logger.warning(f"[Worker-{worker_id}] Proxy blocked from parse_catalog")
            await db.ban_proxy(pool, worker_browser.current_proxy)
            await db.release_proxy(pool, worker_id)
            # Перезапуск браузера после бана прокси (safe close)
            try:
                await worker_browser.close()
            except Exception as e:
                logger.error(f"[Worker-{worker_id}] Failed to close browser after proxy ban: {e}")
            return False

        elif meta.status == CatalogParseStatus.NOT_DETECTED:
            logger.error(f"[Worker-{worker_id}] NOT_DETECTED from parse_catalog")
            return False

        else:
            logger.error(f"[Worker-{worker_id}] Unknown parse status: {meta.status}")
            return False

        # 6. Фильтрация listings
        if not listings:
            logger.info(
                f"[Worker-{worker_id}] No listings after parsing, completing task"
            )
            await db.complete_task(pool, task["id"])
            return True

        # Фильтр: только свежие объявления (сегодня, минуты/часы назад)
        fresh_keywords = ["сегодня", "минут", "минуту", "час", "часа", "часов"]
        today_listings = [
            listing
            for listing in listings
            if not listing.published_ago
            or any(keyword in (listing.published_ago or "").lower() for keyword in fresh_keywords)
        ]

        logger.info(
            f"[Worker-{worker_id}] After 'today' filter: {len(today_listings)} listings"
        )

        if not today_listings:
            await db.complete_task(pool, task["id"])
            return True

        # Преобразование CatalogListing объектов в словари для БД фильтрации
        today_listings_dicts = [
            {
                "item_id": listing.item_id,
                "title": listing.title,
                "price": listing.price,
                "seller_name": listing.seller_name,
                "published": listing.published_ago,
            }
            for listing in today_listings
        ]

        # Фильтр через БД (блоклисты продавцов и объявлений)
        filtered_listings = await db.filter_listings_batch(
            pool, today_listings_dicts, task["blocklist_mode"], task["group_name"]
        )

        logger.info(
            f"[Worker-{worker_id}] After blocklist filter: {len(filtered_listings)} listings"
        )

        if not filtered_listings:
            await db.complete_task(pool, task["id"])
            return True

        # 7. Отправка в Telegram и добавление в блоклисты
        for listing in filtered_listings:
            try:
                # Парсинг currency из price строки
                price_value = listing.get("price")
                price_str = str(price_value) if price_value is not None else ""
                currency = "₽"  # По умолчанию рубли
                if "€" in price_str:
                    currency = "€"
                elif "$" in price_str:
                    currency = "$"

                # Создаём обогащённый словарь для Telegram
                enriched_listing = {**listing, "currency": currency}

                # Отправка уведомления нескольким получателям
                chat_ids = task["telegram_chat_ids"]
                send_results = await send_notification_to_multiple(chat_ids, enriched_listing)

                # Логирование результатов отправки
                success_count = sum(1 for success in send_results.values() if success)
                failed_count = len(send_results) - success_count

                for chat_id, success in send_results.items():
                    if success:
                        logger.info(
                            f"[Worker-{worker_id}] Sent notification for {listing['item_id']} to {chat_id}"
                        )
                    else:
                        logger.error(
                            f"[Worker-{worker_id}] Failed to send notification for {listing['item_id']} to {chat_id}"
                        )

                # Вариант А: добавляем в блоклист если хотя бы одна отправка успешна
                if success_count > 0:
                    # ТОЛЬКО после успешной отправки добавляем в блоклисты
                    await db.add_to_blocklist_global(pool, listing["item_id"])
                    await db.add_to_blocklist_local(
                        pool, listing["item_id"], task["group_name"]
                    )

                    # Сохранение в историю parsed_items
                    price_value = listing.get("price")
                    await db.save_item(
                        pool,
                        {
                            "item_id": listing["item_id"],
                            "group_name": task["group_name"],
                            "title": listing.get("title"),
                            "price": str(price_value) if price_value is not None else None,
                            "currency": currency,
                            "seller_name": listing.get("seller_name"),
                            "published": listing.get("published"),
                            "url": f"https://www.avito.ru/{listing['item_id']}",
                        },
                    )

                    logger.info(
                        f"[Worker-{worker_id}] Added {listing['item_id']} to blocklist "
                        f"(sent to {success_count}/{len(send_results)} recipients)"
                    )
                else:
                    # Все отправки упали - retry задачи
                    logger.error(
                        f"[Worker-{worker_id}] All Telegram sends failed for {listing['item_id']} - task will retry"
                    )
                    return False

            except Exception as e:
                # Ошибка отправки в Telegram - НЕ добавляем в блоклист
                error_type = type(e).__name__
                logger.error(
                    f"[Worker-{worker_id}] Telegram error for {listing['item_id']}: "
                    f"{error_type}: {e}"
                )
                # Задача будет retry - объявление попробуем отправить снова
                return False

        # 8. Завершение задачи (циркулярный режим: completed → pending через 1 сек)
        await db.complete_task(pool, task["id"])
        logger.info(f"[Worker-{worker_id}] Task completed successfully")
        return True

    except Exception as e:
        error_type = type(e).__name__

        # TargetClosedError - браузер закрыт при shutdown (infrastructure issue)
        if "TargetClosedError" in error_type:
            logger.info(f"[Worker-{worker_id}] Browser closed during task processing (shutdown)")
            return False

        # TimeoutError при работе с Playwright - освобождаем прокси
        if "TimeoutError" in error_type or "NavigationError" in error_type:
            logger.warning(
                f"[Worker-{worker_id}] Navigation/Timeout error for task #{task.get('id', 'N/A')}: "
                f"{error_type}: {e} - releasing proxy"
            )
            # Освобождаем прокси для смены (как при капче)
            await db.release_proxy(pool, worker_id)
            # Закрываем браузер для принудительной смены прокси при retry
            try:
                await worker_browser.close()
            except Exception as close_err:
                logger.error(f"[Worker-{worker_id}] Failed to close browser after timeout: {close_err}")
            return "navigation_error"

        # Другие критические ошибки - infrastructure issues (parsing errors, etc)
        logger.error(
            f"[Worker-{worker_id}] Error processing task #{task.get('id', 'N/A')}: "
            f"{error_type}: {e}",
            exc_info=True
        )
        return False


async def run_worker(worker_id: int, pool, shutdown_event: asyncio.Event):
    """
    Главный цикл одного воркера

    Args:
        worker_id: ID воркера (1-15)
        pool: asyncpg connection pool
        shutdown_event: Event для graceful shutdown
    """

    display = f":{worker_id}"
    worker_browser = WorkerBrowser(worker_id, display)

    logger.info(f"[Worker-{worker_id}] Starting with DISPLAY={display}")

    consecutive_failures = 0  # Счетчик последовательных ошибок

    try:
        while not shutdown_event.is_set():
            try:
                # 1. Взятие pending задачи из БД (атомарно)
                task = await db.get_pending_task(pool, worker_id)

                if not task:
                    # Нет доступных задач - ждем
                    await asyncio.sleep(5)
                    continue

                logger.info(
                    f"[Worker-{worker_id}] Got task #{task['id']}: {task.get('search_query', 'N/A')}"
                )

                # 2. Проверка браузера и прокси
                if not worker_browser.browser or not worker_browser.current_proxy:
                    # Нужен новый браузер с прокси
                    proxy = await db.get_free_proxy(pool, worker_id)

                    if not proxy:
                        # Нет свободных прокси - закрываем браузер, освобождаем задачу и ждем
                        logger.warning(
                            f"[Worker-{worker_id}] No free proxies available, closing browser and retrying task"
                        )
                        # Закрываем браузер если он был открыт с предыдущим прокси
                        if worker_browser.browser:
                            await worker_browser.close()

                        await db.retry_task(pool, task["id"])
                        await asyncio.sleep(10)
                        continue

                    # Запуск браузера с прокси
                    await worker_browser.start(proxy)

                # 3. Обработка задачи
                result = await process_task(task, pool, worker_id, worker_browser)

                if result is True:
                    # Задача успешно выполнена
                    consecutive_failures = 0

                elif result == "navigation_error":
                    # Ошибка загрузки страницы - инкрементируем attempts
                    await db.retry_task(pool, task["id"])

                    # Проверка превышения лимита попыток (20 для navigation errors)
                    if task["attempts"] + 1 >= MAX_TASK_ATTEMPTS:
                        logger.error(
                            f"[Worker-{worker_id}] Task #{task['id']} exceeded max attempts "
                            f"({MAX_TASK_ATTEMPTS} navigation errors with different proxies), failing"
                        )
                        await db.fail_task(pool, task["id"])
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1

                else:  # result is False (infrastructure error)
                    # Infrastructure issue (капча, 429, Telegram, etc) - НЕ инкрементируем attempts
                    await db.retry_task_no_increment(pool, task["id"])
                    consecutive_failures += 1

                # Перезапуск браузера после 5 ошибок подряд
                if consecutive_failures >= 5:
                    logger.warning(
                                f"[Worker-{worker_id}] 5 consecutive failures, restarting browser"
                            )
                    await worker_browser.close()
                    consecutive_failures = 0

                # Небольшая пауза между задачами
                await asyncio.sleep(0)

            except Exception as e:
                # Детальное логирование критической ошибки в главном цикле
                error_type = type(e).__name__
                logger.error(
                    f"[Worker-{worker_id}] Critical error in main loop: {error_type}: {e}",
                    exc_info=True
                )

                # Освобождаем все ресурсы воркера в БД
                await db.release_worker_resources(pool, worker_id)
                await worker_browser.close()

                # Пауза перед перезапуском цикла
                logger.info(f"[Worker-{worker_id}] Restarting in 10 seconds...")
                await asyncio.sleep(10)

    except asyncio.CancelledError:
        # Graceful shutdown: немедленно закрываем браузер и освобождаем ресурсы
        logger.info(f"[Worker-{worker_id}] Received signal 2, initiating shutdown...")
        await worker_browser.close()
        await db.release_worker_resources(pool, worker_id)
        logger.info(f"[Worker-{worker_id}] Shutdown complete")
        raise  # Re-raise для корректной обработки в main

    finally:
        # Финальная очистка
        logger.info(f"[Worker-{worker_id}] Cleaning up...")
        await worker_browser.close()
        await db.release_worker_resources(pool, worker_id)
