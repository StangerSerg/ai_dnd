# AI DnD Portal

Портал для настольных ролевых игр с **ИИ-ведущим**: мастер-ИИ ведёт сценарии по правилам D&D 5e
(SRD), игроки ходят по очереди в realtime-столах, расход ИИ считается в кредитах
(1 кредит = $0.0001, каждому новичку — 100 000 бесплатных кредитов в день). Можно играть и с живым
ведущим, по готовым сценариям или импортированным из своих файлов (md/txt/docx/pdf).

## Стек

| Слой | Технологии |
|---|---|
| Бэкенд | Python 3.12+, FastAPI, SQLAlchemy (async) + asyncpg, Alembic, Taskiq (воркер), structlog |
| ИИ | DeepSeek через шлюз с записью расходов (`llm_calls`), локальные эмбеддинги sentence-transformers + pgvector |
| Хранилища | PostgreSQL 16 с pgvector, Redis (брокер задач, realtime-комнаты, pub/sub) |
| Фронтенд | React 19 + TypeScript, Vite, TanStack Router/Query, Tailwind CSS 4, Zustand |
| Зависимости | backend — [uv](https://docs.astral.sh/uv/), frontend — npm |
| Развёртывание | Docker Compose: db + redis + backend + worker + web (nginx) |

## Структура репозитория

| Папка | Что внутри |
|---|---|
| `backend/` | FastAPI-приложение (`src/ai_dnd`), воркер, миграции `alembic/`, конфиг моделей `config/llm.yaml` |
| `frontend/` | React-портал: лобби, столы, карточки персонажей, каталог сценариев, админка |
| `content/` | Встроенный контент: SRD-набор правил, шаблоны карточек, бестиарий, готовые сценарии |
| `infra/` | docker-compose только для **разработки** (PostgreSQL, Redis, S3 на localhost) |
| `docs/` | [Архитектура](docs/ARCHITECTURE.md), [план](docs/PLAN.md), [статус сессий](docs/HANDOFF.md), [roadmap](docs/ROADMAP.md) |
| `docker-compose.yml` | **Продакшен-стек** для самостоятельного развёртывания (этот README) |

## Быстрый старт: Docker (весь стек одной командой)

Нужен только Docker с плагином compose (проверено на Docker 29, compose v5).

```bash
cp .env.example .env
# Откройте .env и заполните секреты:
#   POSTGRES_PASSWORD — пароль базы (и тот же пароль в AI_DND_DATABASE_URL)
#   AI_DND_SECRET_KEY — случайная строка, например `openssl rand -hex 32`
#   AI_DND_DEEPSEEK_API_KEY — ключ с https://platform.deepseek.com/api_keys
docker compose up -d --build
```

Откройте **http://localhost:8080** (порт меняется переменной `WEB_PORT`).

Что происходит при старте:

1. `db` (PostgreSQL 16 + pgvector) и `redis` поднимаются и проходят healthcheck;
2. `backend` применяет миграции (`alembic upgrade head`) и запускает API на 8000
   (внутренний порт, наружу не публикуется);
3. `worker` (тот же образ) запускает `taskiq worker ai_dnd.worker:broker ai_dnd.tasks`;
4. `web` (nginx) отдаёт собранный фронтенд и проксирует `/api` и `/ws` на backend.

Порты базы и Redis **наружу не публикуются** — доступ только из внутренней сети compose.

### Первый сценарий в каталоге

Каталог сценариев берётся из базы. Опубликуйте встроенный демо-сценарий:

```bash
# Быстро, без индексации (поиск по сценарию будет пустым):
docker compose exec backend ai-dnd-play seed --no-index

# Полностью, с индексацией воркером (эмбеддинги, RAG-поиск по сценарию):
docker compose exec backend ai-dnd-play seed --wait
```

При первой индексации воркер скачивает модель эмбеддингов (~500 МБ, нужен интернет);
она кэшируется в томе `model-cache` и повторно не качается. Доступные сценарии —
файлы `content/scenarios/*.yaml` (`--scenario`, `--lang ru|en`).

### Проверка, что всё живо

```bash
docker compose ps                          # все сервисы healthy/running
curl http://localhost:8080/api/auth/csrf   # {"token":"..."} — API через nginx
docker compose exec backend python -m ai_dnd.devtools ping-worker
# «Воркер ответил: pong: hello from devtools»
docker compose logs -f backend worker      # логи без ошибок
```

### Обновление и обслуживание

```bash
docker compose up -d --build     # пересобрать и обновить (миграции применятся сами)
docker compose logs -f backend   # логи API
docker compose down              # остановить (данные в томах сохраняются)
docker compose down -v           # остановить и УДАЛИТЬ данные (базу, redis, кэш модели)
```

### Важно про prod и HTTPS

В `.env.example` стоит `AI_DND_ENVIRONMENT=prod`. В prod cookie сессии помечаются
`Secure`, поэтому **вход по обычному http:// работать не будет** (браузер не примет
cookie). Для доступа не с localhost поставьте впереди TLS-терминатор (например,
Caddy или nginx с Let's Encrypt) и укажите его адрес в `AI_DND_BASE_URL`.
Для локальных экспериментов без TLS используйте `AI_DND_ENVIRONMENT=dev`
и `AI_DND_DEMO_AUTH=true` (регистрация без подтверждения письма).

## Альфа-стенд (тестовые аккаунты)

Профиль для альфа-тестирования: демо-регистрация без подтверждения email,
щедрые лимиты кредитов (тестировщики не платят) и **реальный ИИ** DeepSeek.
Аккаунты тестовые и ценности не представляют. Образец — [.env.alpha.example](.env.alpha.example).

```bash
cp .env.alpha.example .env
# Откройте .env и заполните плейсхолдеры change-me:
#   POSTGRES_PASSWORD — пароль базы (и тот же пароль в AI_DND_DATABASE_URL)
#   AI_DND_SECRET_KEY — случайная строка, например `openssl rand -hex 32`
#   AI_DND_DEEPSEEK_API_KEY — ключ с https://platform.deepseek.com/api_keys
docker compose up -d --build

# Опубликовать сценарий «Пять Стражей» (ru) в свежую БД, с индексацией воркером:
docker compose exec backend ai-dnd-play seed --scenario five-guardians --lang ru --wait
# Быстрый вариант без индексации (RAG-поиск по сценарию будет пустым):
# docker compose exec backend ai-dnd-play seed --scenario five-guardians --lang ru --no-index
```

Откройте **http://localhost:8080** и зарегистрируйте тестовый аккаунт:
при `AI_DND_DEMO_AUTH=true` email подтверждать не нужно — вход работает сразу
(пароль при входе всё равно проверяется).

Важно:

- `AI_DND_ENVIRONMENT=dev` обязателен: только dev даёт не-Secure cookie,
  поэтому вход по обычному http:// работает. В prod/test cookie становятся
  Secure и вход по HTTP ломается (см. раздел про HTTPS выше).
- Лимиты щедрые: 100 000 бесплатных кредитов в день ($10), лимит пользователя
  $10/день и $100/месяц, сессии — $10/день. Кредиты не должны блокировать игру.
- Глобальный USD-предохранитель остаётся защитой реального баланса DeepSeek:
  не более **$20/день и $300/месяц** фактических расходов платформы.
- Переход на строгий prod позже: `AI_DND_ENVIRONMENT=prod` + `AI_DND_DEMO_AUTH=false`
  + HTTPS + настроенный SMTP — см. [.env.example](.env.example) и чек-лист прод-флагов.

## Быстрый старт: разработка (без Docker для приложения)

Нужны [uv](https://docs.astral.sh/uv/), Node.js 24+ и Docker (для сервисов).

```bash
# 1. Сервисы для разработки: PostgreSQL (pgvector, базы ai_dnd и ai_dnd_test),
#    Redis, S3 — порты только на 127.0.0.1.
docker compose -f infra/docker-compose.yml up -d

# 2. Бэкенд.
cd backend
uv sync                          # окружение и зависимости (Python >= 3.12)
cp .env.example .env             # локальные настройки; ключ DeepSeek — по желанию
uv run alembic upgrade head      # миграции (БД ai_dnd создаётся сервисом postgres)

# 3. API (в отдельном терминале).
uv run uvicorn ai_dnd.main:app --reload --port 8000

# 4. Воркер фоновых задач (в отдельном терминале).
uv run taskiq worker ai_dnd.worker:broker ai_dnd.tasks --reload

# 5. Фронтенд (в отдельном терминале). Dev-прокси /api и /ws настроены
#    в frontend/vite.config.ts на работающий backend.
cd ../frontend
npm install
npm run dev                      # http://localhost:5173
```

Проверка: `curl http://localhost:8000/health` → `{"status":"ok",...}`;
`uv run python -m ai_dnd.devtools ping-worker` → «Воркер ответил: pong: ...».
Шлюз ИИ (нужен ключ `AI_DND_DEEPSEEK_API_KEY` и баланс DeepSeek):
`uv run python -m ai_dnd.devtools llm-check`.

### Игра в консоли (без фронтенда)

```bash
cd backend
uv run ai-dnd-play --lang ru --party fighter,rogue,cleric   # демо-сценарий, ИИ-ведущий
uv run ai-dnd-play --fake-llm                               # модель-заглушка: без денег и сети
```

Полезные флаги: `--lang en`, `--improvisation strict|moderate|free`, `--seed 7`,
`--no-db`, `--debug`. В игре: `/sheet [имя]`, `/cost`, `/log [N]`, `/help`, `/quit`.
Импорт своего сценария: `uv run ai-dnd-play import module.md --fake-llm`,
затем публикация: `uv run ai-dnd-play seed --scenario <slug> --wait`.

## Переменные окружения

Продакшен-стек читает корневой `.env` (образец — [.env.example](.env.example));
dev-бэкенд — `backend/.env` (образец — `backend/.env.example`). Префиксы:
общие настройки и identity — `AI_DND_`, биллинг — `AI_DND_BILLING_`.

| Переменная | Назначение | Дефолт |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Пользователь/пароль/база сервиса `db` | `ai_dnd` / `change-me` / `ai_dnd` |
| `WEB_PORT` | Внешний порт веб-интерфейса | `8080` |
| `AI_DND_ENVIRONMENT` | `dev` \| `test` \| `prod`; в prod — Secure-cookie и строгая регистрация | `dev` |
| `AI_DND_LOG_LEVEL` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | `INFO` |
| `AI_DND_LOG_FORMAT` | `console` (dev) \| `json` (сервер) | `console` |
| `AI_DND_DATABASE_URL` | PostgreSQL, драйвер asyncpg | `postgresql+asyncpg://ai_dnd:ai_dnd@localhost:5432/ai_dnd` (в compose — хост `db`) |
| `AI_DND_REDIS_URL` | Redis (брокер, realtime) | `redis://localhost:6379/0` (в compose — хост `redis`) |
| `AI_DND_SECRET_KEY` | Секрет сессий/токенов identity; в проде обязателен свой | dev-заглушка (пишет warning в лог) |
| `AI_DND_DEEPSEEK_API_KEY` | Ключ DeepSeek; без него ИИ-функции не работают | пусто |
| `AI_DND_DEMO_AUTH` | Регистрация сразу подтверждает email (только демо/dev) | `false` |
| `AI_DND_FAKE_LLM` | Модель-заглушка вместо LLM для задач воркера | `false` |
| `AI_DND_BASE_URL` | Публичный адрес для ссылок в письмах | `http://localhost:8000` |
| `AI_DND_CONTENT_DIR` | Папка встроенного контента | `content/` в корне (в образе `/app/content`) |
| `AI_DND_LLM_CONFIG` | YAML моделей/цен ИИ | `backend/config/llm.yaml` |
| `AI_DND_EMBEDDING_MODEL` / `AI_DND_EMBEDDING_DIM` | Модель эмбеддингов и размерность | `paraphrase-multilingual-MiniLM-L12-v2` / `384` |
| `AI_DND_TURN_WINDOW_SECONDS` | Окно сбора действий игроков | `180` |
| `AI_DND_TURN_LOCK_MS` | Замок комнаты на время хода | `180000` |
| `AI_DND_SESSION_TTL_DAYS` | Срок жизни сессии входа | `30` |
| `AI_DND_SMTP_HOST` / `PORT` / `USER` / `PASSWORD` / `FROM` | SMTP для писем подтверждения | пусто / `587` |
| `AI_DND_DISCORD_*` / `AI_DND_GOOGLE_*` | OAuth-ключи (необязательно) | пусто |
| `AI_DND_BILLING_CREDIT_USD` | Курс: долларов за 1 кредит | `0.0001` |
| `AI_DND_BILLING_DAILY_FREE_CREDITS` | Бесплатные кредиты в день | `100000` |
| `AI_DND_BILLING_RESERVE_CREDITS` | Резерв на один AI-вызов | `50` |
| `AI_DND_BILLING_USER_DAILY_SPEND_CREDITS` | Лимит пользователя в день | `100000` |
| `AI_DND_BILLING_USER_MONTHLY_SPEND_CREDITS` | Лимит пользователя в месяц | `1000000` |
| `AI_DND_BILLING_SESSION_DAILY_SPEND_CREDITS` | Лимит сессии в день | `100000` |
| `AI_DND_BILLING_GLOBAL_DAILY_USD` / `GLOBAL_MONTHLY_USD` | Предохранители платформы | `20.00` / `300.00` |
| `AI_DND_BILLING_PROVIDER_BALANCE_ALERT_USD` | Порог алерта о балансе провайдера | `2.00` |

## Миграции

Alembic, конфиг `backend/alembic.ini`, скрипты `backend/alembic/versions/`.
URL базы берётся из настроек приложения (`AI_DND_DATABASE_URL`).

```bash
cd backend
uv run alembic upgrade head                 # применить (в Docker — автоматически при старте backend)
uv run alembic revision --autogenerate -m "описание"   # новая миграция после правки моделей
uv run alembic downgrade -1                 # откатить последнюю
```

Первая миграция включает расширение `vector` — поэтому образ базы именно
`pgvector/pgvector:pg16`, а не обычный postgres.

## Тесты и проверки

```bash
cd backend
uv run pytest                  # юнит-тесты, сервисы не нужны
uv run pytest -m integration   # интеграционные, нужны сервисы из infra/docker-compose.yml
uv run ruff check              # стиль и частые ошибки
uv run ruff format             # форматирование
uv run mypy                    # типы (strict)
uv run lint-imports            # границы модулей (rules/formulas — чистые функции)

cd ../frontend
npm run typecheck              # tsc
npm run lint                   # eslint
npm run build                  # прод-сборка (tsc -b && vite build)
```

То же выполняется в CI при каждом пуше — [.github/workflows/ci.yml](.github/workflows/ci.yml).

## Прод-флаги (чек-лист)

- `AI_DND_ENVIRONMENT=prod` — Secure-cookie, строгая регистрация; **нужен HTTPS**;
- `AI_DND_DEMO_AUTH=false` — подтверждение email обязательно (настройте SMTP);
- `AI_DND_FAKE_LLM=false` — реальные вызовы DeepSeek;
- `AI_DND_DEEPSEEK_API_KEY=<реальный ключ>` и баланс на платформе DeepSeek;
- `AI_DND_SECRET_KEY` — длинная случайная строка, не дефолт;
- `POSTGRES_PASSWORD` — свой пароль, тот же в `AI_DND_DATABASE_URL`;
- `AI_DND_LOG_FORMAT=json`, `AI_DND_BASE_URL=https://ваш-домен`;
- порты db/redis наружу не публиковать (в compose уже так).

## Troubleshooting

| Симптом | Причина и решение |
|---|---|
| `port is already allocated` при старте `web` | Порт 8080 занят. Задайте `WEB_PORT=8081` в `.env` и `docker compose up -d web`. Dev-сервисы (5432/6379) не конфликтуют: продакшен-стек их не публикует. |
| `backend` unhealthy, в логах `connection refused` / `password authentication failed` | Пароль в `AI_DND_DATABASE_URL` не совпадает с `POSTGRES_PASSWORD`. Помните: пароль фиксируется при **первом** старте тома `pgdata`; смена в `.env` позже требует `docker compose down -v` (удалит данные!) или `ALTER USER` внутри базы. |
| `CREATE EXTENSION vector` fails | Образ базы заменён на обычный postgres. Нужен `pgvector/pgvector:pg16` (как в compose). |
| Регистрация в prod: письмо не приходит | Не настроен SMTP (`AI_DND_SMTP_*`). Для локальной проверки включите `AI_DND_DEMO_AUTH=true` и `AI_DND_ENVIRONMENT=dev`. |
| Вход «не сохраняется» (снова форма логина) | `AI_DND_ENVIRONMENT=prod` без HTTPS: браузер отбрасывает Secure-cookie. См. раздел про HTTPS. |
| ИИ-ведущий молчит / ошибки LLM в логах | Нет ключа `AI_DND_DEEPSEEK_API_KEY`, нулевой баланс DeepSeek или сеть недоступна. Проверка: `docker compose exec backend python -m ai_dnd.devtools llm-check`. Бесплатный прогон цикла: `AI_DND_FAKE_LLM=true`. |
| Ходы ИИ не считаются, задачи висят | Воркер не запущен/упал: `docker compose ps`, `docker compose logs worker`, проверка `ping-worker` (см. выше). |
| Каталог сценариев пуст | Не опубликован ни один сценарий: `docker compose exec backend ai-dnd-play seed --no-index`. |
| Первая индексация долгая или падает по сети | Воркер качает модель эмбеддингов (~500 МБ) с Hugging Face. Нужен интернет; кэш — том `model-cache`. Без индексации игра работает: `seed --no-index`. |
| `502 Bad Gateway` у `/api` | Backend ещё стартует (миграции) или упал: `docker compose logs backend`. Nginx резолвит `backend` при старте — после пересоздания backend перезапустите web: `docker compose restart web`. |
| WebSocket не подключается | Прокси `/ws` требует заголовков Upgrade — они уже в `frontend/nginx.conf`; внешний TLS-терминатор тоже должен пропускать WebSocket. |

## Документация

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — архитектура и решения;
- [docs/PLAN.md](docs/PLAN.md) — план внедрения по этапам;
- [docs/HANDOFF.md](docs/HANDOFF.md) — фактический статус и контекст сессий;
- [docs/ROADMAP.md](docs/ROADMAP.md) — дорожная карта;
- [infra/README.md](infra/README.md) — dev-сервисы;
- [backend/.env.example](backend/.env.example) — настройки dev-бэкенда.

Лицензия: [LICENSE](LICENSE).