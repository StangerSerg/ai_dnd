#!/bin/sh
# Точка входа образа бэкенда.
# Без аргументов — сервис API: применить миграции и запустить uvicorn.
# С аргументами (воркер в docker-compose) — выполнить их без миграций:
# миграции уже применил сервис backend, воркер ждёт его healthy.
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "Применяю миграции базы данных (alembic upgrade head)..."
alembic upgrade head

echo "Запускаю API (uvicorn ai_dnd.main:app)..."
exec uvicorn ai_dnd.main:app --host 0.0.0.0 --port 8000