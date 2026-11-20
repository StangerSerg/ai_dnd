# Образ бэкенда AI DnD (API + воркер — один образ, разные команды).
# Контекст сборки — КОРЕНЬ репозитория (нужна папка content/):
#   docker compose build backend
# или вручную:
#   docker build -f backend/Dockerfile -t ai-dnd-backend .
#
# Multi-stage: .venv собирается в builder от root, а в финальный образ попадает
# одним слоем COPY --chown=app:app. Без этого `chown -R app:app /app` после
# установки зависимостей дублировал весь .venv в новый слой (+6 GB к образу).

# --- Стадия сборки: зависимости и проект в /app/backend/.venv ---
FROM python:3.12-slim AS builder

# uv ставим через pip (ghcr.io/astral-sh/uv в этой сети отдаёт denied).
RUN pip install --no-cache-dir uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Раскладка повторяет репозиторий: /app/backend и /app/content,
# чтобы дефолтный content_dir (BACKEND_DIR.parent / "content") работал без настройки.
WORKDIR /app/backend

# Слой зависимостей: кэшируется, пока не менялись pyproject.toml / uv.lock.
# torch резолвится с CPU-индекса (см. [tool.uv.sources] в pyproject.toml) — без CUDA.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Исходники, миграции и конфиг моделей.
COPY backend/src ./src
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/config ./config

# Доустановка самого проекта (пакет ai_dnd, editable) по тому же lock-файлу.
# Пути /app/backend совпадают со финальной стадией — editable-ссылки остаются валидными.
RUN uv sync --frozen --no-dev

# --- Финальный образ: только runtime, без uv и инструментов сборки ---
FROM python:3.12-slim

# Непривилегированный пользователь создаётся ДО копирования; /home/app сразу app-owned.
# /home/app/.cache — том для модели эмбеддингов (model-cache в docker-compose.yml).
# Каталог создаётся app-owned, чтобы новый том инициализировался с владельцем app
# (иначе корень тома root-owned и HuggingFace не может писать кэш модели).
RUN useradd --create-home --uid 10001 app \
    && install -d -o app -g app /home/app/.cache

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:$PATH"

WORKDIR /app/backend

# Один слой с владельцем app вместо chown -R (не дублирует содержимое .venv).
COPY --chown=app:app --from=builder /app/backend /app/backend
COPY --chown=app:app content /app/content

COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER app

EXPOSE 8000

# Без аргументов: миграции + uvicorn (API).
# Воркер: тот же образ с command ["taskiq", "worker", "ai_dnd.worker:broker", "ai_dnd.tasks"].
ENTRYPOINT ["docker-entrypoint.sh"]