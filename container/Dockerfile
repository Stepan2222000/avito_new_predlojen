# Avito Parser Dockerfile
# Образ с Playwright chromium и Xvfb для headless=False режима

FROM python:3.11-slim

# Установка рабочей директории
WORKDIR /app

# Установка системных зависимостей
# git - для установки avito-library из GitHub
# xvfb - виртуальные X дисплеи для каждого воркера
# procps - утилиты (pkill для cleanup в start_xvfb.sh)
# tini - init процесс для reaping zombie processes
# supervisor - process supervision для Xvfb и main.py
RUN apt-get update && apt-get install -y \
    git \
    xvfb \
    procps \
    tini \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements.txt отдельно для кеширования слоя Docker
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Установка Playwright chromium с системными зависимостями
# --with-deps автоматически установит все необходимые библиотеки (fonts, webkit, gtk и т.д.)
RUN playwright install chromium --with-deps

# Копируем startup скрипты и делаем их исполняемыми
COPY start_xvfb.sh /app/start_xvfb.sh
COPY start_xvfb_only.sh /app/start_xvfb_only.sh
RUN chmod +x /app/start_xvfb.sh /app/start_xvfb_only.sh

# Копируем конфигурацию supervisord
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Копируем все Python модули в /app
COPY *.py /app/

# Используем tini как PID 1 для reaping zombie processes
ENTRYPOINT ["/usr/bin/tini", "--"]

# Запуск supervisord для управления Xvfb и main.py
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
