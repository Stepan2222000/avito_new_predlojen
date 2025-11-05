#!/bin/bash

# Скрипт запуска виртуальных дисплеев Xvfb для каждого воркера
# Запускается через supervisord, main.py запускается отдельно

# Читаем количество воркеров из переменной окружения (по умолчанию 10)
WORKER_COUNT=${WORKER_COUNT:-10}

# Останавливаем все существующие процессы Xvfb (cleanup перед запуском)
echo "Cleaning up existing Xvfb processes..."
pkill -9 Xvfb 2>/dev/null || true

# Очищаем lock файлы и сокеты дисплеев
echo "Cleaning up X11 artifacts..."
rm -rf /tmp/.X*-lock 2>/dev/null || true
rm -rf /tmp/.X11-unix/X* 2>/dev/null || true

# Создаем директорию для сокетов с правильными правами
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

echo "Starting $WORKER_COUNT Xvfb displays..."

# Запускаем виртуальный дисплей для каждого воркера последовательно
for i in $(seq 1 $WORKER_COUNT); do
    echo "Starting Xvfb :$i"
    Xvfb :$i -screen 0 1920x1080x24 &
    xvfb_pid=$!

    # Ждем создания сокета (макс 10 секунд)
    timeout=20
    while [ ! -S /tmp/.X11-unix/X$i ] && [ $timeout -gt 0 ]; do
        sleep 0.5
        timeout=$((timeout - 1))
    done

    if [ -S /tmp/.X11-unix/X$i ]; then
        echo "Display :$i ready (PID: $xvfb_pid)"
    else
        echo "FATAL: Display :$i failed to start"
        exit 1
    fi
done

echo "All $WORKER_COUNT Xvfb displays started successfully"

# Держим скрипт запущенным (supervisord будет мониторить)
# Если этот процесс завершится, supervisord перезапустит его
wait
