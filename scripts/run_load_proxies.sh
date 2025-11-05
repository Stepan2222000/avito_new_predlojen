#!/bin/bash
# Скрипт загрузки прокси в БД

cd "$(dirname "$0")"
source ../.venv/bin/activate

echo "y" | python3 load_proxies.py
