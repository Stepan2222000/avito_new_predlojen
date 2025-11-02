# Avito Parser Dockerfile
# Образ с Playwright chromium и Xvfb для headless=False режима

FROM python:3.11-slim

# Установка рабочей директории
WORKDIR /app

# Установка системных зависимостей
# xvfb - виртуальные X дисплеи для каждого воркера
# procps - утилиты (pkill для cleanup в start_xvfb.sh)
RUN apt-get update && apt-get install -y \
    xvfb \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements.txt отдельно для кеширования слоя Docker
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Установка Playwright chromium с системными зависимостями
# --with-deps автоматически установит все необходимые библиотеки (fonts, webkit, gtk и т.д.)
RUN playwright install chromium --with-deps

# Копируем startup скрипт и делаем его исполняемым
COPY start_xvfb.sh /app/start_xvfb.sh
RUN chmod +x /app/start_xvfb.sh

# Копируем все Python модули в /app
COPY *.py /app/

# Запуск через start_xvfb.sh (запускает Xvfb дисплеи, затем main.py)
CMD ["/app/start_xvfb.sh"]
