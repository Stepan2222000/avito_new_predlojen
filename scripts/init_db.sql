-- ======================================
-- Avito Parser Database Schema
-- ======================================
-- 7 таблиц для хранения групп, задач, объявлений, блоклистов и прокси
-- Принцип: KISS - минимальная сложность, максимальная эффективность

-- ======================================
-- 1. Таблица групп парсинга
-- ======================================
-- Хранит конфигурацию каждой группы мониторинга
CREATE TABLE groups (
    name TEXT PRIMARY KEY,                  -- уникальное имя группы
    enabled BOOLEAN NOT NULL,               -- активна ли группа
    category TEXT NOT NULL,                 -- категория Avito (avtomobili, mototsikly, etc)
    region_slug TEXT NOT NULL,              -- регион (moskva, spb, all)
    brands TEXT[] NOT NULL,                 -- массив брендов для мониторинга
    models JSONB NOT NULL,                  -- модели в формате {brand: [model1, model2]}
    all_russia BOOLEAN NOT NULL,            -- поиск по всей России
    enrich_q BOOLEAN NOT NULL,              -- обогащать поисковый запрос родовым словом
    blocklist_mode TEXT NOT NULL,           -- режим блоклиста: 'global' или 'local'
    telegram_chat_ids BIGINT[] NOT NULL,    -- массив ID чатов для уведомлений
    min_price BIGINT,                       -- минимальная цена объявления (рубли, NULL = без ограничения)
    max_price BIGINT                        -- максимальная цена объявления (рубли, NULL = без ограничения)
);

-- ======================================
-- 2. Таблица задач
-- ======================================
-- Циклические задачи парсинга с атомарным захватом через FOR UPDATE SKIP LOCKED
CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    group_name TEXT NOT NULL REFERENCES groups(name) ON DELETE CASCADE,  -- связь с группой
    url TEXT NOT NULL,                                 -- URL для парсинга
    search_query TEXT,                                 -- поисковый запрос (может быть NULL)
    status TEXT NOT NULL DEFAULT 'pending',            -- pending | in_progress | completed | failed
    attempts INTEGER NOT NULL DEFAULT 0,               -- счетчик попыток (max 5)
    successful_parses INTEGER NOT NULL DEFAULT 0,      -- счетчик успешных парсингов
    locked_at TIMESTAMP,                               -- время захвата воркером
    locked_by INTEGER,                                 -- ID воркера (1-15)
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (group_name, url)                           -- предотвращает дубликаты задач
);

-- ======================================
-- 3. Таблица распарсенных объявлений
-- ======================================
-- Хранит результаты парсинга для истории
CREATE TABLE parsed_items (
    item_id TEXT PRIMARY KEY,                          -- уникальный ID объявления
    group_name TEXT NOT NULL REFERENCES groups(name) ON DELETE CASCADE,  -- какая группа нашла
    title TEXT NOT NULL,                               -- заголовок объявления
    price TEXT,                                        -- цена (может быть NULL)
    currency TEXT,                                     -- валюта
    seller_name TEXT,                                  -- имя продавца
    location TEXT,                                     -- местоположение
    published TEXT,                                    -- дата публикации
    url TEXT NOT NULL,                                 -- полный URL объявления
    parsed_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ======================================
-- 4. Глобальный блоклист объявлений
-- ======================================
-- Все объявления, которые были отправлены хотя бы один раз
-- Добавление: ВСЕГДА после успешной отправки в Telegram
-- Проверка: ТОЛЬКО если blocklist_mode='global'
CREATE TABLE blocklist_items_global (
    item_id TEXT PRIMARY KEY,
    added_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ======================================
-- 5. Локальный блоклист объявлений
-- ======================================
-- Объявления для каждой группы отдельно
-- Добавление: ВСЕГДА после успешной отправки в Telegram
-- Проверка: ТОЛЬКО если blocklist_mode='local'
CREATE TABLE blocklist_items_local (
    item_id TEXT NOT NULL,
    group_name TEXT NOT NULL REFERENCES groups(name) ON DELETE CASCADE,
    added_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (item_id, group_name)
);

-- ======================================
-- 6. Блоклист продавцов
-- ======================================
-- Глобальный блоклист нежелательных продавцов
CREATE TABLE blocklist_sellers (
    seller_name TEXT PRIMARY KEY,
    added_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ======================================
-- 7. Таблица прокси
-- ======================================
-- Пул прокси с атомарным захватом и системой банов
CREATE TABLE proxies (
    id SERIAL PRIMARY KEY,
    proxy_url TEXT NOT NULL UNIQUE,         -- http://user:pass@host:port
    is_banned BOOLEAN NOT NULL DEFAULT FALSE,  -- забанен навсегда (403/407)
    locked_at TIMESTAMP,                    -- время захвата воркером
    locked_by INTEGER,                      -- ID воркера (1-15)
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ======================================
-- Индексы для оптимизации запросов
-- ======================================
-- Ускорение поиска pending задач
CREATE INDEX idx_tasks_status ON tasks(status);

-- Очистка зависших задач (locked_at > 10 минут)
CREATE INDEX idx_tasks_locked_at ON tasks(locked_at);

-- Поиск свободных прокси (is_banned=FALSE)
CREATE INDEX idx_proxies_is_banned ON proxies(is_banned);

-- Очистка зависших прокси (locked_at > 30 минут)
CREATE INDEX idx_proxies_locked_at ON proxies(locked_at);

-- Ускорение запросов локального блоклиста по группе
CREATE INDEX idx_blocklist_items_local_group ON blocklist_items_local(group_name);

-- Composite index для queue priority (ORDER BY successful_parses ASC, created_at ASC)
CREATE INDEX idx_tasks_queue_priority ON tasks(status, successful_parses ASC, created_at ASC) WHERE status = 'pending';

-- Ускорение запросов по group_name в parsed_items
CREATE INDEX idx_parsed_items_group_name ON parsed_items(group_name);

-- Ускорение cleanup по locked_by в tasks и proxies
CREATE INDEX idx_tasks_locked_by ON tasks(locked_by) WHERE locked_by IS NOT NULL;
CREATE INDEX idx_proxies_locked_by ON proxies(locked_by) WHERE locked_by IS NOT NULL;
