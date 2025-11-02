# План реализации Avito-парсера

## Группа 1: Подготовка инфраструктуры

**Шаг 1.1** - Создать структуру директорий
- Директории `scripts/` и `scripts/data/` уже существуют
- Создать `container/`

**Шаг 1.2** - Создать пустые файлы данных
- `scripts/data/proxies.txt` - уже существует (формат: host:port:user:pass)
- `scripts/data/sellers_blocklist.txt` (пустой, по одному имени на строку)

**Шаг 1.3** - Создать `scripts/data/groups.json` с примером группы
- Использовать структуру из doc.md раздел 1 (auto_msk пример)

---

## Группа 2: База данных

**Шаг 2.1** - Создать `container/init_db.sql`
- 7 таблиц: groups, tasks, parsed_items, blocklist_items_global, blocklist_items_local, blocklist_sellers, proxies
- Индексы: idx_tasks_status, idx_tasks_locked_at, idx_proxies_is_banned, idx_proxies_locked_at
- Полная схема из doc.md раздел 3

**Шаг 2.2** - Создать `container/db.py` с asyncpg функциями
- create_pool() - создание connection pool (читает DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS из os.getenv)
- get_pending_task(worker_id) - взятие задачи с блокировкой (FOR UPDATE SKIP LOCKED)
- complete_task(task_id) - status='completed'
- reset_completed_to_pending() - completed → pending (через 1 сек)
- fail_task(task_id, error) - attempts++, status='failed' если >= MAX_TASK_ATTEMPTS (из os.getenv)
- save_item(item_data) - UPSERT в parsed_items (ON CONFLICT item_id DO UPDATE)
- get_blocked_items_global() / get_blocked_items_local(group_name)
- get_blocked_sellers()
- add_to_blocklist_global(item_id) / add_to_blocklist_local(item_id, group_name)
- add_to_blocklist_sellers(seller_name)
- get_free_proxy(worker_id) - взятие незабаненного прокси с блокировкой
- ban_proxy(proxy_id, reason) - is_banned=True
- release_proxy(proxy_id) - locked_at=NULL, worker_id=NULL
- cleanup_stale_resources() - освобождение зависших задач (>TASK_LOCK_TIMEOUT_MINUTES) и прокси (>PROXY_LOCK_TIMEOUT_MINUTES) из os.getenv

---

## Группа 3: Docker и Xvfb

**Шаг 3.1** - Создать `container/requirements.txt`
```
git+https://github.com/Stepan2222000/avito-library.git@main#egg=avito-library
playwright
asyncpg
aiogram
```

**Шаг 3.2** - Создать `container/Dockerfile`
- FROM python:3.11-slim
- Установка системных зависимостей (xvfb, fonts)
- COPY requirements.txt и RUN pip install
- RUN playwright install chromium --with-deps
- COPY всех .py файлов в /app
- CMD запуск скрипта start_xvfb.sh + python main.py

**Шаг 3.3** - Создать `container/start_xvfb.sh` скрипт запуска виртуальных дисплеев
- Цикл от 1 до WORKER_COUNT (по умолчанию 15)
- Для каждого: `Xvfb :$i -screen 0 1920x1080x24 &`
- Ожидание запуска всех дисплеев
- Затем exec python /app/main.py

**Шаг 3.4** - Создать `container/docker-compose.yml`
- Один сервис avito_parser
- build: ./container
- volumes: ../scripts/data:/app/data (для доступа к groups.json, proxies.txt)
- environment:
  - DB_HOST=81.30.105.134
  - DB_PORT=5415
  - DB_NAME=avito_new_predlojen
  - DB_USER=admin
  - DB_PASS=Password123
  - WORKER_COUNT=15
  - TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
  - MAX_TASK_ATTEMPTS=5
  - TASK_LOCK_TIMEOUT_MINUTES=10
  - PROXY_LOCK_TIMEOUT_MINUTES=30
  - CAPTCHA_MAX_ATTEMPTS=10
- restart: unless-stopped

---

## Группа 4: Вспомогательные модули

**Шаг 4.1** - Создать `container/url_builder.py`
- Константы CATEGORY_PATHS (dict с путями для каждой категории)
- Константы ENRICH_Q_WORDS (dict со словами для каждой категории)
- Функция build_url(group_dict):
  - Определение region (all_russia ? '/all' : region_slug)
  - Определение category_path из CATEGORY_PATHS
  - Формирование параметра q (brand + model)
  - Применение enrich_q (добавление в КОНЕЦ строки q)
  - Возврат полного URL: https://www.avito.ru/{region}/{category_path}?cd=1&radius=0&searchRadius=0&localPriority=0&s=104[&q=...]

**Шаг 4.2** - Создать `container/telegram_notifier.py`
- Импорт aiogram (Bot), os
- Константа CATEGORY_EMOJI (dict с emoji для категорий)
- Инициализация бота: Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
- Функция send_notification(chat_id, listing, category):
  - Получение emoji из CATEGORY_EMOJI[category]
  - Форматирование сообщения: "{title}\n💰 {price} {currency}\n {location}\n🔗 https://www.avito.ru/{item_id}"
  - await bot.send_message(chat_id, text)
  - Обработка исключений с re-raise для retry логики

---

## Группа 5: Логика воркера

**Шаг 5.1** - Создать `container/worker.py` с основной функцией `async def run_worker(worker_id, db_pool)`
- Инициализация:
  - DISPLAY = f":{worker_id}"
  - Получение свободного прокси из БД
  - Запуск Playwright: playwright.chromium.launch(headless=False, proxy={...})
  - Создание долголивущей page

- Основной цикл:
  - Взятие pending задачи из БД (get_pending_task)
  - Если задач нет: await asyncio.sleep(5), continue

- Обработка задачи:
  1. page.goto(task.url, wait_until="domcontentloaded")
  2. detect_page_state(page, last_response=resp)
  3. Обработка состояний:
     - CAPTCHA/CONTINUE_BUTTON/PROXY_BLOCK_429 → resolve_captcha_flow(max_attempts=CAPTCHA_MAX_ATTEMPTS из os.getenv)
     - PROXY_BLOCK_403/PROXY_AUTH → ban_proxy + смена + перезапуск браузера
     - NOT_DETECTED → retry (до MAX_TASK_ATTEMPTS попыток через fail_task)
     - CATALOG → parse_catalog

  4. parse_catalog(page, task.url, fields=FIELDS, max_pages=1, sort_by_date=True, include_html=False, start_page=1)
     - FIELDS = ["item_id", "title", "price", "currency", "seller_name", "location", "published"]

  5. Обработка статуса parse_catalog:
     - SUCCESS → фильтрация listings
     - CAPTCHA_UNSOLVED/RATE_LIMIT → resolve_captcha_flow + retry
     - PROXY_BLOCKED → ban_proxy + retry
     - EMPTY → complete_task (нет объявлений)
     - NOT_DETECTED → fail_task

  6. Фильтрация listings:
     - Проверка: "сегодня" in published.lower() OR published is None/empty
     - Проверка: seller_name NOT IN blocked_sellers
     - Проверка: item_id NOT IN blocked_items (если blocklist_mode='global' → проверяем global, если 'local' → проверяем local)

  7. Сохранение и отправка:
     - Для каждого listing:
       - save_item() в БД
       - try: send_notification()
       - except: fail_task() + return (задача неуспешна)
       - Telegram успешно → add_to_blocklist_global() + add_to_blocklist_local() ВСЕГДА (независимо от режима)

  8. Завершение:
     - complete_task()
     - await asyncio.sleep(1)
     - reset_completed_to_pending()

**Шаг 5.2** - Добавить вспомогательные функции в worker.py
- restart_browser(worker_id, new_proxy) - полный перезапуск браузера с новым прокси
- handle_proxy_error(error_type) - логика бана/смены прокси
- Exception handling с логированием всех критических событий

---

## Группа 6: Оркестрация воркеров

**Шаг 6.1** - Создать `container/main.py`
- Функция async def main():
  - Чтение WORKER_COUNT из os.getenv("WORKER_COUNT", "15")
  - Создание asyncpg pool: db_pool = await db.create_pool()
  - Запуск фоновой задачи cleanup: asyncio.create_task(periodic_cleanup(db_pool))
  - Запуск воркеров: tasks = [run_worker(i+1, db_pool) for i in range(WORKER_COUNT)]
  - await asyncio.gather(*tasks)

- Функция async def periodic_cleanup(db_pool):
  - Бесконечный цикл:
    - await asyncio.sleep(60)
    - await db.cleanup_stale_resources()
    - Логирование количества освобожденных ресурсов

- Signal handlers:
  - SIGTERM, SIGINT → graceful shutdown (закрытие pool, остановка воркеров)

- if __name__ == "__main__": asyncio.run(main())

**Шаг 6.2** - Добавить настройку логирования
- logging.basicConfig(level=INFO, format='[%(asctime)s] [Worker-%(worker_id)s] %(message)s', stream=sys.stdout)
- Логировать: взятие задачи, новые объявления, бан прокси, капча, ошибки Telegram, ошибки parse_catalog, освобождение ресурсов

---

## Группа 7: Скрипты управления

**Все скрипты:**
- Подключение к БД через asyncpg, читают DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS из os.getenv
- Запуск: `DB_HOST=... DB_PORT=... python scripts/script_name.py`

**Шаг 7.1** - Создать `scripts/load_proxies.py`
- Импорт asyncpg, os
- Чтение scripts/data/proxies.txt (построчно, формат host:port:user:pass)
- Парсинг каждой строки: server, username, password
- Интерактивный выбор: "Перезаписать все прокси?" (y/n)
- Если yes: DELETE FROM proxies, затем INSERT всех с is_banned=False
- Если no: INSERT только новых (ON CONFLICT DO NOTHING)

**Шаг 7.2** - Создать `scripts/load_groups.py`
- Импорт asyncpg, json, os
- Импорт sys.path для доступа к container/url_builder.py
- Чтение scripts/data/groups.json
- Интерактивный выбор: "Перезаписать группы?" (y/n)
- Если yes:
  - Для каждой группы: DELETE FROM tasks WHERE group_name=...
  - DELETE FROM groups
  - INSERT всех групп
- Если no: INSERT новых групп (ON CONFLICT name DO UPDATE)
- Автогенерация задач:
  - Для каждой группы: генерация URLs через url_builder.build_url (brands/models комбинации)
  - INSERT задач в таблицу tasks (group_name, url, status='pending')

**Шаг 7.3** - Создать `scripts/manage_blocklist.py`
- Чтение scripts/data/sellers_blocklist.txt (по одному seller_name на строку)
- Интерактивный выбор: "Перезаписать блоклист продавцов?" (y/n)
- Если yes: DELETE FROM blocklist_sellers, затем INSERT всех
- Если no: INSERT только новых (ON CONFLICT DO NOTHING)

**Шаг 7.4** - Создать `scripts/delete_group.py`
- Интерактивный ввод: "Введите имя группы для удаления:"
- Подтверждение: "Удалить группу {name} и все её задачи?" (y/n)
- DELETE FROM groups WHERE name=... (CASCADE автоматически удалит задачи)

---

## Группа 8: Проверка и запуск

**Шаг 8.1** - Инициализация базы данных
- Подключиться к PostgreSQL (81.30.105.134:5415)
- Выполнить container/init_db.sql вручную (psql или pgAdmin)
- Проверить создание всех 7 таблиц и индексов

**Шаг 8.2** - Загрузка начальных данных
- Подготовить scripts/data/proxies.txt с реальными прокси (уже есть)
- Подготовить scripts/data/groups.json с группами для парсинга
- Запустить scripts/load_proxies.py (выбрать "перезаписать")
- Запустить scripts/load_groups.py (выбрать "перезаписать")
- Проверить в БД: таблица proxies заполнена, groups заполнена, tasks автосгенерированы

**Шаг 8.3** - Сборка и запуск Docker контейнера
- cd container/
- docker-compose build
- docker-compose up -d
- docker-compose logs -f (мониторинг запуска Xvfb и воркеров)

**Шаг 8.4** - Мониторинг работы
- Проверить логи: взятие задач, переходы на страницы, парсинг
- Проверить БД: статусы задач меняются (pending → in_progress → completed → pending)
- Проверить Telegram: приходят уведомления о новых объявлениях
- Проверить блоклисты: item_id добавляются после успешной отправки

---

## Ключевые технические детали

### Архитектура
- **15 asyncio воркеров в одном процессе Python** (asyncio.gather)
- **15 Xvfb дисплеев** (DISPLAY :1 до :15), запускаются скриптом start_xvfb.sh
- **Браузер**: chromium, headless=False
- **Одна долголивущая page на воркера** (переиспользуется между задачами)

### База данных
- **Подключение**: asyncpg pool (НЕ ORM)
- **Все настройки**: указываются в docker-compose.yml через environment (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS, MAX_TASK_ATTEMPTS, TASK_LOCK_TIMEOUT_MINUTES, PROXY_LOCK_TIMEOUT_MINUTES, CAPTCHA_MAX_ATTEMPTS)
- **Таблиц**: 7 (groups, tasks, parsed_items, 3 blocklist таблицы, proxies)

### Парсинг
- **ТОЛЬКО parse_catalog**, БЕЗ parse_card, БЕЗ переходов в карточки
- **FIELDS**: ["item_id", "title", "price", "currency", "seller_name", "location", "published"]
- **max_pages=1**, sort_by_date=True, include_html=False

### Жизненный цикл задач
- **Циркулярный**: pending → in_progress → completed → (1 сек) → pending
- **Зависшие ресурсы**: освобождаются фоновой задачей (задачи >10 мин, прокси >30 мин)

### Блоклисты
- **Добавление**: ТОЛЬКО после успешной отправки в Telegram → ВСЕГДА в обе таблицы (global И local)
- **Проверка**: зависит от blocklist_mode (global → только global, local → только local)
- **Если Telegram упал**: item_id НЕ добавляется (гарантия повторной отправки при retry)
- **Sellers**: всегда global
- **Items**: две таблицы (blocklist_items_global и blocklist_items_local)

### Telegram
- **Библиотека**: aiogram (async)
- **Token**: из docker-compose.yml (environment TELEGRAM_BOT_TOKEN)
- **Отправка**: по telegram_chat_id из группы (каждая группа → свой чат)

### Retry политика
- **Max attempts**: 5 (через поле tasks.attempts)
- **Captcha не решилась**: смена прокси + retry
- **403/407 прокси**: бан навсегда + смена + retry
- **429**: НЕ банится, смена + retry
- **Telegram failed**: retry до 5 попыток
- **NOT_DETECTED (5 попыток)**: status='failed'

---

**Конец плана реализации**
