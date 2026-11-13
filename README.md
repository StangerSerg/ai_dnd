# AI DnD Portal

Портал для настольных ролевых игр: с живым ведущим или с ИИ-ведущим, по готовым и своим сценариям.

- [Архитектура](docs/ARCHITECTURE.md)
- [План внедрения](docs/PLAN.md)

## Структура

| Папка | Что внутри |
|---|---|
| `backend/` | Бэкенд на Python (FastAPI) |
| `frontend/` | Фронтенд на React + TypeScript (этап 3) |
| `infra/` | docker-compose с сервисами для разработки — см. [infra/README.md](infra/README.md) |
| `content/` | Встроенный контент: SRD, пресеты карточек, демо-сценарий (этап 1) |
| `docs/` | Документация |

## Бэкенд: начало работы

Нужны [uv](https://docs.astral.sh/uv/) и Docker.

```bash
cd backend
uv sync                      # окружение и зависимости
cp .env.example .env         # локальные настройки
uv run pre-commit install    # проверки перед каждым коммитом
```

## Запуск

Сервисы (PostgreSQL с pgvector, Redis, S3-хранилище) — из корня репозитория:

```bash
docker compose -f infra/docker-compose.yml up -d
```

Дальше всё из папки `backend`. Миграции базы:

```bash
uv run alembic upgrade head
```

API, с перезапуском при изменении кода:

```bash
uv run uvicorn ai_dnd.main:app --reload
```

Воркер фоновых задач (в отдельном терминале):

```bash
uv run taskiq worker ai_dnd.worker:broker ai_dnd.tasks --reload
```

Проверить, что всё работает:

```bash
curl http://localhost:8000/health
```

```bash
uv run python -m ai_dnd.devtools ping-worker
```

Первая команда должна ответить `{"status":"ok", ...}`, вторая — `Воркер ответил: pong: ...`.

## Проверки

```bash
uv run pytest                  # тесты, сервисы не нужны
uv run pytest -m integration   # интеграционные тесты, нужны запущенные сервисы
uv run ruff check              # стиль и частые ошибки
uv run ruff format             # форматирование
uv run mypy                    # типы
uv run lint-imports            # границы модулей
```

То же самое выполняется на GitHub при каждом пуше — см. [.github/workflows/ci.yml](.github/workflows/ci.yml).

Границы модулей описаны в `backend/pyproject.toml`, раздел `[tool.importlinter]`: движок правил (`rules`) и вычислитель формул (`sheets.formulas`) не импортируют остальной проект и библиотеки ввода-вывода.
