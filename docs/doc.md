# ТЗ: Avito-парсер (итоговая спецификация)

Всё взаимодействие с Avito — **только через Playwright**. Одна долголивущая `page` на воркера. **Парсинг ТОЛЬКО через parse_catalog БЕЗ перехода в карточки.**

---

## 1) Входные данные (groups.json)

Один файл `scripts/data/groups.json` содержит массив групп со строго машинными полями:

```json
{
  "groups": [
    {
      "name": "auto_msk",
      "enabled": true,
      "category": "avtomobili",
      "region_slug": "moskva",
      "brands": ["audi", "bmw"],
      "models": {"audi": ["a6", "q7"], "bmw": ["x5"]},
      "all_russia": false,
      "enrich_q": false,
      "blocklist_mode": "global",
      "telegram_chat_id": 123456789
    }
  ]
}
```

**Пояснения:**
- `category` ∈ {`avtomobili`, `mototsikly`, `snegohody`, `kvadrotsikly`, `gidrotsikly`, `katera_i_yahty`}
- `region_slug` — готовый slug (например, `moskva`, `nizhegorodskaya_oblast` или `all`)
- `brands` — массив строк в нижнем регистре
- `models` — словарь `{brand: [model1, model2]}` (пустой список = только бренд без моделей)
- `all_russia` — если `true`, используется `/all` вместо `region_slug`
- `enrich_q` — добавлять родовое слово к поисковому запросу
- `blocklist_mode` — `global` или `local` (для объявлений; продавцы всегда глобально)

**Генерация задач:**
- `brands=[audi, bmw]`, `models={audi: [a6, q7], bmw: [x5]}` → 5 задач: `q=audi`, `q=audi a6`, `q=audi a6`, `q=bmw`, `q=bmw x5`
- `brands=[]`, `models={}` → 1 задача без параметра `q` (вся категория)
- `models={audi: []}` → создаём задачу `q=audi` (пустой список ≠ пропуск бренда)

---

## 2) URL-конструктор

**Пути категорий:**
- `avtomobili` → `/avtomobili`
- `mototsikly` → `/mototsikly_i_mototehnika/mototsikly`
- `kvadrotsikly` → `/mototsikly_i_mototehnika/kvadrotsikly`
- `snegohody` → `/mototsikly_i_mototehnika/snegohody`
- `katera_i_yahty` → `/vodnyy_transport/katera_i_yahty`
- `gidrotsikly` → `/vodnyy_transport/gidrotsikly`

**Параметр q:**
- Бренд + модель → `q="brand model"`
- Только бренд → `q=brand`
- Только модель → `q=model`
- Ничего → параметр `q` отсутствует

**enrich_q (добавляется в КОНЕЦ строки q):**
- `mototsikly` → "мотоцикл"
- `snegohody` → "снегоход"
- `gidrotsikly` → "гидроцикл"
- `katera_i_yahty` → "катер"
- `kvadrotsikly` → "квадроцикл"
- `avtomobili` → не добавляется

Пример: `q=lynx` + `enrich_q=true` + `category=snegohody` → `q=lynx снегоход`

**Итоговый URL:**
```
https://www.avito.ru/{region_slug}/{category_path}?cd=1&radius=0&searchRadius=0&localPriority=0&s=104[&q=...]
```

---

## 3) База данных PostgreSQL

**Подключение:**
- Host: `81.30.105.134`
- Port: `5415`
- Database: `avito_new_predlojen`
- Username: `admin`
- Password: `Password123`

**Схемы таблиц:**

```sql
-- Группы (зеркало groups.json)
CREATE TABLE groups (
    name VARCHAR(255) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    category VARCHAR(50) NOT NULL,
    region_slug VARCHAR(100),
    brands JSONB NOT NULL DEFAULT '[]'::jsonb,
    models JSONB NOT NULL DEFAULT '{}'::jsonb,
    all_russia BOOLEAN NOT NULL DEFAULT FALSE,
    enrich_q BOOLEAN NOT NULL DEFAULT FALSE,
    blocklist_mode VARCHAR(10) NOT NULL CHECK (blocklist_mode IN ('global', 'local')),
    telegram_chat_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Задачи (URL для парсинга)
CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    group_name VARCHAR(255) NOT NULL REFERENCES groups(name) ON DELETE CASCADE,
    url TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    locked_at TIMESTAMP,
    worker_id INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Результаты парсинга
CREATE TABLE parsed_items (
    id SERIAL PRIMARY KEY,
    item_id VARCHAR(50) UNIQUE NOT NULL,
    group_name VARCHAR(255) NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    price DECIMAL(15, 2),
    currency VARCHAR(10),
    seller_name VARCHAR(255),
    location VARCHAR(255),
    published_date VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Блоклисты
CREATE TABLE blocklist_items_global (item_id VARCHAR(50) PRIMARY KEY, created_at TIMESTAMP NOT NULL DEFAULT NOW());
CREATE TABLE blocklist_items_local (item_id VARCHAR(50) NOT NULL, group_name VARCHAR(255) NOT NULL REFERENCES groups(name) ON DELETE CASCADE, created_at TIMESTAMP NOT NULL DEFAULT NOW(), PRIMARY KEY (item_id, group_name));
CREATE TABLE blocklist_sellers (seller_name VARCHAR(255) PRIMARY KEY, created_at TIMESTAMP NOT NULL DEFAULT NOW());

-- Прокси
CREATE TABLE proxies (
    id SERIAL PRIMARY KEY,
    server VARCHAR(255) NOT NULL,
    username VARCHAR(255),
    password VARCHAR(255),
    is_banned BOOLEAN NOT NULL DEFAULT FALSE,
    ban_reason TEXT,
    worker_id INTEGER,
    locked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(server, username)
);

-- Индексы (основные)
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_locked_at ON tasks(locked_at);
CREATE INDEX idx_proxies_is_banned ON proxies(is_banned);
CREATE INDEX idx_proxies_locked_at ON proxies(locked_at);
```

---

## 4) Docker и Xvfb

**Контейнер:**
- Один сервис, 15 asyncio воркеров в одном процессе Python (по умолчанию)
- Каждый воркер = 1 браузер Playwright (chromium) + 1 долголивущая page
- headless=False → требуется Xvfb (виртуальный X-сервер)

**Xvfb конфигурация:**
- 15 воркеров = 15 процессов Xvfb
- Worker #1 → `DISPLAY=:1`, Worker #2 → `DISPLAY=:2`, ..., Worker #15 → `DISPLAY=:15`
- Разрешение: 1920x1080x24
- Запуск через скрипт start_xvfb.sh

**container/start_xvfb.sh:**
```bash
#!/bin/bash
WORKER_COUNT=${WORKER_COUNT:-15}

for i in $(seq 1 $WORKER_COUNT); do
  Xvfb :$i -screen 0 1920x1080x24 &
done

sleep 2
exec python /app/main.py
```

---

## 5) Логика воркера (БЕЗ перехода в карточки)

**ВАЖНО:** Весь парсинг через `parse_catalog` на 1-й странице. **НЕТ** вызовов `parse_card`, **НЕТ** переходов в карточки через `page.goto`.

**Последовательность:**

1. **Взятие задачи из БД:**
   ```sql
   UPDATE tasks SET status='in_progress', locked_at=NOW(), worker_id=$1
   WHERE id = (SELECT id FROM tasks WHERE status='pending' AND group_name IN (SELECT name FROM groups WHERE enabled=TRUE) ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *;
   ```

2. **Переход на каталог:**
   ```python
   resp = await page.goto(task.url, wait_until="domcontentloaded")
   state = await detect_page_state(page, last_response=resp)
   ```

3. **Обработка состояния:**
   - `CAPTCHA_DETECTOR_ID` / `PROXY_BLOCK_429_DETECTOR_ID` / `CONTINUE_BUTTON_DETECTOR_ID` → `resolve_captcha_flow(page, max_attempts=10)` → повторный детект
   - `PROXY_BLOCK_403_DETECTOR_ID` / `PROXY_AUTH_DETECTOR_ID` → бан прокси, смена, перезапуск браузера, retry
   - `NOT_DETECTED_STATE_ID` → если 5 попыток не помогли → `status='failed'`
   - `CATALOG_DETECTOR_ID` → переход к парсингу

4. **Парсинг каталога:**
   ```python
   FIELDS = ["item_id", "title", "price", "currency", "seller_name", "location", "published"]
   listings, meta = await parse_catalog(page, task.url, fields=FIELDS, max_pages=1, sort_by_date=True, include_html=False, start_page=1)
   ```

5. **Обработка статуса:**
   - `SUCCESS` → фильтрация listings
   - `CAPTCHA_UNSOLVED` / `RATE_LIMIT` → `resolve_captcha_flow` и повтор
   - `PROXY_BLOCKED` → бан прокси, перезапуск
   - `EMPTY` → завершение без результатов
   - `NOT_DETECTED` → retry по политике

6. **Фильтрация listings:**
   - Проверка: `"сегодня" in listing.published.lower()` ИЛИ `listing.published is None/empty`
   - Проверка: `listing.seller_name NOT IN blocklist_sellers`
   - Проверка: `listing.item_id NOT IN blocklist_items` (глобальный если blocklist_mode='global', локальный если blocklist_mode='local')

7. **Сохранение:**
   ```python
   for listing in filtered_listings:
       item_url = f"https://www.avito.ru/{listing.item_id}"
       await save_item({...})  # UPSERT в parsed_items

       try:
           await send_to_telegram(task.group.telegram_chat_id, listing, task.group.category)
       except Exception as e:
           await fail_task(task.id, f"Telegram failed: {e}")
           return  # Задача неуспешна

       # Telegram успешно → добавляем в блоклист
       await add_to_blocklist_global(listing.item_id)  # ВСЕГДА в global
       await add_to_blocklist_local(listing.item_id, task.group_name)  # ВСЕГДА в local
   ```

8. **Завершение:**
   ```python
   await complete_task(task.id)  # status='completed' → через 1 сек → status='pending'
   ```

---

## 6) Жизненный цикл задач

**Статусы:** `pending`, `in_progress`, `completed`, `failed`

**Циклическая обработка:**
- Создание: `status='pending'`
- Взятие воркером: `status='in_progress'`, `locked_at=NOW()`, `worker_id=X`
- Успех: `status='completed'` → через 1 сек → `status='pending'` (новый круг)
- Ошибка: `attempts++`, если `<5` → `status='pending'` (retry), иначе `status='failed'`

**Зависшие ресурсы:**
- Задачи с `locked_at > 10 минут` → освобождаются фоновой функцией
- Прокси с `locked_at > 30 минут` → освобождаются
- При падении воркера: `release_worker_resources(worker_id)` освобождает все его ресурсы

---

## 7) Блоклисты

**Объявления:**
- Режим на уровне группы: `global` (общий) или `local` (только для группы)
- Добавление: **ПОСЛЕ** успешной отправки в Telegram → **ВСЕГДА** в blocklist_items_global И blocklist_items_local
- Проверка: зависит от blocklist_mode (global → проверяем только global, local → проверяем только local)
- Если Telegram упал → item_id **НЕ** добавляется (гарантия повторной отправки)

**Продавцы:**
- **Всегда глобальный** блоклист
- Проверка: строгое совпадение имени (без нормализации)

**Логика проверки:**
```python
if task.group.blocklist_mode == 'global':
    blocked_items = await get_blocked_items_global()
else:  # local
    blocked_items = await get_blocked_items_local(task.group_name)
```

---

## 8) Telegram уведомления

**Библиотека:** aiogram (асинхронная)

**Отправка:**
- Используется `telegram_chat_id` из группы (каждая группа = свой чат)
- Бот отправляет сообщения соответствующим пользователям по их Telegram ID

**Формат сообщения:**
```
{emoji} {title}
💰 {price} {currency}
📍 {location}
🔗 https://www.avito.ru/{item_id}
```

**Emoji по категориям:**
- `avtomobili` → 🚗
- `mototsikly` → 🏍
- `snegohody` → 🛷
- `kvadrotsikly` → 🏍
- `gidrotsikly` → 🛥
- `katera_i_yahty` → ⛵

**Обработка ошибок:**
- Если `send_message()` упал → задача неуспешна, `attempts++`
- Объявление не добавляется в блоклист до успешной отправки

---

## 9) Прокси

**Загрузка:**
- Локальный скрипт `scripts/load_proxies.py` читает `scripts/data/proxies.txt`
- Формат файла: `host:port:user:pass` (каждый с новой строки)
- Интерактивный выбор: перезаписать (сброс `is_banned`) или добавить

**Политика:**
- 403/407 → бан навсегда (`is_banned=True`)
- 429 → не банится, смена прокси + retry
- Капча не решилась после 10 попыток → смена прокси
- Если свободных прокси нет → воркеры ждут, логируют

---

## 10) Скрипты управления (scripts/)

**Подключение к БД в скриптах:**
- Все скрипты читают настройки БД через `os.getenv()`: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
- Можно запускать с переменными окружения: `DB_HOST=... DB_PORT=... python scripts/load_proxies.py`

**1. load_proxies.py:**
- Чтение `scripts/data/proxies.txt`
- Выбор: перезаписать/добавить
- При перезаписи: сброс `is_banned=False`

**2. load_groups.py:**
- Чтение `scripts/data/groups.json`
- Выбор: перезаписать/добавить
- Автогенерация задач (URL) в таблицу `tasks` через url_builder
- При перезаписи: `DELETE FROM tasks WHERE group_name=...` (удаление старых задач)

**3. manage_blocklist.py:**
- Чтение `scripts/data/sellers_blocklist.txt`
- Выбор: перезаписать/добавить
- `INSERT INTO blocklist_sellers`

**4. delete_group.py:**
- Удаление группы и всех её задач
- `DELETE FROM groups WHERE name=...` (CASCADE удаляет связанные записи)

---

## 11) Структура проекта

```
avito_new_predlojen/
├── scripts/
│   ├── data/
│   │   ├── proxies.txt
│   │   ├── groups.json
│   │   └── sellers_blocklist.txt
│   ├── load_proxies.py
│   ├── load_groups.py
│   ├── manage_blocklist.py
│   └── delete_group.py
├── container/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── start_xvfb.sh
│   ├── requirements.txt
│   ├── main.py
│   ├── db.py
│   ├── worker.py
│   ├── url_builder.py
│   ├── telegram_notifier.py
│   └── init_db.sql
├── docs/
│   ├── doc.md
│   └── implementation_plan.md
├── CLAUDE.md
└── README.md
```

**container/requirements.txt:**
```
git+https://github.com/Stepan2222000/avito-library.git@main#egg=avito-library
playwright
asyncpg
aiogram
```

**container/docker-compose.yml (все настройки в environment):**
```yaml
version: '3.8'

services:
  avito_parser:
    build: .
    container_name: avito_parser
    volumes:
      - ../scripts/data:/app/data
    environment:
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
    restart: unless-stopped
```

---

## 12) Импорты из avito-library

```python
from avito_library.detectors import (
    detect_page_state,
    CAPTCHA_DETECTOR_ID, CONTINUE_BUTTON_DETECTOR_ID, CATALOG_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID, PROXY_BLOCK_429_DETECTOR_ID, PROXY_AUTH_DETECTOR_ID,
    NOT_DETECTED_STATE_ID
)
from avito_library.capcha import resolve_captcha_flow
from avito_library.parsers.catalog_parser import parse_catalog, CatalogParseStatus
```

**FIELDS для parse_catalog:**
```python
["item_id", "title", "price", "currency", "seller_name", "location", "published"]
```

**Важно:** используем `seller_name` (не `seller`), т.к. `seller` возвращает сложную структуру.

---

## 13) Финальные инварианты

**Обязательные правила:**

1. **Парсинг только через parse_catalog:**
   - НЕТ вызовов `parse_card`
   - НЕТ переходов в карточки
   - Вся информация из каталога

2. **Одна долголивущая page на воркера:**
   - Переиспользование между задачами
   - Перезапуск только при фатальных ошибках

3. **Детектор после каждого goto:**
   - Действия строго по результату детектора

4. **Блоклист после успешного Telegram:**
   - Если Telegram упал → НЕ добавляем в блоклист

5. **Зависшие ресурсы освобождаются:**
   - Задачи: 10 минут
   - Прокси: 30 минут
   - При падении воркера: cleanup

6. **Задачи выполняются по кругу:**
   - completed → pending (бесконечно)

7. **headless=False + Xvfb:**
   - 15 DISPLAY для 15 воркеров

**Политика ретраев:**
- Капча не решилась: смена прокси, retry
- 403/407: бан прокси, retry
- NOT_DETECTED (5 попыток): failed
- Telegram упал: retry до 5 попыток
- Общее: макс 5 попыток → failed

**Логирование:**
- Взятие задачи, бан прокси, капча, новое объявление, ошибки Telegram, ошибки parse_catalog, освобождение ресурсов, падение воркера

---

**Конец документации.**
