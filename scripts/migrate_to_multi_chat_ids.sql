-- Миграция: поддержка нескольких telegram_chat_id для одной группы
-- Добавляет колонку telegram_chat_ids BIGINT[] вместо telegram_chat_id BIGINT

-- 1. Добавить новую колонку telegram_chat_ids (массив)
ALTER TABLE groups ADD COLUMN IF NOT EXISTS telegram_chat_ids BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[];

-- 2. Перенести существующие данные из telegram_chat_id в telegram_chat_ids
UPDATE groups SET telegram_chat_ids = ARRAY[telegram_chat_id] WHERE telegram_chat_id IS NOT NULL;

-- 3. Удалить старую колонку telegram_chat_id
ALTER TABLE groups DROP COLUMN IF EXISTS telegram_chat_id;
