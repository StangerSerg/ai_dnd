# VM альфа-стенд — инструкция агентам

Как новому чату подключиться к развёрнутому альфа-стенду и продолжить работу,
не начиная с нуля. **Секретов здесь нет** — креды и точные ID в локальном
ignored-файле `.local/vm-stand.md` (покрыт `.gitignore`, не коммитится).

## 0. С чего начать новую сессию

1. Прочитать `docs/HANDOFF.md` (свежий раздел) и актуальный раздел `docs/PLAN.md`.
2. Прочитать этот файл и `.local/vm-stand.md` (данные подключения, секреты).
3. Проверить состояние стенда read-only (см. §2), прежде чем что-то менять.

## 1. Что развёрнуто

- VM пользователя `root@201.34.159.217`, домен **camp2city.ru**, web на `:80`.
- Проект на VM `/root/ai_dnd`, compose project `ai-dnd-portal`, стек в Docker:
  `backend` (FastAPI), `worker` (taskiq, DM-ходы/бой), `db` (PostgreSQL+pgvector),
  `redis`, `web` (nginx + frontend). Образ backend/worker общий:
  `ai-dnd-portal/backend:local`.
- Ресурсы VM тесные: RAM 1.9 ГБ + swap 3 ГБ. Эмбеддинги на CPU.
- Авторизация демо (тестовые аккаунты), биллинг с большими лимитами (тестировщики
  не платят), реальный LLM (DeepSeek). Конфиг — `.env.alpha.example`
  (`ENVIRONMENT=dev` обязательно для HTTP, иначе Secure-cookie ломает вход).

## 2. Подключение и проверка состояния (read-only)

SSH идёт через **WSL Ubuntu-24.04** (ключи не авторизованы, sshpass нет).
Хелпер с паролем: `C:\Users\user\AppData\Local\Temp\vm_ssh.sh` (пробрасывает stdin).
Пароль VM и метод — в `.local/vm-stand.md`. Вызов из Git Bash:

```bash
wsl.exe -d Ubuntu-24.04 -- bash -lc "bash /mnt/c/Users/user/AppData/Local/Temp/vm_ssh.sh '<remote cmd>'"
```

Статус контейнеров:
```bash
wsl.exe -d Ubuntu-24.04 -- bash -lc "bash /mnt/c/Users/user/AppData/Local/Temp/vm_ssh.sh 'cd /root/ai_dnd && docker compose ps'"
```

- WSL периодически даёт `WSAETIMEDOUT` — просто повторить вызов.
- Не запускать параллельные WSL SSH во время большой передачи (забивает канал).
- Кавычки SQL ломаются при многослойном экранировании (Git Bash→WSL→SSH→psql).
  Надёжно: писать запрос в `.local/*.sql` и подавать через stdin-пайп:
  ```bash
  tr -d '\r' < .local/q_cost.sql | wsl.exe -d Ubuntu-24.04 -- bash -lc "bash /mnt/c/Users/user/AppData/Local/Temp/vm_ssh.sh 'cd /root/ai_dnd && docker compose exec -T db psql -U ai_dnd -d ai_dnd -t'"
  ```

## 2b. Подключение DBeaver к БД стенда

PostgreSQL в контейнере `ai-dnd-portal-db-1` (образ `pgvector/pgvector:pg16`), порт 5432
**наружу НЕ опубликован** — только внутренняя сеть `ai-dnd-portal_appnet`. На хосте VM
порт 5432 не слушается, поэтому прямое подключение к `201.34.159.217:5432` не работает.
Нужен SSH-туннель (DBeaver умеет встроенно).

Параметры БД: database `ai_dnd`, user `ai_dnd`, password = `POSTGRES_PASSWORD` из
`/root/ai_dnd/.env` на VM (СЕКРЕТ, не коммитить; тот же пароль в `AI_DND_DATABASE_URL`).
Прочитать на VM: `grep '^POSTGRES_PASSWORD=' /root/ai_dnd/.env`.

**Способ 1 — SSH-туннель к IP контейнера (без изменений, работает сразу):**
- Вкладка «Основное»: Host = `172.19.0.3` (IP контейнера db), Port = `5432`,
  Database = `ai_dnd`, Username = `ai_dnd`, Password = `<DB password>`.
- Вкладка «SSH»: Use SSH tunnel = вкл; Host = `201.34.159.217`, Port = `22`,
  User = `root`, Password = `<VM password>` (или ключ).
- DBeaver пробрасывает локальный порт → VM → `172.19.0.3:5432`.
- МИНУС: IP контейнера может смениться при пересоздании db. Узнать текущий:
  `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ai-dnd-portal-db-1`.

**Способ 2 — опубликовать 5432 на loopback VM (стабильно, нужен рестарт db):**
- **УЖЕ ПРИМЕНЕНО на стенде 22.11** (выбор пользователя «loopback + SSH-туннель»):
  в VM-local `docker-compose.override.yml` добавлено:
  ```yaml
  services:
    db:
      ports:
        - "127.0.0.1:5432:5432"
  ```
  `127.0.0.1` — порт виден ТОЛЬКО на loopback VM, не в интернете (безопасно).
  Проверено: `ss -tlnp` → `127.0.0.1:5432` (docker-proxy); db healthy, данные целы.
- Если нужно повторить/изменить: `docker compose up -d db` (пересоздаёт db, краткий
  рестарт PostgreSQL — по разрешению; том pgdata сохраняет данные).
- DBeaver «Основное»: Host = `localhost`, Port = `5432`, Database = `ai_dnd`,
  Username = `ai_dnd`, Password = `<DB password>`.
- Вкладка «SSH»: Use SSH tunnel = вкл; Host = `201.34.159.217`, Port = `22`, User = `root`,
  Password = `<VM password>`; remote host = `localhost` (по умолчанию), remote port = `5432`.
- Плюс: адрес стабилен (`localhost:5432`) независимо от IP контейнера. DBeaver сохраняет
  туннель в профиле — после одноразовой настройки подключение в один клик.

## 3. Бюджет прогона — ТОЛЬКО через psql

Дневной free-грант (100000 кредитов = $10) **сбрасывается в UTC полночь**, поэтому
прокси «потрачено = грант − баланс» ломается через полночь. Авторитетная стоимость:

```sql
SELECT count(*) AS calls, round(sum(cost)::numeric,6) AS usd
FROM llm_calls WHERE session_id='<SESSION>';
```

`credit_usd=0.0001`; бюджет $0.3 = 3000 кредитов. SESSION/INVITE/аккаунты — в
`.local/vm-stand.md`. На лимите — **пауза и вопрос пользователю**, не автостоп.

## 4. Игровой прогон через REST (не WS)

Автопилот-проба: `C:\Users\user\AppData\Local\Temp\autopilot.py`
(`setup|state|turn <action>|combat <index>`), BASE=http://camp2city.ru.
Запуск: `cd /c/Users/user/AppData/Local/Temp && PYTHONUTF8=1 PYTHONUNBUFFERED=1 python autopilot.py turn "<действие>"` (Bash timeout 300000).

Поток хода:
- Игрок: `POST /api/sessions/{id}/actions {text}` (только player-seat; dm-seat → 403).
- Если все player-seats подали — ход стартует сам; иначе `POST /{id}/turn` или таймаут 180s.
- Worker `dm_turn` под turn_lock → DeepSeek planner+narrator → события журнала.
- Бой: `GET /{id}/combat-options` → `POST /{id}/combat-actions {option_index}` → worker `combat_step`.
- Состояние = `apply_events(journal)`; `GET /api/sessions/{id}` и `/events?since=`.

## 5. Стабильность воркера (фиксы уже в образе)

- **Таймауты redis** (`backend/src/ai_dnd/worker.py`): redis-py 8.1.0 по умолчанию
  socket_timeout=5s убивал receiver во время синхронной загрузки SentenceTransformer
  (~27–40s, перезагружается каждый ход). Поднято: socket_timeout=60,
  socket_connect_timeout=30, retry_on_timeout=True, xread_block=5000,
  health_check_interval=30; result_backend socket_timeout=60. Пользователь одобрил 60s
  («не хай-лоад, могут подождать, лишь бы игра не стопорилась»).
- **VM-local override** `docker-compose.override.yml`: worker `--workers 1`
  (репо `docker-compose.yml` НЕ тронут). «1 процесс + рестарт» — решение пользователя.
- **Боевой тупик #50** (`backend/src/ai_dnd/realtime/turns.py:678-684`):
  `_advance_combat` возвращал "ok" на ходе существа после нарратива границы раунда →
  combat.current=creature, your_turn=False, нет HTTP-триггера → тупик. Фикс:
  `if current is None: return "ok"` + `if not is_party(current): continue`.
  Восстановление зависшего боя: подать player action → `run_dm_turn` → `_advance_combat`
  возобновляет бой до хода игрока.

## 6. Пересборка и доставка образа на VM

Code-only изменение → слой `uv sync` кэшируется → быстрая пересборка (~70s), без
повторной загрузки torch. Сборка из корня репо:
```bash
docker compose build backend
```
Доставка (docker save → gzip → stdin → docker load на VM; VM дедуплицирует слои):
```bash
docker save ai-dnd-portal/backend:local | gzip -1 | wsl.exe -d Ubuntu-24.04 -- bash -lc "bash /mnt/c/Users/user/AppData/Local/Temp/vm_ssh.sh 'docker load'"
```
Передачу запускать в фоне (run_in_background), уведомление придёт по завершении.
После load — пересоздать контейнеры новым образом:
```bash
wsl.exe -d Ubuntu-24.04 -- bash -lc "bash /mnt/c/Users/user/AppData/Local/Temp/vm_ssh.sh 'cd /root/ai_dnd && docker compose up -d backend worker'"
```
Рестарт worker/backend — часть авторизованного деплоя/прогона; прочее — по разрешению.

## 7. Operational don'ts

- Не трогать `camp_to_city_game` (остановлен) и системные службы VM (docker/ssh/zabbix).
- Не мутировать/не рестартить локальный run-stack `:5176` (сессия 6200e9c2, Сокол,
  хост=Root) и аккаунт пользователя `yarser1988@gmail.com` (Странник).
- Секреты (пароль VM, пароли аккаунтов, ключ DeepSeek) — только в ignored
  `.env`/`.local`, не публиковать и не коммитить.
- Платные LLM-прогоны — только по решению/пре-одобрению цикла.
- `git commit`/`push` — только по явной инструкции пользователя.
- Тесты — после завершения этапа целиком; существующие тесты/CI не удалять.
- Разработку вести субагентами (Root — архитектор/тимлид, независимо проверяет
  каждый результат); бриф субагенту явно запрещает мутации БД и выход за рамки файлов.

## 8. Текущий статус (обновлять по ходу)

- Деплой #48: стек поднят на camp2city.ru:80, воркер стабилен (таймауты + 1 процесс).
- Сценарии на стенде (public/published): `five-guardians.ru` и демо
  `shadow-over-millford` **ru + en** («Тень над Мельничьим Бродом», засеян 21.11,
  по 32 индексированных фрагмента). Засев/переиздание:
  `docker compose exec -T backend ai-dnd-play seed --scenario <slug> --lang ru|en --wait`
  (файлы в образе `/app/content/scenarios/`; индексация воркером — локальные эмбеддинги,
  без LLM-затрат). ID версий — в `.local/vm-stand.md`.
- Фикс #50 ЗАКРЫТ: код принят Root (ruff/mypy clean), образ доставлен, backend/worker
  пересозданы. **Live-проверка 21.11:** зависший бой (current=wolf_5, your_turn=False)
  после подачи player action возобновился и разрешился до конца без тупика.
- Прогон #49 «Пять Стражей», бюджет $0.3 — **ФИНАЛ TPK на 21.11:** 91 вызов, **$0.107244**;
  сюжет дошёл до wolf_forest (7 сцен), ход 41. Ворон (6/28 HP) пал от крита волка
  (nat20, 15 урона) в бою со стаей из 5 волков; CombatEnded winner=enemies, PartyDefeated,
  нарративный финал сгенерирован. Остаток бюджета ~$0.193. Решение пользователя:
  остановиться на TPK или новый заход с остатком.
- Финальное ревью Astra — отложено до понедельника (лимит исчерпан).