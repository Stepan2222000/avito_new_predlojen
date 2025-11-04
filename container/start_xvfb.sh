#!/bin/bash

# Скрипт запуска виртуальных дисплеев Xvfb для каждого воркера
# Каждый воркер получает свой дисплей :1, :2, ..., :15

# Читаем количество воркеров из переменной окружения (по умолчанию 15)
WORKER_COUNT=${WORKER_COUNT:-15}

# Останавливаем все существующие процессы Xvfb (cleanup перед запуском)
echo "Cleaning up existing Xvfb processes..."
pkill -9 Xvfb 2>/dev/null || true

# Очищаем lock файлы дисплеев (если остались после предыдущих запусков)
rm -f /tmp/.X*-lock 2>/dev/null || true

echo "Starting $WORKER_COUNT Xvfb displays..."

# Запускаем виртуальный дисплей для каждого воркера
for i in $(seq 1 $WORKER_COUNT); do
    echo "Starting Xvfb :$i"
    Xvfb :$i -screen 0 1920x1080x24 &
done

# Ожидаем инициализации всех дисплеев
echo "Waiting for displays to initialize..."
sleep 5

# Проверяем что все сокеты созданы
for i in $(seq 1 $WORKER_COUNT); do
    timeout=10
    while [ ! -S /tmp/.X11-unix/X$i ] && [ $timeout -gt 0 ]; do
        echo "Waiting for X$i socket..."
        sleep 1
        timeout=$((timeout - 1))
    done
    if [ -S /tmp/.X11-unix/X$i ]; then
        echo "Display :$i ready"
    else
        echo "WARNING: Display :$i failed to start"
    fi
done

echo "All Xvfb displays started. Launching main application..."

# Запускаем основное приложение (exec для правильной обработки SIGTERM)
exec python /app/main.py
