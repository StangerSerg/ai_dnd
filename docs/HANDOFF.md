## 23.11 (ветка release/v0.1) — создатель стола = лидер+игрок, публичность при создании, удаление стола

- **ВЕТКА: release/v0.1** (HEAD 2376cb8). Работа v0.2 закоммичена пользователем отдельно:
  `release/v0.2` = 1408ef8 «Start of v0.2». Правки этой сессии v0.2-фич НЕ содержат
  (например, `available_classes` сценария есть только в v0.2) — при слиянии v0.1→v0.2
  ожидаются конфликты в `realtime/service.py`, `pages/games-page.tsx`, i18n-файлах.
- **Решение пользователя:** создатель стола больше не занимает dm-место (Мастер — ИИ,
  человеческое dm-место бессмысленно). При `dm_mode="ai"` он садится ЛИДЕРОМ + ИГРОКОМ:
  `host_id` = создатель и у него место `role="player"` со своим персонажем; dm-место не
  создаётся вовсе. При `dm_mode="human"` прежнее поведение сохранено (режим — заглушка,
  но пользователь решил его оставить). Лидер-игрок НЕ видит секреты Мастера: фильтры
  `can_see(dm=seat.role=="dm")` и `state_for` не менялись — при отсутствии dm-места
  события `visibility=dm`, чужие `player:<id>` и `state.facts/summary` не отдаются никому.
- **Backend:** `create_session` принимает `character_id` (обязателен для ai → иначе 422
  `character_required`), `recruiting`, `seats_total` (1..12|null); актёр создателя входит
  в СТАРТОВЫЕ setup/state (`ActorsJoined` не пишется), возвращает dataclass `CreatedSession`.
  Общая валидация персонажа вынесена в `_player_seat_from_character` (используется и в
  `join_session`). **Закрыта дыра авторизации:** добавлена проверка владельца персонажа
  (`_require_character_owner`, 403 `character_forbidden`, в т.ч. `owner_id is None`) —
  раньше сесть за стол можно было ЧУЖИМ персонажем. Лидерство определяется `host_id`,
  клиенту отдаётся новым флагом `is_host` в `SessionResponse` и `SessionListItem`
  (`host_id` не отдаётся). `leave_seat`/`kick_seat` защищают лидера по `host_id`
  (422 `cannot_leave_host` / `cannot_kick_host`). `transfer_host`: если dm-места нет,
  `host_id` просто переходит цели и она СОХРАНЯЕТ место и персонажа (ответ
  `dm_seat_id`/`removed_seat_id` = null, сокеты не закрываются); при наличии dm-места
  (human/старые столы) — прежняя логика. 409 `dm_seat_not_found` удалён как невозможный.
- **Удаление стола (безопасное):** миграция `0016_session_archived`
  (`game_sessions.archived bool default false`, down_revision 0015) +
  `POST /api/sessions/{id}/archive` → `{archived:true}`: только лидер, только
  `status=="stopped"` (иначе 409 `session_not_stopped`), идемпотентно, ставит
  `recruiting=false`, пишет аудит `session.archive`. Записи из БД НЕ удаляются.
  Архив скрыт из `list_sessions`, `open-tables`, `by-invite` (404 `invalid_invite` —
  существование не раскрывается), `join_session`, `observe_session`. Админ-надзор
  архив видит (`SessionItem.archived`, `SessionDetail.archived`).
- **Frontend:** диалог создания стола — пикер своего персонажа (портрет+имя, empty-state
  со ссылкой на /characters, submit заблокирован без выбора) и новый компонент
  `components/create-visibility.tsx` (переключатель публичный/приватный + ёмкость 1..12/∞,
  по умолчанию приватный и 4). Ёмкость уходит в запрос только для публичного стола
  (иначе приватный стол получал невидимый лимит 4 и `table_full` на 5-м приглашённом).
  `isLeader` везде считается по `is_host`, а не по `role==='dm'`. Передача лидерства в
  ai-режиме не выгоняет никого в зал (`host-leave-dialog` получил `staysAtTable`,
  `use-session-socket` допускает null-ids, `game-page` различает legacy/human-ветку).
  В зале у лидера появились «Закрыть стол» (stop, для active) и «Удалить стол»
  (archive, для stopped), оба с подтверждением вторым кликом.
- **Гейты Root (все зелёные):** backend ruff check / ruff format --check / mypy (202 файла) /
  lint-imports (3 kept) / pytest 819 passed, 26 deselected; frontend typecheck / lint / build
  (предсуществующий chunk-warning). i18n паритет ru/en — 828/828 ключей, расхождений нет.
  Из правок исполнителя Root откатил одно: придуманную проверку
  `class_id not in content.classes` → 422 `class_not_available` (в v0.1 такого гейта нет,
  он появился бы как новое ограничение на вход).
- **ДЕПЛОЙ НА VM 23.11 (выполнен Root, коммит e8e84b7).** Образы пересобраны локально и
  доставлены штатно `docker save | gzip -1 | WSL SSH stdin → docker load`
  (web 127 МБ — 18 с, backend 2.25 ГБ — 236 с), контейнеры `backend worker web`
  пересозданы; entrypoint сам накатил миграцию — `alembic current` = **0016 (head)**.
  Все 5 контейнеров Up/healthy, traceback'ов в логах нет. Инфраструктура не менялась
  (compose/.env/Dockerfile/nginx/content не тронуты), поэтому достаточно образов.
  **Подтверждено живым прогоном пользователя:** стол `80e99bb2` создан через новый диалог
  (`recruiting=t`, `seats_total=3`, `dm_mode=ai`), в `seats` ровно ДВА места `role=player`
  (controller=human, с user_id и character_id, `actor_id=pc_<hex>`) и НЕТ dm-места;
  `dm_open_session` отработал (DeepSeek 200 → `dm_open_done ok`); второй игрок сел;
  «Удалить стол» применён дважды — 2 stopped-сессии получили `archived=t` и скрыты из зала.
  Старый backend-образ на VM остался untagged — это путь отката, НЕ prune'ить.
- **НЕ проверено (долг):** интеграционные тесты не запускались; передача лидерства и
  `host-leave` в ai-режиме на стенде не прогонялись; вёрстка диалога создания и лидерских
  кнопок глазами Root не смотрелась. Смок под тест-аккаунтом `alpha@test.local` получил 403
  (аккаунт verified и не забанен — вероятно, CSRF/Origin на голом urllib-запросе);
  на реальные браузерные сессии это не повлияло.
  Новые тесты не писались (отложенный тестовый долг до рабочего прототипа).

## 21.11 — альфа-стенд #44 принят; поворот на деплой на VM пользователя

- **#44 Альфа-стенд ПРИНЯТ Root.** Дефолты биллинга подняты (тестировщики не платят):
  `BillingSettings` daily_free/user_daily/session_daily=100000 кредитов ($10),
  user_monthly=1000000 ($100); глобальный USD-предохранитель $20/день, $300/мес
  (защита реального баланса DeepSeek). credit_usd=0.0001/reserve=50/provider_alert=2
  без изменений. Демо-авторизация оставлена для альфы (аккаунты тестовые). Новый
  `.env.alpha.example` (ENVIRONMENT=dev обязательно для HTTP — иначе Secure cookie
  ломает вход; DEMO_AUTH=true; FAKE_LLM=false) + README-раздел «Альфа-стенд» +
  `.gitignore` `!.env.alpha.example`. `.env.example` биллинг-блок синхронизирован,
  прод-флаги не тронуты. Гейты Root: ruff/mypy clean (130 files). Файлы:
  backend/src/ai_dnd/billing/settings.py, .env.alpha.example, .env.example, README.md,
  .gitignore, docs/PLAN.md.
- **Поворот на VPS-деплой (отменяет «самостоятельное развёртывание» 19.11).**
  Пользователь дал свою VM `root@201.34.159.217`, другой проект остановить, портал
  развернуть позже когда готов. Доступ: SSH из WSL Ubuntu-24.04, пароль через
  SSH_ASKPASS (ключи не авторизованы, sshpass нет). Временные askpass-скрипты с
  паролем созданы в %TEMP% и УДАЛЕНЫ после работы; пароль в проекте/памяти не хранить.
- **Другой проект на VM ОСТАНОВЛЕН:** `camp_to_city_game` (compose
  `/root/camp_to_city_game`: core :8000, game1 :8001, game2 :8002, redis; тома
  core-data/redis-data). `docker compose stop` → все Exited, порты 8000–8002 свободны,
  restart=unless-stopped (не воскреснет после ребута), данные/образы целы. Системные
  службы (docker/ssh/zabbix) не тронуты. Ресурсы VM: RAM 1.9 ГБ, диск 28 ГБ (24 свободно)
  — ОГРАНИЧЕНИЕ: наш стек ~2.25 ГБ образ + embeddings CPU тесен в 1.9 ГБ RAM, при
  деплое тюнить память/swap. Детали в PLAN.md «Деплой на VM пользователя».
- **Осталось для альфа-деплоя:** решение по коммиту (HEAD f458c8e далеко позади дерева),
  чистка каркаса после вывода run-стека, локальный подъём стенда + смок (секреты .env
  заполняет пользователь; платный LLM — по разрешению), финальное ревью Astra, деплой
  на VM (позже, с тюнингом под 1.9 ГБ RAM), тесты этапа 3 (#7) параллельно.
- **Run-stack :5176 всё ещё поднят** (пользователь играет соло за Сокола, хост=Root):
  backend :8033 PID 15192 (backend8.log, FAKE_LLM=false), worker (worker10.log),
  vite :5176 PID 11416 (vite2.log). Docker postgres:5432+redis:6379 healthy, БД
  ai_dnd_run, redis db14, сессия 6200e9c2 last_seq=39. Не мутировать/не рестартить
  без разрешения. Коммитов НЕТ.

## 20.11 (текущая сессия) — #40 presence ЗАКРЫТ live; #51 минимальный фикс АКТИВИРОВАН live

- **#40 присутствие NPC (ЗАКРЫТ, live PASS).** БД-авторитет `state.npc_locations` + `npc.home`
  + Redis-кэш `ai_dnd:npcloc:{sid}`, DM-инструмент `move_npc`, гейт `game/presence.py`.
  Home 11 NPC five-guardians.ru применены ко всем версиям в живой run-БД
  (`.local/run7/set_npc_homes.py --apply`). Backend+worker рестартнуты (разрешение
  «Применить и рестартнуть сейчас»). Live read-only проверка
  (`.local/run7/verify_live_presence.py`, сессия 6200e9c2): tavern → в промпте только
  severin/efim/agafya, charioteer/hermit исключены. Баг «Хоремхеб в таверне» устранён.
- **#51 «мёртвый Северин снова жив» — МИНИМАЛЬНЫЙ ФИКС (задача #42): КОД принят Root,
  НЕ активирован в живой игре.** Причина: сценарные NPC не боевые актёры
  (`Npc.statblock` не используется, `start_combat` спавнит только существ энкаунтера),
  смерть в прозе DM нигде не записана; в нарративном слое не было статуса жив/мёртв.
  - Backend: `SessionState.npc_status` (npc_id→alive/dead, absent=alive, session.py:90)
    + событие `NpcDied` (events.py:73, union, replay session.py:125) + `presence.is_dead`
    + DM-инструмент `kill_npc` (tools.py:479, валидация unknown_npc, шорт-кат already_dead,
    _TOOLS:774, master.py EFFECT_TOOLS:62) + гейт промпта: мёртвые в отдельный блок
    turn.j2:15 «Dead here (corpses — NEVER alive)», не в живых «NPCs here»
    (context.py _scene_view:504/506) + инструкция dm_system.j2:10/15 (move_npc/kill_npc
    в списке инструментов, «записывай смерть через kill_npc, не оживляй мёртвых»).
  - Frontend parity: events.ts NpcDied (324/390/449), event-feed.tsx case (49),
    i18n ru/en game.events.npcDied (521).
  - Проверки Root (сам): offline-смоук `.local/run7/npc_death_offline.py` PASS
    (мёртвый severin исключён из живых, в «Dead here»; kill_npc валидирует; replay+JSON
    round-trip), `ruff check src`/`format --check src`/`mypy src` clean (130 файлов),
    pytest 819 passed/26 deselected, frontend `npm run lint`/`typecheck` clean. Дефектов нет.
  - **АКТИВИРОВАН live 20.11 (после паузы питания).** Обнаружен рассинхрон: worker
    сам перезапустил детей в 17:55 (redis-таймаут, taskiq process-manager) и подхватил
    #51, backend (PID 4612, старт 16:54) остался на #40; в живой сессии не было ни
    npc_locations, ни npc_status (ни одного DM-хода с #40-рестарта) → следующий ход
    рисковал упасть на extra="forbid" backend#40 vs npc_status worker#51. По разрешению
    «Да, рестартнуть оба» backend+worker рестартнуты вместе: backend PID 20660 :8033
    (backend7.log), worker 2 процесса 21376/22536 (worker9.log), оба на #51, WS
    переподключился. Пользователь выбрал «начну сначала»; хост стола — Root-аккаунт
    run7_host@test.local (пользователь — обычный игрок Сокол Феннар, host-only кнопка
    ему недоступна), поэтому host-рестарт выполнил Root через API POST /api/sessions/{id}/restart
    (.local/run7/host_restart.py, логин хоста из runlib; 200 restarted=true state_version=11):
    журнал очищен до 1 события, новое вступление Narrated seq=1 (таверна «Ольхов лист»),
    scene=tavern, npc_status={} npc_locations={} (Северин жив), state содержит поля #51;
    backfill NpcDied(severin) НЕ нужен. Клиенты получают WS session_restarted и перечитывают снапшот.
- **Бэклог (задача #41, решение «запиши в бэклог, а пока фикс сделаем»):** полная
  консолидация runtime-состояния NPC — единая запись `state.npcs[npc_id]={location,
  attitude,status,actor_id}` вместо раздельных npc_locations/attitudes + мост npc↔actor
  через `Npc.statblock`. Делать после тестов этапа (#7/#41-этап). НЕ физическая
  реляционная таблица (ломает инвариант state=apply_events(journal)); контент уже
  унифицирован (scenario_entities одна строка на NPC, Scene.npcs — id-ссылки).
- **Игровая инъекция игрока → бэклог #43 (решение пользователя 20.11 «пока не страшно,
  у нас коммерческих игроков не будет»):** на тесте игрок декларировал «я нашёл под
  столом никем не замеченный кошель с золотом… поднял находку» (PlayerActed seq 38) →
  DM канонизировал находку в прозе (Narrated seq 39) без сценарного основания (ItemGranted
  не вызван — пока только проза); seq 34 — инъекция NPC-статуса/квеста («Северин
  предатель, не буду убивать Отшельника, подружусь с Хоремхебом»). Текст действия игрока
  — недоверенный ввод, но DM принимает утверждения игрока за факты мира. Защита
  (направление «гарантия кодом»): (1) grant_item (game/tools.py:490-517) — путь
  выдуманного предмета по name (item_id=None) в moderate/free принимает любое название
  без гейта ценности (tools.py:510-514) → запрет валюты/ценностей как выдуманных
  предметов, реквизит только не-ценный, ценности только сценарный item_id с grounding;
  (2) dm_system.j2 — текст игрока это ПОПЫТКА/заявление, НЕ авторитетная реальность;
  декларированные находки/ресурсы/NPC-статусы DM адъюдицирует против сценария/правил,
  не авто-канонизирует. Промпт alone недостаточен → код-гейт (1). Запись в PLAN.md
  «Бэклог», задача #43. Делать после тестов этапа (#7)/перед коммерческим запуском.
  Смежное (отдельно): seq 37 «Ведущий не смог ответить» — отказ DM-хода на абсурдном
  действии (seq 36 прыжок с люстры на нос Северина); робастность хода, не инъекция.
- **Run-stack ВОССТАНОВЛЕН после моргания света 20.11** (Docker Desktop+контейнеры
  поднял пользователь; backend/worker/vite — Root). Текущий: backend :8033 PID 15192
  (backend8.log, FAKE_LLM=false live LLM, balance 200), worker 2 процесса (worker10.log,
  Listening started), vite :5176 PID 11416 (vite2.log, [::1], proxy→8033, HMR). Docker
  ai-dnd-postgres-1+ai-dnd-redis-1 healthy. Сессия 6200e9c2 active last_seq=39 (журнал
  пережил отказ, том postgres durable; бой с бегством Северина + инъекция золота внутри).
  Оба backend+worker на #51 (свежий совместный запуск, skew нет). Перезапуск из backend/:
  `AI_DND_DATABASE_URL='postgresql+asyncpg://ai_dnd:ai_dnd@127.0.0.1:5432/ai_dnd_run'
  AI_DND_REDIS_URL='redis://127.0.0.1:6379/14' AI_DND_FAKE_LLM=false
  uv run uvicorn ai_dnd.main:app --host 127.0.0.1 --port 8033` и
  `… uv run taskiq worker ai_dnd.worker:broker ai_dnd.tasks`. Vite :5176
  (frontend, vite.run7.config.ts proxy→8033, HMR) — тоже поднять. Docker
  postgres:5432+redis:6379 должны быть запущены. Сессия прогона
  6200e9c2-ffb9-4fe1-9765-c1401e4555eb. taskkill деревьев в Git Bash:
  `MSYS2_ARG_CONV_EXCL='*' taskkill /F /T /PID <uv-launcher>`.
- **Коммитов НЕТ.** Тесты отложены до работающего прототипа; долг: formal tests
  NpcDied/kill_npc/prompt-gate (в этапный тест-проход). Не мутировать живую БД и не
  рестартить стек без разрешения пользователя.
- **Фикс CI ruff (задача #45, ПРИНЯТ Root 21.11).** CI падал на `ruff check`:
  tracked scratch-драйверы `backend/.local/*.py` линтились (gitignore кроет только
  корневой `/.local/`, не `backend/.local/`) + миграция alembic 0015 без trailing
  newline (W292). Фикс: `extend-exclude = [".local"]` в `[tool.ruff]`
  backend/pyproject.toml (scratch исключён из check+format; mypy уже игнорил через
  files=["src","tests","alembic"]) + перенос строки в
  backend/alembic/versions/20261120_1200_0015_user_ui_settings.py. uv.lock не тронут.
  Гейты Root: ruff check exit 0, format --check 201 files ok, mypy 201 files ok.
  **Scratch untrack выполнен 21.11 по команде пользователя:** `.gitignore` дополнен
  `/backend/.local/`; `git rm -r --cached backend/.local` снял 5 драйверов с трекинга
  (staged D, файлы на диске целы, check-ignore подтверждает). Коммит НЕ сделан
  (отдельное решение пользователя). `extend-exclude=[".local"]` в ruff оставлен как
  явный поясняющий фильтр (gitignore теперь и так исключает их из ruff).

## 20.11 — аудит размера промптов ведущего

- [Отчёт с замерами и критериями сравнения](reviews/2026-11-20-prompt-size-review.md).
  Контролируемый офлайн-замер в стартовой сцене без истории и результатов поиска.
  Полное планирование с доступным поиском занимает 25 435 символов в русском
  «Миллфорде» и 40 129 в «Пяти хранителях».
- Первые кандидаты — компактная сериализация JSON и однократная передача действий.
  Разделение инструкций планировщика и рассказчика требует сравнения на модели.
  Аудит завершён. Изменений активных промптов и платных прогонов в рамках аудита нет.

## 20.11 (поздно) — #33 поражение+рестарт и #34 стиль дайсов: КОД принят, live-проверка ожидается

- **#33 TPK-поражение + рестарт (КОД принят Root, backend+frontend).** Решения пользователя:
  рестарт только хостом; тот же стол со сбросом игры (полное HP, стартовая сцена, чистый
  лог, новый opening); поражение = все герои без сознания/мертвы (fled ≠ поражение).
  - Backend: `PartyDefeated` event (game/events.py:104, union, render ru/en маркер,
    ModelLogRenderer None); `party_wiped` (rules/combat.py:109); defeat-финал в
    combat_message (context.py:346, ended+defeated → scenario FAILURE conclusive phrase,
    не hand-back); master.narrate_combat коммитит PartyDefeated после финального Narrated
    (master.py:754); `restart_session_host` (service.py:671, host-only, non-blocking
    room-lock→409 session_busy, with_for_update, DELETE GameEventRow + last_seq=0 +
    SeatReadRow.last_seen_seq=0, пересборка актёров из SeatRow.character_id с полным HP,
    new_session_state, summary=None, state_version++, status=active, redis pending/window
    clean, sync_knockout_mutes, broadcast session_restarted, dm_open_session.kiq);
    POST /api/sessions/{id}/restart (router.py:482, RestartSessionResponse).
  - Frontend: events.ts PartyDefeated+STATE_EVENT_TYPES; socket session_restarted →
    сброс ленты/lastSeq/nonce (use-session-socket.ts:192); game-page derived defeated из
    ленты + финальный Narrated (:373), restartMutation host-only с 409 busy (:458),
    DefeatDialog (game/defeat-dialog.tsx NEW: финал + «Покинуть стол» все / «Начать
    сначала» host-only, dismiss в баннер); event-feed маркер; i18n game:defeat.*;
    schema.d.ts перегенерирован (только +/restart).
  - Проверки Root: backend ruff/format199/mypy129/pytest819 ✓ + смок TPK/restart OK;
    frontend typecheck0/lint0/build ✓, i18n 794/794, demo/Dice.tsx mtime прежний.
- **#34 стиль дайсов в настройках (КОД принят Root).** 6 стилей готовы для прода
  (amber/jade/amethyst/gold/malachite/ruby; 2D demo.css палитры уже импортированы +
  3D COLORSETS dice-3d.tsx). stores/settings.ts: diceStyle+setDiceStyle (persist
  aidnd.settings, default amber, старые localStorage не ломаются); roll-card.tsx:86
  appearance={diceStyle} → DiceRenderer → 2D/3D; ProfilePage карточка-пикер (6 d20-превью,
  radiogroup, data-testid dice-style-*); i18n profile:diceStyle.* (Янтарь/Нефрит/Аметист/
  Золото/Малахит/Рубин). Проверки Root: typecheck0/lint0/build ✓, i18n 801/801, demo не
  тронут, persist-смок OK. Долг: кросс-девайс синхронизация (только localStorage).
- **Начало игры (ответ пользователю):** opening генерируется АВТОМАТИЧЕСКИ при создании
  стола хостом (dm_mode=ai → dm_open_session.kiq, service.py:217), не по кнопке/заполнению
  мест; идемпотентно (есть Narrated → no-op, turns.py:533). Рестарт использует тот же
  механизм (журнал очищен → новый opening).
- **Дальше:** объединённый живой перепрогон TPK (рестарт run-стека с новым кодом —
  разрешение пользователя + платный прогон, бюджет today $0.160/чекпойнт $0.5): проверить
  #32 полноту mandatory-outcomes на плотном бою, #33 defeat-финал+PartyDefeated+диалог+
  рестарт (чистый лог/новый opening), #34 браузерный смок пикера дайсов. #32/#33/#34 НЕ
  закрыты до live-проверки. Коммитов НЕТ.

## 20.11 (поздно) — #32 фикс полноты нарратива боя: перепрогон, ЧАСТИЧНЫЙ успех

Жалоба пользователя: «ведущий упускал удары по волкам». Аудит run7 подтвердил
СИСТЕМАТИЧЕСКИЙ пропуск ударов/убийств отряда в боевом нарративе. По правилу
(feedback-narration-fidelity) — фикс + платный перепрогон (пользователь одобрил «да»).

- **Фикс ПРИНЯТ (код).** `game/context.py`: combat_message (363-382) — инструкция
  полноты («Account for EVERY attack… Every DamageDealt and every ActorDied MUST be
  reflected… completeness takes priority over brevity», hit/miss fidelity, запрет
  выдуманных смертей/счёта/террейна, группировать можно только промахи); narration_message
  (234-236) — «Reflect every committed outcome that changed state». render.py/master.py
  НЕ менялись (пайплайн и так отдаёт полный лог). Проверки Root: ruff src/tests/alembic ✓,
  mypy 199 ✓, pytest 819 passed / 26 deselected ✓ (снапшоты промптов не ассертят).
- **Перепрогон (wolf_thicket, 5 волков, отряд выбит TPK).** Стек перезапущен с разрешения
  пользователя. Аудит 3 окон нарратива против механики:
  `.local/demo-playthrough-2026-11-20/run7-narration-rerun-audit.txt` (+ run7-play.log).
  - **#446 (итог боя, 2 удара с уроном) — PASS:** оба удара волков по Мире (9+9, нокаут)
    озвучены индивидуально, счёт «4 волка / 3 тела» верен, поражение верно, галлюцинаций нет.
  - **#431 (раунд 2) — частично:** убийство wolf_7 Мирой озвучено ВЕРНО (ActorDied),
    счёт «4 волка» верен; НО крит wolf_10 по Мире (10 урона) ОПУЩЕН.
  - **#390 (раунд 1, плотный) — частично/провал:** удар Миры по wolf_7 озвучен, но как
    ЛОЖНОЕ убийство (wolf_7 hp=1, жив, атакует далее; ActorDied нет) → счёт «4 волка»
    занижен (живы 5); криты волков по Брану (14) и Тени (15+7) НЕ озвучены как атаки.
- **Вывод.** Исходная жалоба (удары ОТРЯДА по волкам опускались) — ИСПРАВЛЕНА: удары/
  убийства Миры теперь озвучиваются во всех окнах (в run7 Mira 4/4 опущена). Чистые окна
  идеальны. ОСТАТОК на ПЛОТНЫХ раундах: входящий урон по отряду sometimes теряется +
  единичная галлюцинация смерти/счёта. Пайплайн подтверждён полным (master.py:691 →
  ModelLogRenderer render.py:327, все DamageDealt/ActorDied в логе; counts/participants/
  terminal_events авторитетны) → остаток = compliance DeepSeek на длинном логе, не обрезка.
- **Итерация 2 ПРИНЯТА (код, по решению пользователя).** Двухуровневая полнота:
  смерти/потеря сознания/урон/лечение — обязательны поштучно; промахи сжимаемы в одну
  фразу («ни один удар героев не достиг цели»). Механизм «условно, при длинном логе»:
  обязательные факты вычисляются КОДОМ из событий окна и подаются отдельным компактным
  блоком `# Mandatory outcomes` (master.py:758 `_mandatory_outcomes` → context.py:365).
  На длинном логе блок остаётся маленьким (только state-change), промахи в него не входят
  (`# Misses in this log: N`). Инструкция (context.py:368-391): художественная проза,
  NEVER list; mandatory индивидуально «no matter how long the log»; промахи в одну фразу;
  fidelity-гарды (смерть только DIED, unconscious только downed, hit/miss, authoritative
  counts). consequence урона: killed (ActorDied после урона) / downed unconscious (hp→0) /
  wounded. Проверки Root: ruff ✓ / format 199 ✓ / mypy 129 ✓ / pytest 819 passed 26
  deselected ✓; offline-смок `.local/smoke_mandatory.py` OK (4 урона+DIED+Healed, misses=3,
  unconscious без урона отдельно). Файлы: только master.py + context.py; render/движок/
  frontend/demo не тронуты.
- **Перепрогон итерации 2 ожидается:** нужен рестарт run-стека с новым кодом (разрешение
  пользователя) + свежий плотный бой (сессия 6200e9c2 закончилась TPK). Бюджет: today
  $0.160104, чекпойнт $0.5. #32 НЕ закрыт до перепрогона.
- Стоимость перепрогона: today $0.160104 / 44 вызова (чекпойнт $0.5 не достигнут).
  Run-стек (:8033 backend bg b37zabzxr + worker db14 bg b86o3j6w6, ai_dnd_run) жив;
  сессия 6200e9c2, отряд unconscious (TPK). Cleanup по решению. Коммитов НЕТ.

## 20.11 (ночь, поздняя) — классы+скиллы: Фазы 1–3 ПРИНЯТЫ, live-прогон run7 PASS

Рефактор по решению пользователя (dataclass классов+скиллов, честные FK, без
текстовых конструкций) + первый набор скиллов: heal (cleric), stealth (rogue),
protect (fighter). Задача #27, фазы #28/#29/#30.

- **Фаза 1 (#28) ПРИНЯТА.** NEW `game/skills.py` (HealSkill/StealthSkill/ProtectSkill,
  discriminated union по kind; CharacterClass; `load_classes_and_skills` валидирует FK;
  `class_grants` — общий для консоли и web). NEW `content/skills.yaml` + `content/classes.yaml`
  (fighter→protect, rogue→stealth, cleric→cure_wounds, ranger→()). `Character.class_id`
  (верхний уровень data JSONB; legacy None → пустая выдача). `Actor.skills`,
  `PlayerSeat.skills`+`cure_wounds`. `content/characters/cure-wounds.json` УДАЛЁН.
  Web join_session (realtime/service.py ~287) и scenarios/store.py выдают capability
  через class_grants → **пробел cure_wounds из прогона #6 закрыт**.
- **Фаза 2 (#29) ПРИНЯТА.** Механика protect (решение пользователя): ДЕЙСТВИЕ в ход
  fighter вместо атаки (не реакция); до следующего хода защитника атаки по прикрытому
  союзнику идут **по AC защитника**, урон получает защитник (`AttackResolved.redirected_to_id`,
  target_id остаётся исходной целью). HideAction/ProtectAction в actions.py; редирект в
  engine._attack; hidden-условие в dnd5e-srd.json (преимущество своим атакам, помеха по
  скрытому; снятие после своей атаки); враги не таргетят скрытых (фолбэк: все скрыты →
  таргетят). Прикрытие спадает: ход защитника (expired), 0 hp/смерть от любого урона
  (protector_fell, централизованный `_fallen_protectors`), начало/конец боя (apply).
  Опции gated по actor.skills; подписи ru/en в turns.py+console.py. DM-инструменты
  protect/stealth (tools.py, EFFECT_TOOLS+actor-match в master.py): protect вне боя →
  combat_required (основной путь — кнопка), stealth вне боя работает; дефолтная цель
  protect — самый раненый СТОЯЩИЙ союзник. Промпты dm_system/turn/decision: секция
  Class skills + запрет нарратива без committed outcome (guard context.py НЕ тронут).
  render.py: 🛡 логи протекции + «перехвачено».
- **Независимая проверка Root (Фазы 1+2):** ruff/format(201)/mypy(199, полный scope)/
  lint-imports(3 kept) ✓, **pytest 819 passed / 26 deselected**; offline-смоки:
  `.local/class_skill_foundation_smoke.py` PASS, `.local/skills_engine_smoke.py` 7 проверок
  PASS, `.local/skills_dm_tools_smoke.py` (NEW, Root) 7 проверок PASS на реальном
  five-guardians. Единственная правка сьюта: conditions 14→15 (test_rulesets_service).
- **Фаза 3 (#30) ПРИНЯТА.** schema.d.ts перегенерирован offline (`create_app().openapi()`
  → openapi.json → gen:api); правила/события не в OpenAPI → рукописное зеркало
  `api/events.ts` сверено с backend-моделями: ActorState.skills/protected_by (опциональные),
  AttackResolved.redirected_to_id, ConditionRemoved.reason+="attacked", ProtectionStarted/Ended
  в union + STATE_EVENT_TYPES + COMBAT_REFETCH_TYPES. event-feed: 🛡 записи прикрытия
  (expired/protector_fell) + суффикс «перехвачено». combat-panel: быстрая кнопка
  «Прикрыть самого раненого» (min hp по rules.actors, data-testid=combat-quick-protect),
  иконки EyeOff/Shield + класс combat-option-skill, подсказка «Вас прикрывает X»
  (combat-covered-hint). Стили в существующем game.css. i18n: 7 ключей ru/en, паритет 0.
  Root-проверка: typecheck 0 / lint 0 / build ✓ (только пре-существующий chunk-warning);
  demo/* и Dice.tsx не тронуты (mtime 19.11). Мелочь на будущее: лейбл protect/attack
  уже содержит имя цели, генерический суффикс «→ имя» дублирует его (пре-существует).
- **Live-прогон run7 (#31) PASS.** Run-стек перезапущен с разрешения пользователя
  (:8033 backend + worker db14, ai_dnd_run, FAKE_LLM=false); scratch-vite :5176
  (proxy→:8033, config frontend/vite.run7.config.ts — удалить после приёмки).
  Сессия 6200e9c2 (five-guardians ru), публичная (recruiting=true, 4 места);
  пользователь вошёл наблюдателем через зал. Бой wolf_pack (5 волков, 8 раундов,
  партия победила): **heal** — слот spell_slots_1 −1, 1d8+6=10, Healed (Бран 3→13),
  пробел #6 закрыт; **stealth** — ConditionApplied hidden, волки не таргетят,
  атака из скрытности advantage 2d20kh1 (crit), ConditionRemoved reason=attacked;
  **protect** — ProtectionStarted ×5 / ProtectionEnded expired ×5 на старте хода
  Брана, Мира 27/27 за бой, опции gated по skills. Перехват (redirected_to_id) и
  protector_fell — offline-смок 7 PASS; live перехват не выпал (тактика волков
  «бей самого израненного» → все грызли Брана). Стоимость сессии $0.085232 / 36
  вызовов, сегодня $0.148908 (чекпоинт $0.5 не достигнут). Отчёт:
  `.local/demo-playthrough-2026-11-20/run7-skills-verdict.md` + run7-play.log.
- **Находка run7: устаревший ruleset в БД.** Web-воркер грузит ruleset из
  `rulesets.data` (turns.py:634), не из файла; строка dnd5e-srd@1 в ai_dnd_run
  засеяна до добавления hidden → «Спрятаться» не появлялось. Фикс: UPDATE rulesets
  data из текущего content/rulesets/dnd5e-srd.json (рестарт не нужен). Операционно:
  при изменении content/rulesets существующие БД ресевать/обновлять; прод ai_dnd
  при деплое — ресев. В бэклоге PLAN.md.
- Тестовый долг #41: механика protect/stealth, DM-инструменты, подписи, промпты — пока
  только разовые смоки; 2d6-атаки монстров не достигнуты в #6.
- Коммитов НЕТ (HEAD f458c8e далеко позади дерева — вся stage 2/3 работа не закоммичена).
  Стек прогона #6 (:8033/воркер/ai_dnd_run/redis db14) оставлен живым; cleanup по решению.

## 20.11 (ночь) — финальный реальный LLM-прогон #6 «Пять Стражей» ЗАВЕРШЁН

Изолированный web-стек: БД `ai_dnd_run`, redis db14, backend :8033 (bg), отдельный
воркер (bg), `FAKE_LLM=false` (реальный DeepSeek), billing fuse $0.60. Защищённые
:8030/воркер/portal-стек НЕ тронуты. Сессия `d62a6bf3-469d-4175-9597-fc5ff46140a1`,
five-guardians.ru (private, сид в run-БД). Драйвер `.local/run6/runlib.py`.

- **Стоимость: $0.063676 / 16 llm_calls** (session=today=total). Идеал ≤$0.3 выполнен
  с большим запасом; checkpoint $0.5 / fuse $0.6 НЕ достигнуты → пауза не требовалась.
- **Маршрут:** tavern → wolf_forest → wolf_pack (5 волков). **G1 вживую PASS:**
  инициатива волков первая → `_advance_combat` авто-доводит очередь → стоп на
  party-актёре → `GET /combat-options` your_turn=true → `POST /combat-actions` 202
  (не 409). Бой завершён (CombatEnded winner=enemies, Мира отступила).
- **Поражение в бою (нокаут) PASS:** Бран hp 0/28 на 4-м раунде (wolf_3 bite 2d4+2=7)
  → unconscious, `mute_source=knockout`; `POST /actions` → 403 muted, `POST /chat` →
  201 (чат разрешён), `combat-options` your_turn=false options=[], ход unconscious
  авто-пропущен. Мира убила wolf_4/5. Multi-dice runtime: урон волков 2d4+2 отработан
  (2d6-атаки монстров ещё не достигнуты — покрыть в #41).
- **Качество DeepSeek на five-guardians:** отличное, верно dm_notes сценария.
- **НАХОДКА (web-пробел cure_wounds):** `realtime/service.py` join_session (~286)
  строит `PlayerSeat` БЕЗ `cure_wounds`; консольный `game/content.py` load_party (~58)
  выдаёт capability из `content/characters/cure-wounds.json` (ключ — имя файла архетипа
  «cleric»). Web-жрец (#22) не может лечить: попытка «Лечение ран» на нокаутированном
  Бране → DM корректно рассказал «молитва ушла впустую» (context.py:235-236: нет
  committed outcome → не нарратить лечение). Восстановление из нокаута в web-бете
  недоступно. `_normalize` (sheets/compiled.py:232) отвергает лишние ключи values →
  id класса нельзя дописать в values; текстовый reverse-map хрупкий.
- **РЕШЕНИЕ ПОЛЬЗОВАТЕЛЯ 20.11:** не патчить хрупкими текстовыми ключами. Будущий
  рефактор — dataclass классов персонажей + dataclass скиллов, привязка скиллов к
  классам через честные FK (записано в бэклог PLAN). Web-фикс cure_wounds отложен до
  этого рефактора. Прогон #6 как приёмка ЗАВЕРШЁН (все цели достигнуты).
- **Стек прогона оставлен живым** для инспекции (backend :8033 bg, воркер bg,
  ai_dnd_run, redis db14). Cleanup — по решению пользователя. Без коммитов/секретов;
  платные вызовы только в рамках одобренного #6.

## 20.11 (вечер) — батч дайсов/создания персонажа + фикс G1 ПРИНЯТЫ Root (#19/#20/#21/#22)

Все dev-блокеры финального прогона закрыты. Уведомление #19 потерялось при обрыве
интернета; Root верифицировал файлы+gates независимо и принял.

- **#19 G1 (боевой тупик NPC-first) — ПРИНЯТ.** `realtime/turns.py`: общий цикл
  `_advance_combat` (ходы существ → рассказ раунда → стоп на party-актёре с
  human-местом / конце боя; авто-действие при единственном варианте; тактика
  ИИ-спутника), работает из сохранённого состояния. `run_dm_turn` зовёт его сразу
  после старта боя этим ходом (NPC-first → очередь доходит до human) И при resume
  из сохранённого состояния. Guard `run_combat_step` (current!=actor → not_your_turn)
  сохранён, но очередь пред-доведена → `POST /combat-actions` = 202, не 409.
  Регресс-тест `tests/game/test_combat_autostart.py` (4 теста: NPC-first advance,
  stop-at-human, финальный рассказ после CombatEnded, resume-from-saved) PASS.
  Полные backend-gates: ruff/format/mypy/lint-imports ✓, **pytest 819 passed / 26
  deselected**. Тест — targeted G1-guard (согласовано с решением 18.11 «тесты
  субагентам»), не промежуточные фиче-тесты.
- **#20 клик-d20 — ПРИНЯТ ранее.** zustand `stores/dice-reveal.ts` (revealed/rolling
  per-seq, seedHistory no-op при refetch), `roll-card.tsx` pending/rolling +
  deferred-mount DiceRenderer, `event-feed.tsx` гейт на свой single-d20. Донор не тронут.
- **#21 d6/d8 + клик-урон — ПРИНЯТ.** NEW `game/dice-2d.tsx` (стилізованные
  per-sides SVG d4/6/8/10/12, generic hexagon d100/exotic; single d20 → донор
  `demo/Dice.tsx` байт-в-байт; мультидайс = N клонов). `dice-3d.tsx` обобщён на
  `DieSpec[]`: `notationFor` → `NdS+NdS@v1,v2,…`, верификация КАЖДОЙ грани
  (sides:value по порядку) → 2D-фолбэк при mismatch/WebGL (никогда неверная грань);
  `onSettled` на всех путях драйвит раскрытие; singleton-стейдж сохранён; d100/exotic
  вне 3D (`supports3D`). `dice-renderer.tsx` DiceRenderer({dice,valuelegacy,onSettled}).
  Гейт расширен на ЛЮБОЙ свой live DiceRolled с dice-term (`hasVisualDice`): d20
  проверки/атаки/инициатива AND d6/d8 урон/лечение AND мультидайс; чужие — авто.
  Gates ✓ (tsc/lint/build). Браузер-смок (изолированный temp-стек, FAKE_LLM, реальный
  бой 5+ раундов): d20-регрессия ✓, свой урон d8 по клику ✓ в 3D И 2D (server-exact,
  downstream держится до конца броска), NPC-урон авто ✓, history reload ✓ (0 ложных
  гейтов), invite join ✓. **Ограничение (бета, не дефект):** мультидайс 2d6 runtime
  не достигнут — оружие архетипов 1d8/1d6, sneak +2d6 только текст фичи; код
  поддерживает, покрыть в #41/финальном прогоне (2d6-атаки монстров). Cleanup
  верифицирован: temp-стеки/scratch-БД удалены, защищённые :8030/:8080/воркеры живы,
  фикстура ad834188… не тронута, без коммитов/платных вызовов/секретов.
- **#22 класс (4 архетипа) + раса — ПРИНЯТ.** `characters/router.py`:
  `CreateCharacterRequest.char_class` Literal(fighter/rogue/cleric/ranger) обязательный
  + `race` опциональный (max60, пусто → раса архетипа); `_archetype_values` грузит
  `content/characters/<lang>/<class>.json` (ru-фолбэк для отсутствующего en/ranger);
  язык из версии сценария (`scenarios/store.py version_language`); лист строится из
  архетипа (оружие/статы/доспех/заклинания) + name/race override. Frontend
  `characters-page.tsx` селекты класса/расы + payload + i18n `characters:class.*`/`race.*`
  + schema.d.ts regen. **Бонус-фикс:** `game/sheet-view.tsx` списки через
  `Object.entries(field.item)` (id из ключа записи; раньше sub.id=undefined →
  оружие/инвентарь рендерились прочерками). Gates ✓ (backend 356 chars/sheets тестов,
  frontend tsc/lint/build). Смок (изолированный temp-стек): 201 с оружием/статами/расой
  архетипа, EN-ranger фолбэк, 422 на неверный класс; защищённый стек не тронут.

**Ждёт решения пользователя:**
- **#6 финальный реальный LLM-прогон «Пять Стражей» — ЗАВЕРШЁН 20.11 (ночь), см. выше.**
  $0.063676/16 вызовов (идеал ≤$0.3 выполнен), G1/нокаут/DeepSeek PASS. Находка:
  web-пробел cure_wounds → отложен до рефактора дата-модели классов/скиллов (бэклог).
  Cleanup run-стека (:8033/воркер/ai_dnd_run/redis db14) — по решению пользователя.
- **#7/#41** тесты этапа 3 целиком — после прогона (вкл. 2d6-атаки монстров / multi-dice).
- Бэклог: P1 ревью 19.11 **G2/G3/G5/S3** (G1 закрыт), docker builder prune (~19.3GB
  кэш + ~28GB images), **дата-модель классов+скиллов с FK (решение 20.11, закрывает
  web-cure_wounds и сценарный блок «доступные классы»)**, редактор values листа.

## 20.11 — целостный Docker-стек: браузерная приёмка ЗАКРЫТА (#13), фикс #17

Стек `ai-dnd-portal` (env `.local/docker-test.env`, web :8080→nginx→backend:8000,
свежая изолированная БД `ai_dnd`, FAKE_LLM) поднят целиком; индексация сценария
завершена (chunks=32 embedded=32, O4 закрыт). Полный браузерный смок по плану
«регистрация→персонаж С АВАТАРОМ→стол→ход FAKE_LLM→чат→transfer/stop→лист» пройден:

- **Auth (demo):** register сразу verified, но сессию НЕ создаёт → экран «Проверьте
  почту» → «Вернуться ко входу» → login (demo пропускает email-гейт, пароль проверяется).
  Квирк понятен, не дефект (`identity/router.py` demo-ветка).
- **Персонаж + аватар (#16):** создание с пресетом и PATCH-смена аватара персистятся;
  picker работает. **Лист (#17, новый фикс):** поле «ПОРТРЕТ» показывало ✧, т.к.
  аватар хранится в `characters.portrait`, а не в `values`. Субагент добавил в
  `frontend/src/game/sheet-view.tsx` (SheetView, строки 72–79) overlay `sheet.portrait`
  на пустые image-поля template. Root: typecheck/lint/build PASS, web-образ пересобран
  (`build web` + `up -d web`, backend/db/redis не тронуты), вживую img
  `elf-ranger.png` naturalWidth=512 в шапке листа «Герой Игрока».
- **Лист #15/#14:** подписи спасбросков без слова «Спасбросок»; перекрытия нет —
  программный worst-case 6-col (forced `!important`, ячейки 63.46px, 0 overflow/0
  overlap) + скриншоты; мобильная 2-col вёрстка чистая (`game.css:81` media !important).
- **Стол/ход/чат:** создание стола, WS-посадка, OOC-чат в обе стороны, действие
  игрока → авто-ход → d20 + проверка восприятия + нарратив вживую через WS (кубик отрисован).
- **Выход хоста (#12):** transfer-host игроку (в БД host_id переключился, место старого
  хоста удалено, редирект в зал), новый ведущий видит «Пульт ведущего» + журнал/чат;
  stop при отсутствии игроков (БД stopped). Сессия смока `9c23d739…` остановлена.

Приняты #12/#14/#15/#16, закрыты #13/#17. Scratch-файлы `.local/p2*` (cookie-jar/тела
игрока 2) удалены. Остаток этапа 3 по плану: **3.8 наблюдатели (#5, frontend)** →
тесты этапа (#41/#7) → финальный реальный LLM-прогон $0.3 (#6, фокус поражение в бою;
нужно решение пользователя + рестарт). P1 из ревью 19.11 (S3,G1,G2,G3) и docker
builder prune (~19.3GB кэш + ~28GB images) — ждут приоритизации/разрешения пользователя.

## 19.11 — итоговое ревью проекта

[Отчёт о ревью веб-игры](reviews/2026-11-19-project-review.md) охватывает UI/UX, качество кода, безопасность и готовность этапа 3. Четыре P1 касаются изоляции клиентских данных между аккаунтами, первого хода NPC, конкурентной записи снимка и потери принятого окна действий. В отчёте приведены результаты проверок и порядок приёмки исправлений. Допуск закрытой беты остаётся открытым.

## Дополнение 19.11 — три оформления дайсов

- В `/demo` добавлены `gold`, `malachite`, `ruby` — золото, малахит и рубин.
- Реализованы SVG-материалы и соответствующие 3D-палитры.
- Применён workaround `updateConfig`/кэша материала при возврате к ранее использованному скину.
- Root: smoke 17/17 PASS; typecheck/lint/build PASS; независимое ревью — GO.
- 3D-материал уже брошенного mesh меняется на следующем броске.

## Возобновление 19.11 (вечер) — правки по директивам пользователя, работа идёт

Пользователь после паузы дал новые директивы и «продолжай по плану» (работа
фактически возобновлена активными указаниями). Дайсы (отдельный агент) завершены —
см. «Дополнение 19.11» выше, изолированы в /demo, нам не мешают.

НОВЫЕ ЗАДАЧИ (субагенты запущены параллельно, файлы не пересекаются):
- #15 Подписи спасбросков: убрать «Спасбросок»/«спасбросками» из 12 полей блока
  «Спасброски» в content/sheets/srd5-basic.json (save_*_prof → «Владение: X» /
  «X proficiency»; save_* → «X» род.п. / «X»). bindings.saves НЕ менять.
- #16 Выбор аватара — ОКАЗАЛСЯ НЕ РЕАЛИЗОВАН в реальном приложении: пресеты есть
  (frontend/public/images/avatars/*.png, 6 шт), AvatarImage есть, демо использует,
  но форма создания без пикера, карточки списка без аватара, row.portrait всегда
  None (POST без portrait, PATCH нет). game-page.tsx:606/460 уже показывают portrait
  когда задан. Фича: backend allowlist пресетов + portrait в CreateCharacterRequest
  (422 invalid_portrait) + PATCH /api/characters/{id} (owner-only); frontend
  avatars.ts + пикер (создание+смена) + аватар в карточках + i18n ru/en. Демо не трогать.

ВАЖНО ДЛЯ DOCKER (установлено расследованием):
- Лист отдаётся из БД sheet_templates.data (characters/router.py GET sheet), НЕ из
  файла напрямую. Контент сидится `console seed` → publish_scenario →
  _upsert_sheet_template (scenarios/store.py) читает content/sheets/*.json. Версия
  шаблона неизменяема: native ai_dnd_test сохранит СТАРЫЕ подписи srd5-basic@1;
  свежие получит только свежая БД при сидинге (или bump версии).
- content/ ЗАШИТ в backend-образ (backend/Dockerfile:57 COPY content /app/content):
  после правки JSON (#15) и backend-кода (#16) нужна ПЕРЕСБОРКА backend-образа.
- Контент НЕ сидится при старте (main.py lifespan — engine/redis/watcher). Свежую
  docker-БД засеять ЯВНО: `docker compose --env-file .local/docker-test.env exec
  backend python -m ai_dnd.console seed --scenario shadow-over-millford --lang ru`
  (сценарии: shadow-over-millford.ru/en, five-guardians.ru, well-of-forgotten-names.ru;
  ruleset dnd5e-srd.json). Индексация через воркер (эмбеддинги CPU, model-cache том);
  быстрый smoke — --no-index, целостный тест лучше с индексацией.
- Docker поднят: infra ai-dnd-postgres-1/redis-1 (127.0.0.1:5432/6379) — native infra;
  целостный стек ai-dnd-portal использует внутренние db/redis без публикации → без конфликта.

ОБНОВЛЁННЫЙ ПЛАН ПРИЁМКИ (#13): дождаться #15/#16 → Root ревью → пересборка
backend+web → `up -d --build` → healthy+миграции → СИДИНГ контента → браузерный
smoke :8080 (регистрация→персонаж С АВАТАРОМ→стол→ход FAKE_LLM→чат→transfer/stop→
лист: подписи спасбросков без «Спасбросок», без перекрытия #14) → принять
#12/#14/#15/#16, закрыть #13.

# ПАУЗА 19.11 (вечер) — компьютер выключен, продолжение по сигналу пользователя

Root остановил работу по просьбе пользователя («ставь на паузу, надо компьютер
выключить»). Фоновый фикс-агент остановлен. Docker-стек НЕ поднят. Dev-серверы
погаснут при выключении — на рестарт нужно НОВОЕ разрешение пользователя.

## Фактический статус задач
- #10 backend transfer-host/stop — ПРИНЯТ ранее; эндпоинты живы на :8030
  (openapi содержит /transfer-host и /stop, POST без cookie → 401, не 404).
- #11 вёрстка «сверху вниз» + заметная кнопка выхода — ПРИНЯТ ранее.
- Рестарт + CPU-пересинхрон (одобрены) — ВЫПОЛНЕН: torch 2.15.0+cpu, nvidia=0;
  backend :8030 health ok (db+redis), воркер taskiq работал. ВАЖНО: redis в .env
  задан как localhost → на Windows уходит в IPv6 ::1 и /health давал 503; фикс —
  запуск с `AI_DND_REDIS_URL=redis://127.0.0.1:6379/0` (только env запуска, .env не
  менялся). Команды запуска backend/воркера — в памяти project-dev-servers.
- #12 UI выхода ведущего (C-агент) — РЕАЛИЗОВАН, гейты typecheck/lint/build PASS
  (по отчёту C), 24/24 headless live-проверок. Отчёт: .local/host-transfer/ui-report.md.
  Root ревью кода почти завершён, дефектов нет: host-leave-dialog.tsx (radio→
  transfer-host, без игроков→stop), use-session-socket.ts (reconnectNonce форс-
  reconnect повышенного игрока, 4403 терминален для кика), game-page.tsx
  (handleHostTransferred/handleSessionStopped/confirmHostLeave, memo hostLeaveTargets,
  transferringRef сброс при seat.role==='dm', StoppedBanner, блокировка ActionForm/
  trigger-turn при tableStopped). НЕ сделано: повторный прогон гейтов Root + живой
  end-to-end transfer/stop + WS 4403 reconnect (необратимые мутации мест — только на
  scratch-столах, НЕ защищённая фикстура ad834188). #12 остаётся in_progress до приёмки.
- #14 фикс перекрытия подписей листа — ПРИМЕНЁН: game.css:66 `.sheet-view .sheet-label`
  получил `overflow-wrap: anywhere;` (чинит и модификаторы, и спасброски — правило
  общее). Агент остановлен после правки; гейты Root ещё не прогнаны (правка тривиальная
  CSS, безопасна). Отчёт агента может отсутствовать (.local/sheet-label-fix/).

## #13 целостный Docker-стек — подготовлен, НЕ поднят
- Env-профиль ГОТОВ: .local/docker-test.env (FAKE_LLM=true, DEMO_AUTH=true,
  ENVIRONMENT=dev, БД ai_dnd@db, redis@redis, WEB_PORT=8080, billing global $100/день,
  secret random, реальный DeepSeek-ключ НЕ используется). `docker compose --env-file
  .local/docker-test.env config` проверен (exit 0, все 5 сервисов).
- Backend-образ ПЕРЕСОБРАН: ai-dnd-portal/backend:local 2.25GB id 5101c9358729 (с правками A).
- Web-образ НЕ пересобран (нужен фронтенд C+#14). Стек изолирован: db/redis не
  публикуются наружу, свежий том pgdata (БД ai_dnd) — не задевает native ai_dnd_test
  и защищённую фикстуру. Build cache НЕ pruneить (нужен для сборки).
- КОМАНДЫ ПРОДОЛЖЕНИЯ (из корня репозитория):
  1. `cd frontend && npm run typecheck && npm run lint && npm run build` — гейты C+#14.
  2. `docker compose --env-file .local/docker-test.env build web` — web-образ с C+#14.
  3. `docker compose --env-file .local/docker-test.env up -d --build` — весь стек.
  4. Дождаться healthy (миграции через entrypoint backend), `docker compose ps`.
  5. Браузерный целостный smoke на http://localhost:8080: регистрация(DEMO_AUTH)→
     персонаж→стол→ход(FAKE_LLM)→чат→передача ведущего/выход→лист персонажа
     (модификаторы и спасброски НЕ перекрываются, #14).
  6. Живой transfer/stop + WS host_transferred/4403 reconnect — на scratch-столах
     свежей docker-БД (безопасно, необратимо только в ней). Принять #12/#14, закрыть #13.

## Отложено / ожидает решения пользователя
- Docker build-cache prune (~19.3GB cache + ~28GB неиспользуемых образов):
  `docker image prune -f` освободил лишь 59MB; реальный резерв — `docker builder prune`
  и/или `docker image prune -a`. Более широкие destructive-операции — нужно явное согласие.
- #5 frontend наблюдателей (3.8), #6 финальный LLM-прогон $0.3 (FAKE_LLM=false +
  billing 3000 + отдельный рестарт), #7 тесты этапа #41.
- Все изменения не закоммичены; commit/push только по отдельному указанию.

---

# Передача контекста — 18.11.2026

## Цель и порядок работы

- Цель — полное консольное демо `shadow-over-millford` на ru/en. 1.7 закрыт
  18.11: оба языка прошли сценарий от начала до сюжетного финала (RU `f4c0a41d…`,
  EN `09b2e375…`); P1 верности повествования принят пользователем как допущение.
  1.8 оценён 18.11: каталог 20 ситуаций покрыт полностью (offline 6/6 + прежние
  пробы + live 10/10 PASS), замеры стоимости готовы; 1.8 готов к закрытию.
  Затем этап 2 (данные, поиск, память), веб 3, кубики 3.5, карта 4.3.
- Root ведёт архитектуру, задачи и приёмку; разработка у субагентов. Terra high
  для простых задач, Sol high для сложных, Astra high для итогового ревью качества.
  В задачах задавать цель, файлы, ограничения и критерии; ошибки возвращать на
  исправление. При старте сверять доступные идентификаторы моделей с каталогом.
  18.11 (вечер): недельная квота Codex исчерпана, бэкенд продолжил Qoder; модели
  Terra/Sol/Astra в его сессии недоступны, по решению пользователя задачи идут
  general-purpose субагентам, сложный код и проверку прогонов Root делает сам.
  Codex позже продолжит начатое — не перезаписывать его незакоммиченные изменения.
- Тесты пишем по завершении этапа, покрывая этап целиком, не во время промежуточных
  подэтапов (чтобы не переписывать промежуточные тесты). До завершения этапа: review,
  ручные smoke, lint и типы; тестовый долг фиксировать. Существующие тесты и CI не удалять.
- Оценки сроков и вехи — `docs/ROADMAP.md` (пройденные этапы отмечать там и в PLAN).
- Все изменения не закоммичены; не делать commit/push без отдельного указания.
  18.11 появились отдельные изменения frontend (src/package и др.): эта ветка
  задач их не делала и не ревьюила; не откатывать и не перезаписывать.

## Реализовано и проверено

- Typed `TurnDecision` (`game/decision.py`, `game/master.py`): обязательное
  приватное JSON-решение через `gateway.complete`, проверка refs/actor, управление
  move/engage и разрешёнными эффектами кодом, затем отдельное повествование после
  событий и commit `Narrated`. JSON планировщика не публикуется в повествовании и истории.
- Последовательный ledger операций, pending commit и retry narration,
  нормализованная дедупликация checks/resources, continue/defer и максимум четыре
  planning-вызова за попытку. Остаётся семантический риск LLM stay/narration.
- Реализованы gated reveal, fresh scene, combat roster, вычисляемые из состояния
  и roster счётчики alive/dead/incapacitated/fled и локация, Jinja `member['items']`,
  started encounters, идемпотентный item, combat pending flag и CLI retry.
- Root/Astra offline code review, fake ru/en, fault smoke, ruff/format, mypy с
  приватным cache (97 файлов) и три import-контракта прошли. Это не full-game PASS.
- Typed Cure Wounds принят Root (Qoder) 18.11: `game/spells.py`, `game/tools.py`,
  `game/master.py`, `game/content.py`, `game/session.py`, `game/context.py`,
  `game/prompts/decision_system.j2`, `content/characters/cure-wounds.json`.
  Capability per seat из авторского JSON (cleric prepared, Disciple of Life,
  формула 1d8+6 из computed spell_mod, без парсинга prose). `cast_cure_wounds1`:
  только вне боя, party-only caster/target, prepared, дееспособность, слот;
  атомарные ResourceAction(-1)+HealAction в одном ToolOutcome/commit; цель с 0 HP
  допустима. `adjust_resource` запрещает positive HP и любые spell_slots_*
  (restriction и в валидации решения, и в инструменте). Повтор cast тем же input
  в сцене запрещён; caster должен совпадать с actor входа. Narration prompt
  запрещает описывать лечение/пробуждение без committed outcome.
- Проверки приёмки: offline smoke `.local/cure_wounds_offline_20261118.py` 8/8 PASS
  (exact EN batch local+cast+defer, zero-slot, invalid-target, caster-mismatch,
  repeat-cast, commit-fault retry без двойного списания/лечения, gates
  adjust_resource, narration guard), независимый прогон Root, лог
  `.local/cure-wounds-20261118/smoke-root-run1.log`. Ruff/format, mypy
  --no-incremental (69 файлов), три import-контракта — чисто. Live fixture
  `.local/cure_wounds_live_20261118.py` PASS: session
  `618baf23-7b4b-4d78-9c36-a9bee40ced6d`, 3 вызова/$0.007746 DB, 10/10 checks
  (slot 4→3, один heal 12, один бросок 1d8+6, unconscious снят, один cast,
  search после cast до narration, replay == snapshot, без error/pending).
  Артефакты `.local/cure-wounds-20261118/live_en_v1/`; Root сверил
  trace/journal/transcript. Голоса под полом в narration — сценарные (dm_notes
  old_mill), не выдуманный исход; P2-наблюдение. Astra-review не выполнялся
  (модель недоступна); приёмка Root.
- Офлайн-фикстуры 1.8 (D/F/G/H) из EN-журнала: `.local/quality_fixtures_18_20261118.py`
  reconstructит состояние сессии `09b2e375…` через `JOURNAL_EVENT` + `apply_events`
  и проверяет механические guardrail через ScriptedGateway (без сети/денег).
  6/6 PASS: Q14 heal на реальном seq247 (slot 4→3, heal 12), Q17 один check seq254,
  Q18 repeat reveal seq260 (already_revealed), Q20 collective move seq8 + defer,
  Q20b scene_not_reachable, Q09 combat_message seq151 (enemies dead=4/fled=0, guard).
  Ruff/format clean; артефакт `.local/quality-18-20261118/offline_results.json`.
  Каталог `docs/quality-situations-1.8.md` дополнен таблицей офлайн-покрытия.
  Семантический текст Q09/Q11 не asserted (принятое допущение); Q15/Q16/Q19 уже
  покрыты прежними пробами, не повторять.
- Замеры стоимости 1.8 (offline из `llm_calls`, без новых вызовов API):
  `.local/quality_cost_18_20261118.py`, артефакт
  `.local/quality-18-20261118/cost_metrics.json`. Полные RU/EN прогоны (13 ходов):
  cost/turn $0.008194 RU / $0.003324 EN; проекция часа игры при 12 ходах/ч
  ~$0.098 RU / $0.040 EN; доля кэша dm_tools 0.645/0.621, narration 0.487/0.439;
  reasoning=0, errors=0. RU дороже EN ~2.5× (больше input-токенов). Таблица в
  каталоге 1.8. Базовая линия кэша для 2.3 зафиксирована.
- Live-оценка поведенческих ситуаций 1.8 (18.11, бюджет одобрен «трать сколько
  нужно»): `.local/quality_live_18_20261118.py`, 10 ситуаций Q01–Q08/Q12/Q13 на
  replayed-фикстурах RU/EN-журналов через реальный DeepSeek. 20 вызовов/$0.011133
  DB, 10/10 механических PASS, 0 ошибок/pending, replay==snapshot везде. Root
  проверил семантику всех narrations: модель в роли, инъекции/запросы секретов
  отклонены, исходы проверок верны (Q02 perception 14/14→clue_symbol, Q03 sleight
  18/DC15). P2: Q03 кошелёк описан но не выдан; Q06 соц. прогресс без check;
  Q13 note_fact не использован (facts пусты — долг памяти этапа 2). FAIL B/F/G/H
  нет → dm_tools остаётся v4-pro, narration flash. Артефакты
  `.local/quality-18-20261118/live_v1/`; таблица и P2 в каталоге 1.8. Каталог
  покрыт полностью; 1.8 готов к закрытию. Готовые пробы не повторять.
- `backend/config/llm.yaml`: `dm_tools=deepseek-v4-pro`, temperature 0.2,
  thinking false; narration — `deepseek-flash`, temperature 0.3. Цены в config;
  DB округляет cost до шести знаков. Это оценка, а не счёт провайдера.

## Исторический полный ru 17.11: BLOCKED

- `.local/demo-playthrough-2026-11-17/protocol_full/`, session
  `39b87328-a86b-4148-843b-4def7c350a1d`: tavern → mill_road → old_mill,
  реальные `goblin_ambush` (4 раунда) и `mill_guards` (2 раунда), обе победы.
  HP fighter 1/28, rogue 21/21, cleric 27/27. 262 события, последний seq 262,
  replay == snapshot, 26 model calls, $0.022956 по оценке DB.
- На player turn 7, seq 260–262 содержат только `PlayerActed`: fighter входит
  через приоткрытую дверь мельницы, rogue осматривает внутри, cleric слушает;
  текущая сцена уже `old_mill`. JSON 1: `move(old_mill)` + defer/defer →
  `already_in_scene`. JSON 2: `move(old_mill)` + `find_trapdoor` tool + stay →
  validator запрещает move + tool. Это validation/representation deadlock,
  не выдуманный исход. На тот момент English full ещё не запускали.
- Процесс завершён через QUIT; продукт не восстанавливает RNG/history/pending
  после перезапуска. Не считать эту сессию возобновляемой.

## Узкий local move: offline принят, live fixture PASS

- Fix применён в `game/master.py` и `game/prompts/decision_system.j2`;
  изолированный production diff — `.local/local-move-offline-20261118/production.diff`.
  Root независимо принял offline-результат. Astra дала GO, defects 0;
  live-проба `local_enter_ru` прошла в пределах, указанных ниже.
- `move(current_scene)` записывается в ledger как `local_move` с refs/actor/scene,
  без нового `GameEvent`/`ToolUsed`, `SceneChanged` или `pending.moved`; потребляется
  обычный ref. `continue_after` сохраняет только различный остаток. Повтор того
  же local ref/scene запрещён. Same-scene tool рядом допустим.
- Реальный переход по-прежнему начинает fresh phase, коллективно даёт один
  `SceneChanged`; local и real move не объединяются. Local не означает автоматический
  unlock или успех опасного check. Сохранены защиты all-defer/no-progress/reroll,
  engage+effects и максимум четырёх planning-вызовов.
- Offline smoke: 15/15 PASS (`.local/local-move-offline-20261118/smoke-final.log`):
  точные JSON 1/2, local+tool, failed check/gated reveal, refs/continuation/dedupe,
  fresh real scene, no-progress/all-defer/reroll/engage+effects и four-call retry.
  Root независимо проверил exact JSON 2 с отказом narration и retry: без повторного
  check/local step; ledger доступен planner и narrator, но не публичной history;
  без ToolUsed/SceneChanged от локального шага,
  replay совпал со snapshot. Ruff/format и mypy прошли на 98 файлах (mypy с
  приватным ignored cache smoke); root также проверил ruff и три import-контракта.
- Первое smoke-предположение `state == STATE` не учло обычный increment turn при
  Narrated; исправлен только smoke. Исходный отказ сохранён в
  `.local/local-move-offline-20261118/smoke-initial-assertion-failed.log`.
- Astra: 15 smoke и три независимые безфайловые проверки PASS: точный JSON 1
  от seq 259; real → distinct local → повтор rejected → repair на четвёртом
  вызове; no-event continue+defer. Replay совпал со snapshot.
- Live `local_enter_ru` PASS на исходных inputs и истории до seq 259: session
  `780a0bb9-d7e6-434d-9223-dbd89d185410`, два вызова 189–190, $0.010762 DB;
  семь непрерывных событий, replay == snapshot, error/pending null, old_mill без
  combat/SceneChanged. Решение stay + tool(find_trapdoor) + stay: check 14/DC 12,
  clue revealed seq 6, Narrated seq 7. Root сверил trace/journal/transcript.
  Модель не выбрала move(current_scene): live подтверждает отсутствие прежнего
  deadlock на этих inputs/history, сам local branch подтверждён offline. Артефакты:
  `.local/demo-playthrough-2026-11-17/protocol_probes/local_move_20261118_v1/model_default/local_enter_ru/`.
- Готовые пробы не повторять. Результаты новых полных прогонов и следующий
  blocker приведены ниже.
- Готов `.local/demo_probes.py local_enter_ru --output-tag <NEW_TAG>`; fixture —
  в `protocol_probes/fixtures/`. `.local/demo_runner.py` уже содержит complete
  wrapper, decision trace, retry, `--output`, `--max-calls`, `--max-cost` и защиту
  непустого output. Запускать из `backend` через `.venv/Scripts/python.exe` и
  `../.local/...`; helpers не переписывать, failures не перезаписывать.
- PTY ломал JSON длиннее 511 символов: использовать <350–400 или file queue.
  При блокировке экспортировать/сохранить pending diagnostic и session ID;
  не делать quit без решения root.

## Полные прогоны 18.11 и следующий blocker

- Первый RU `087dfd14-16b3-46a7-87db-8a90ff847d78` INTERRUPTED: runner
  исчез, исходные summary/journal/trace содержали NUL; причина не установлена.
  Docker Desktop и БД восстановлены, volume сохранён. Salvage в новом
  `local_move_full_salvage/`: 273 события, replay == DB snapshot, 27 calls,
  $0.051410 DB; root независимо проверил. Turn 8 завершился: Revealed cellar_key,
  ItemGranted fighter, Narrated seq 271–273. RNG/history процесса потеряны,
  продукт не может возобновить эту сессию. Исходные файлы не перезаписывать.
- Новый retry в `.local/demo-playthrough-2026-11-18/local_move_full_retry/`:
  RU `f4c0a41d-ab0f-422d-8e4f-0d46f5c801f7` достиг сюжетного финала: 445
  последовательных событий, replay == DB snapshot, 45 calls/$0.106528 DB,
  три победы; жрец Змея погиб на втором раунде финального боя и ритуал сорван,
  освобождены пленники, получено письмо, группа вышла. Astra принимает сюжетное
  завершение, но не полное качество: P1 Cure Wounds дал +8 HP и снял unconscious
  без расхода слота и броска лечения (seq 248–250).
- EN `7023b306-ec6a-461c-9991-ba239b27ffc2` BLOCKED (исторический): 250
  последовательных событий, replay == DB snapshot, 23 calls/$0.037830 DB.
  Narration seq 153 выдумал побег гоблина при четырёх ActorDied и нуле ActorFled
  (P1, риск верности повествования остаётся открытым для 1.8). На turn 6 два
  schema-valid решения повторили input ref 2 для стоимости слота и лечения
  (refs [2,2,0,1]); оба отклонены проверкой coverage. 18.11 вечером проверено:
  runner PID 9664 мёртв, pending/RNG не восстановимы; сессия не возобновляема.
  Вместо retry выполнен изолированный live fixture на тех же входах seq 248–250
  (см. раздел приёмки Cure Wounds выше) — PASS.
- Typed Cure Wounds реализован и принят (см. «Реализовано и проверено»);
  прежний blocker compound healing закрыт. Новый полный EN-прогон с исправленным
  лечением выполнен 18.11 (см. ниже) и достиг финала. Повторный полный RU не
  нужен: прежнее лечение P1 закрыто фиксом и подтверждено EN-прогоном и fixture.
- Полный EN-прогон 18.11 `09b2e375-4dc0-459e-a326-c38f5b2c7bce` достиг сюжетного
  финала: 406 последовательных событий, replay == DB snapshot, 43 calls/$0.043213
  DB, три победы (goblin_ambush, mill_guards, ritual), 0 ошибок/retry. Маршрут
  tavern → mill_road → old_mill → cellar; освобождены пленники, получено письмо
  cult_letter, группа вышла. Cure Wounds сработал корректно (один heal, слот
  списан, unconscious снят). Артефакты
  `.local/demo-playthrough-2026-11-18/cure_full_en_v1/` (summary/journal/transcript/
  decision_trace + driver jsonl/stderr). Root независимо проверил журнал и trace.
  P1 выдуманного побега гоблина повторился (seq 153) — принят как допущение (ниже).
  P2: лишний pick_lock при уже полученном cellar_key; note_fact не использован,
  facts пуст. Это не блокеры сюжетного завершения.
- Решение пользователя: повествование о бое, включая его финал, остаётся за LLM: сухой автоматический результат хуже периодической ошибки. Выдуманный побег при нулевом `ActorFled` — признанный пользователем риск качества прототипа, а не исправленный дефект; игровое состояние и события остаются авторитетом движка. Не заменять модельное описание детерминированным итогом; специальное исправление этого эпизодического повествовательного риска не является текущей задачей. Готовые пробы не воспроизводить; P2 с picklock при ключе и пустыми facts этим решением не закрыты.
- P2: лишний picklock при ключе, перевёрнутая чаша без action, неподтверждённые
  голоса за дверью после выхода, пустые facts (note_fact не используется).
  Видимый cellar_key — P2, не P1. 1.7 закрыт; 1.8 открыт (каталог принят).

## Этап 2: 2.1 хранение в базе (18.11, готов)

- Миграция 0004 (head): таблицы `rulesets`, `sheet_templates`, `scenarios`,
  `scenario_versions`, `scenario_entities`, `characters`; модели per-package
  (`scenarios/models.py`, `rulesets/models.py`, `sheets/models.py`,
  `characters/models.py`), реестр `db/models.py`. `game_sessions` получил
  `scenario_version_id`/`ruleset_id`/`sheet_template_id` (FK, nullable),
  `state_version`, `summary`. `author_id`/`owner_id` nullable без FK (users — этап 3).
  Alembic check без расхождений, downgrade/upgrade проверены.
- `scenarios/store.py`: `publish_scenario` (YAML→БД, валидация через
  `load_game_content`, upsert ruleset/template по name@version, новая неизменяемая
  версия number+1, сущности по строкам), `load_game_content_from_db` (version
  pinning, детерминированный порядок сущностей по key), `resolve_current_version`,
  `version_refs`. `DbJournal.create` принимает version refs (SessionSetup не тронут).
- Консоль: `play --from-db` (игра по опубликованной версии из БД) и команда `seed`
  (публикация YAML в БД). `--from-db` требует базу (не совместим с `--no-db`).
- Проверки: offline smoke `.local/content_store_smoke_20261118.py` 9/9 (round-trip
  ru/en == file load, ruleset@1/template@1 pinned, v1 неизменна после v2, current=v2);
  console smoke `.local/console_db_smoke_20261118.py` 8/8 (fake-LLM play --from-db,
  сессия привязана к версии, после публикации новой версии старая сессия остаётся на
  своей версии и её контент загружается). Артефакты `.local/content-21-20261118/`.
  Ruff/format, mypy --no-incremental (74 файла, приватный cache `.local/mypy-cache-21`),
  существующие тесты 664 passed — чисто. Приёмка 2.1 выполнена.
- Bounded decisions прототипа: бестиарий/глоссарий (справочные SRD-данные) и партия
  (готовые SRD-персонажи) пока из `content/`; таблица `characters` готова, загрузка
  партии из БД — с появлением пользовательских персонажей (этап 3/5). Видимость
  сущностей public/dm хранится; фильтр видимости в поиске — 2.3.
- 2.2 индексация, 2.3 гибридный поиск + контекст v2, 2.4 память кампании и 2.5 импорт
  сценария из файла готовы (см. ниже). Этап 2 закрыт 18.11; следующий — этап 3 (веб).

## Этап 2: 2.2 индексация (18.11, готов)

- Зависимость добавлена с разрешения пользователя 18.11 («локальная модель + uv.lock»):
  `sentence-transformers` 6.3.0 (+torch/transformers) и `pgvector` 0.5.1 в `pyproject.toml`,
  `uv.lock` обновлён. Модель многоязычная `paraphrase-multilingual-MiniLM-L12-v2` (384),
  CPU, кэш HuggingFace в `~/.cache/huggingface` (на Windows symlink-warning безвреден).
  Настройки `embedding_model`/`embedding_dim` в `config.py`.
- Миграция 0005 (head): `scenario_chunks` — vector(384), генерируемый `tsv` по словарю
  языка (russian/english), индексы hnsw (vector_cosine_ops) + gin, FK CASCADE к версии и
  сущности, unique(version_id, entity_id, ord), `embedding_model` для детекта смены модели.
  Alembic check без расхождений.
- `rag/chunking.py`: нарезка сущностей на фрагменты ~200–500 токенов, видимость на уровне
  текста (сцена: `read_aloud` public + `dm_notes` dm; npc: description public +
  personality/secrets dm; item/clue/handout/lore по их visibility). `rag/embeddings.py`:
  ленивая загрузка модели, нормализованные векторы. `rag/index.py`: `index_version`
  переиспользует фрагменты неизменённых сущностей из предыдущей версии, эмбеддинги
  считает в `asyncio.to_thread` (иначе блокируется event loop воркера и рвётся Redis).
- Воркер: задача `index_scenario_version` в `tasks.py` (эмбеддинги в процессе воркера).
  Консоль: `seed` публикует и индексирует inline (`--no-index` пропускает), `reindex
  <version_id> [--previous] [--wait]` ставит задачу в очередь воркера.
- Проверки: smoke `.local/index_smoke_20261118.py` 13/13 (`.local/index-22-20261118/`):
  ru 32 фрагмента dim 384/tsv/видимость public+dm у сцен; идентичная версия 32 reused/0
  новых; изменена одна сущность — 1 новая/31 reused; en-версия проиндексирована задачей
  в реальном воркере через Redis (32 фрагмента). ruff/mypy (78 файлов, cache
  `.local/mypy-cache-22`)/import-linter (3 kept), тесты 664 passed. Приёмка 2.2 выполнена.
- Решение по модели: MiniLM-L12-v2 выбрана как лёгкая многоязычная для CPU-прототипа;
  архитектура допускает bge-m3/e5 — смена модели/размерности требует переиндексации
  (имя пишется в `embedding_model`) и при другой размерности — новой миграции.

## Этап 2: 2.3 гибридный поиск и контекст v2 (18.11, готов, офлайн-приёмка)

- `rag/search.py`: `hybrid_search` — pgvector cosine (`<=>`) + полнотекстовый `tsv @@
  plainto_tsquery` по словарю языка (russian/english), объединение RRF (k=60), фильтр
  видимости в SQL (`visibility = ANY(:allowed)`): для игрока только public, dm даже не
  выбирается. Порог релевантности: `min_similarity=0.35` или FTS-совпадение; иначе пусто.
  `LoreSearcher` (sessions+version_id+embedder+language) и `format_fragments` (бюджет
  слоя 7 ≤ 1500 токенов, ~3 симв/токен, без дублей).
- Контекст v2 аддитивно: `ContextBuilder.turn_message/decision_message` принимают
  `fragments`, шаблон `turn.j2` рендерит секцию «Relevant scenario fragments» в
  меняющемся слое. `GameMaster` получил опциональный `searcher`; `_search_fragments`
  делает предварительный поиск по тексту действий раз на ход и кладёт результат только
  в слой планирования (не в narration/system/историю) — кэш начала промпта сохраняется.
- `lookup_lore` (`game/tools.py` `LOOKUP_LORE_SPEC`/`LookupLoreArgs`, обработка в
  `master._commit_lookup`): query-инструмент, не effect; `continue_after=true`,
  возвращает фрагменты, мир не меняет, вход остаётся открытым для решения. Доступен
  только при searcher; идентичный повтор блокируется `_is_repeated_effect`, лимит
  планирования 4 вызова ограничивает злоупотребление.
- Консоль `--from-db` подключает `LoreSearcher` к `GameMaster`; YAML/`--no-db` режим
  работает без поиска (searcher=None, слой пуст, lookup не предлагается).
- Проверки: smoke поиска `.local/search_smoke_20261118.py` 11/11
  (`.local/search-23-20261118/`); smoke контекста `.local/context_search_smoke_20261118.py`
  12/12 (`.local/context-23-20261118/`): системный промпт байт-в-байт между ходами,
  фрагменты только в последнем user-сообщении, lookup возвращает фрагменты и решает вход.
  ruff/mypy (79 файлов)/import-linter (3 kept), тесты 664 passed.
- Осталось для полной live-приёмки 2.3 (по отдельному решению о платных вызовах):
  замер доли кэша относительно 1.8 и live-ответ DM по фрагментам вне сцены. Офлайн
  кэш сохранён по построению (меняется только нижний слой) и проверен байт-в-байт.

## Этап 2: 2.4 память кампании (18.11, готов, офлайн-приёмка механизма)

- Дословное окно N=`HISTORY_TURNS`=12 (`master._history_messages`); более старые ходы
  сворачиваются в сводку «что было раньше» (слой 4, `context.summary_entry`) — после
  системы, перед дословной историей. Системный промпт (слои 1–3) не затрагивается.
- `game/memory.py`: `build_summary` одним вызовом задачи `summary` (deepseek-flash,
  max_tokens 700) сжимает предыдущую сводку + ушедшие ходы; K=`SUMMARY_EVERY`=10.
  Промпт-инструкция на английском, маркер «session summarizer» (fake-провайдер отличает
  задачу сводки по маркеру без импорта game — слои не нарушены).
- Триггер `master._maybe_summarize` после рассказа игрокам: когда непокрытых ушедших
  ходов ≥ K, коммит `SummaryUpdated(text, through_turn)` (visibility dm). Состояние
  `state.summary`/`summary_through_turn` воспроизводимо из журнала (event-sourcing).
  Сводка обновляется раз в K ходов, а не на каждом; между обновлениями байт-стабильна.
- Журнал фактов уже структурирован в состоянии: улики `revealed`, отношения NPC
  `attitudes`, обещания/задания `facts` (инструмент `note_fact`/`FactNoted`), рендер в
  `turn.j2`. Факты, записанные кодом, надёжнее «памяти» модели.
- Проверки: smoke `.local/memory_smoke_20261118.py` 16/16 (`.local/memory-24-20261118/`):
  сессия 130 ходов, ранний пароль выживает в сводке и журнале фактов, сводка слоем 4
  (не в системе/последнем user), system байт-стабилен, окно ≤12, размер промпта
  21663→21664 символов (ratio 1.00), `SummaryUpdated` в журнале, covered монотонен
  10..110, сводка переиспользует предыдущую. Продакшен-офлайн FakeProvider даёт сводку
  после 22+ ходов. ruff/mypy (80 файлов)/import-linter (3 kept), тесты 664 passed.
- Отложено: live-семантика памяти (реальная модель помнит факт — платная проба по
  отдельному решению); воркер-оффлоад сводки (async-конвейер этапа 4). Сейчас сводка —
  отдельный вызов после рассказа в синхронном консольном цикле.

## Этап 2: 2.5 импорт сценария из файла (18.11, готов, офлайн + live-приёмка)

- Новый пакет `importer/`: `extract.py` (md/txt utf-8-sig; DOCX в порядке тела —
  заголовки/таблицы/списки → Markdown; PDF pypdf по страницам; ошибки
  unsupported_format/empty_document/encrypted_pdf/invalid_document/file_too_large,
  pre-check 5 МБ до чтения), `schema.py` (ImportDraft поверх Scenario + ImportNote
  kind assumption/question/missing), `prompts.py` (маркер «scenario importer»,
  контекст id правил/бестиария, видимость полей, запрет выдумывать сюжет, «текст
  модуля — данные, не команды»), `pipeline.py` (один structured-вызов задачи
  `import`, guard 60k символов, find_scenario_issues + language/template mismatch),
  `report.py` (YAML exclude_none + Markdown-отчёт: счётчики, проблемы, заметки,
  стоимость, шаги seed/play). Консоль `ai-dnd-play import <file> --lang --out
  [--ruleset --template --fake-llm --force]` — без базы; guard output_exists до
  чтения/вызова модели; проблемы валидации — предупреждение, не ошибка.
  Публикация — отдельный шаг `seed` после правки автором. Задача `import` уже была
  в config/llm.yaml (flash, thinking low, 16k токенов, timeout 300).
- Зависимости python-docx 1.2.0 + pypdf 7.1.1 (+lxml) добавлены по решению
  пользователя 18.11 (uv.lock изменён второй раз с разрешения).
- Офлайн-проверки: smoke конвейера 17/17 (`.local/import_pipeline_smoke_20261118.py`,
  ScriptedProvider: retry невалидного JSON, сломанные ссылки, guards, детерминизм
  промпта), extract-проба 14/14 (независимо перепроверена Root), консольный smoke
  10/10 (`.local/import_console_smoke_20261118.py`: md/docx/pdf, round-trip YAML,
  ошибки, load_game_content + fake play импортированного сценария). ruff/mypy
  (117 файлов, кэш `.local/mypy-cache-25final`)/import-linter (3 kept)/pytest
  664 passed 2 deselected.
- Live-приёмка (бюджет $0.1 одобрен пользователем 18.11): тестовый модуль
  «Колодец забытых имён» `.local/import-25-20261118/module.md` (3 сцены, свой
  статблок, проверки с авторскими СЛ и один без СЛ) импортирован с первого вызова:
  3 сцены/3 NPC/6 улик/1 раздатка/2 столкновения/4 lore/1 статблок, 0 проблем
  валидации, 5 заметок (допущение СЛ 13, числа статблока, вопросы про имя брата и
  замирание скелетов), $0.0045567. Артефакты `.local/import-25-20261118/live_v1/`.
- Ручная правка автора — 3 строки: attitude хранителя hostile→neutral (в модуле он
  сначала говорит), уточнение улики (доска — ножом, плиты — зубилом), убран
  не-asset map.background. Правленый YAML — `content/scenarios/well-of-forgotten-names.ru.yaml`
  (новый контент-фикстура приёмки). load_game_content чистый; fake play: проверки и
  переход площадь→кладбище работают.
- Публикация: `seed --visibility private --wait` → версия b2773649-de85-4ec9-879d-1e6cda0ce155,
  воркер проиндексировал 20 сущностей/26 фрагментов (0 reused). Live-игра
  `play --from-db` 2 хода (сессия 975d6407-baa1-4ca5-be5e-824bcf3ad796): открытие
  дословно по read_aloud + квест Миланы из dm_notes; проверки 21/DC12 и 15/DC10
  раскрыли авторские улики (names_scraped_knife, crypt_beneath_well), письмо
  brother_letter public с начала; переход на кладбище дословным read_aloud.
  13 событий, replay == snapshot, 0 ошибок, 5 вызовов/$0.010902. Проверка Root:
  `.local/import_live_verify_20261118.py` VERIFY PASS. Итого live 2.5: $0.0154587
  из $0.1. Логи: `.local/import-25-20261118/{live_v1_cli,seed_live,live_play_v1,fake_play_edited}.log`.
- Независимое ревью 2.5: P1 — 0, P2 — 4 (битые файлы/бестиарий → traceback;
  молчаливая перезапись правленого YAML; чтение огромного файла до guard; пути
  без escape в rich). Все исправлены субагентом и перепроверены Root: проба фиксов
  11/11 (`.local/import-25-20261118/fixes_p2_run2/`), смоки 17/17 + 10/10, полный
  набор проверок зелёный. Guard output_exists Root перенёс до чтения/вызова модели.
  Отчёт: `docs/reviews/2026-11-18-stage-2.5.md`.
- Bounded decisions: один вызов на модуль ≤60k символов (деление больших модулей —
  будущее); импорт не пишет в БД; регенерация хранителя/замирание скелетов —
  авторские подсказки, не механизированы; шаблон файла не требуется — обычный
  рассказ тоже импортируется, разметка лишь снижает число допущений.
- Тестовый долг 2.5 закрыт 18.11 вместе с долгом всего этапа (см. раздел ниже).
  Этап 2 закрыт 18.11 (2.1–2.5). Следующий — этап 3 (веб).

## Этап 2: тесты этапа + live-пробы 2.3/2.4 (18.11, долг закрыт)

- Решение пользователя 18.11: тесты пишут параллельно субагенты, прогоны ведёт
  Root; бюджет проб $0.2, после FAIL памяти — «фикс + перепрогон, бюджет $0.2».
- 175 новых тестов четырьмя пакетами: `tests/importer/` 70 (2.5: extract/pipeline/
  prompts/schema/report/fake/консоль), `tests/rag/` 53 (2.2/2.3: chunking/
  embeddings/search), `tests/game/` 28 (2.3/2.4: окно N=12, каденс сводки,
  SummaryUpdated, факты, промпт не растёт на 130 ходах, replay, видимость слоёв
  контекста v2, байт-в-байт префикс), `tests/integration/` 24 новых + 2 прежних
  (2.1–2.3: publish/load/pinning, индексация, гибрид+видимость в SQL, lookup_lore,
  ru/en FTS, реальная модель). Integration — только база `ai_dnd_test` (страховка
  `*_test` в stage2_helpers), боевая не затрагивается.
- Приёмка Root: pytest **815 passed, 26 deselected** + integration **26 passed**;
  mypy strict 146 файлов Success; ruff check/format 146 чисто; import-linter
  3 kept. Исправления по ходу: флейк visibility-теста (ничьи постоянного вектора →
  limit=100/candidates=200), 9 `__init__.py` + относительные импорты (mypy
  duplicate conftest), промпт сводки (см. ниже), баг драйвера проб (рекурсия
  turn↔heal, не продукт).
- Live-пробы: драйвер `.local/probe_23_24_live_20261118.py`, EN millford из базы,
  25 ходов, BudgetGateway; артефакты `.local/probe-23-24-20261118/live_v{1..4}/`,
  логи `.local/probe_23_24_live_run{1..4}.log`. run1 стоп на ходу 7
  (invalid_turn_decision от условной формулировки игрока — сценарий переписан на
  явные действия, $0.019031); run2 25 ходов $0.075285 — кэш PASS, факт вне сцены
  PASS, память FAIL; run3 стоп на ходу 17 (рекурсия драйвера, $0.048684);
  run4 25 ходов + 1 лечение $0.080297 — **все три PASS**. Replay==snapshot во
  всех, 0 ошибок модели в завершённых.
- Кэш vs baseline 1.8 EN: dm_tools 0.634/0.629 ≥ 0.621 PASS; narration 0.399/0.390
  < 0.439 — следствие скользящего окна (история сдвигается каждый ход; до сводки
  доля ~0.53→0.76, после ~0.25; стабильный префикс кэшируется 0.89–0.98), не
  регрессия. $/ход $0.0030–0.0032 ≤ $0.0033. Факт вне сцены (ход 2, вопрос о
  мельнице из таверны): ответ из public-lore + знаний NPC, без dm-утечек.
- **Продуктовый фикс памяти**: суммаризатор терял личные детали персонажей
  (run2: бабушка/медное кольцо Mira выброшены, ход 23 «not spoken here»).
  `game/memory.py`: список сохраняемого расширен — «personal details and
  backstory players share about their characters (family, keepsakes, oaths,
  fears)». run4: ход 23 верно вспоминает факт хода 1 вне окна (сводка покрывает
  1–20); журнал фактов использован (1 факт). Оговорка: финальная сводка run4
  приписала факт Marta вместо Mira (в сводке через ход 10 и нарративе верно) —
  эпизодическая ошибка, принята по политике, фиксы не усложняем.
- Бюджет проб: первая серия run1+2 $0.094316 из $0.2; перепрогон run3+4 $0.128981
  из $0.2; итого $0.223297. Остатки не переносить; готовые пробы не повторять.
- P2 (в бэклоге PLAN, не блокеры): dm-фрагменты lookup_lore в ledger нарратива
  (согласовано с «нарратор = голос DM», возможное усиление — фильтр);
  chunking._sentences — границы только по «! »/«? », точки не границы;
  format_fragments — обрыв вместо пропуска длинного фрагмента.
- Окружение: integration требуют env-хуки с `127.0.0.1` (AI_DND_TEST_DATABASE_URL
  и AI_DND_REDIS_URL): `localhost` даёт IPv6-fallback ~2.08s > таймаута health 2s.
- Отчёт: `docs/reviews/2026-11-18-stage-2-tests.md`. Оставшийся тестовый долг —
  1.4–1.8.

## Этап 3: старт 18.11, параллельные потоки

### Wiring каркаса ПРИНЯТ Root 18.11 (задача #18 закрыта; пауза снята по «продолжай»)

- Состав сборки: адаптер identity_get_current_user в realtime/auth.py
  (cookie→сессия, HTTP+WS), реестр db/models.py (+identity/realtime/billing),
  main.py (install_identity + sessions/WS роутеры + billing router с
  dependency_overrides на _billing_current_user: role=="admin"→is_admin).
  Шим-таблицы в ai_dnd_test снесены (.local/stage3-wiring/prep_test_db.py);
  alembic upgrade head 0005→0006→0007→0008 прошёл на ai_dnd_test и на dev-базе
  ai_dnd (чистая цепочка).
- Гейты после сборки: pytest 815 passed / 26 deselected, integration 26 passed
  (env-хуки 127.0.0.1), ruff check + format 178 файлов, mypy strict 178 Success,
  lint-imports 3 kept / 0 broken.
- **End-to-end смок wiring 29/29 PASS**: `.local/stage3-wiring/wiring_smoke.py`
  (результаты wiring_smoke_result_*.json). create_app() целиком, настоящая
  identity-auth без seam-заглушек: CSRF (GET /api/auth/csrf, без заголовка 403),
  register→verify-email через ConsoleMailer.sent→login (до подтверждения 403),
  cookie-сессии, /api/me и 401 без cookie; создание сессии DM, join по invite
  (actor pc_<hex>, чужой 403, без auth 401, WS 4401/4403); WS-комната с реальной
  cookie-auth: backlog ActorsJoined, public всем, dm только DM, player:<actor>
  адресату+DM; HTTP state/events с фильтром видимости (facts/summary скрыты от
  игрока); billing: daily_free 500, balance 200/401, /api/admin/expenses 403→
  (role=admin в БД)→200; logout отзывает сессию.
- Грабли смока (исправлены в нём, не продукт): httpx jar держит два значения
  ai_dnd_session (cookie из ответа + выставленная вручную) — удалять перед
  set; admin-отчёт агрегирует daily_free по всем пользователям прошлых прогонов
  (проверка ≥500, не ==500).
- BillingGuard в оркестрацию хода НЕ подключён намеренно: правильное место —
  worker-оркестратор хода (следующая задача 3.3), не глобальный gateway.
- 3.2 фронтенд (агент F) остановлен по паузе, не завершён и не проверен:
  структура создана (src/api, components, demo, i18n, pages, router.tsx,
  stores, vite.config.ts, .env.example), демо Codex перенесено в src/demo.
  Достройка/проверка (build/typecheck/lint + браузерный golden path) — новая
  задача субагенту.
- Следующая волна: оркестрация хода AI DM в воркере (streaming, блокировка
  комнаты, окно сбора действий, BillingGuard) + 3.4 экран игрока + 3.2 достройка.
- Ничего не коммитилось. Платных вызовов в этапе 3 не было.

### 3.3 оркестрация хода ПРИНЯТА Root 18.11 (агент O, задача #20)

- Состав: realtime/turns.py (окно сбора действий Redis hash+window_id, Lua
  submit/close/release; замок turn_lock NX PX с токеном; done-маркер окна;
  run_dm_turn/run_dm_open собирают GameMaster как console._play + BillingGuard +
  BroadcastJournal; build_turns_router: POST /{id}/actions только player,
  POST /{id}/turn dm/player, 409 под замком); tasks.py dm_turn/dm_open_session/
  close_action_window (таймаут сном внутри задачи — taskiq без delayed delivery);
  broadcast.publish_chunk; ws.py форвард эфемерных chunk-ов; service.create_session
  ставит dm_open_session.kiq после коммита. Root подключил build_turns_router в
  main.py (identity_get_current_user).
- Протокол chunk: {"type":"narration_chunk","payload":{"text":...}} без seq —
  всем в комнате, не журналируется, не досылается; финальный Narrated (seq) —
  конец стрима и полный текст.
- Отступления приняты: GameMaster отдаёт NarrationChunk после коммита Narrated и
  зовёт complete("narration") — адаптер _RoomGateway сам потребляет
  BillingGuard.stream и публикует TextDelta до финального события; адаптер
  подставляет плательщика-host в CallContext (master не передаёт user_id);
  дедуп открытия по наличию Narrated в журнале (join-события пишутся раньше);
  бой в ai-сессии пока вежливый combat_pending (web-бой — задача 3.4).
- Проверки Root: turns-смок 45/45 перепрогон (.local/stage3-turns/turns_smoke.py,
  результат turns_smoke_result_20261118-193900.json); wiring-смок 30/30;
  pytest 815 + integration 26; ruff check/format 179; mypy src 116 Success;
  lint-imports 3 kept. Браузерный перепрогон фронтенда выполнен против живого
  бэкенда (test DB): register→verify→login→профиль 500 кредитов/$0.0500→RU→
  демо gate→hall→setup→экран игрока→d20=20; скрины root-01…03 в
  .local/stage3-frontend/.
- Изоляция смока O: глобальные предохранители биллинга подняты через env ВНУТРИ
  смока (в общей ai_dnd_test накопились cost прошлых живых прогонов $2.16/$31.66)
  — billing-код не менялся; для живых прогонов помнить про накопленный cost.
- Долг: таймаут окна теряется при рестарте воркера (сон внутри задачи); места
  мёртвых актёров учитываются в полноте окна; web-бой и read-эндпоинты
  (списки сессий/персонажей, лист персонажа) — следующие задачи 3.4.

### 3.4 backend ПРИНЯТ Root 18.11 (агент P, задача #22)

- Состав: `realtime/turns.py` — `run_combat_step` (замок комнаты, цикл как
  console._combat: действие игрока → ходы существ → рассказ раунда стримом;
  единственный option авто-действием; party-актёр с human-местом → waiting_player,
  без human-места → тактика движка choose_action), `GET /{id}/combat-options`
  (чистый RulesEngine.available_actions из state.rules, без GameMaster/LLM),
  `POST /{id}/combat-actions` (202, индекс против свежих options, воркер
  перепроверяет под замком); `tasks.combat_step`; `GET /api/sessions` (мои по
  seats, с ролью); новые `characters/router.py` (список/создание/лист owner)
  и `scenarios/router.py` (текущая published public-версия на slug+язык,
  title из info версии).
- Wiring Root в main.py: build_characters_router + build_scenarios_router
  (identity_get_current_user); combat-ручки и список сессий уже внутри
  подключённых роутеров. Смок P собирал портал сам, поэтому wiring проверен
  отдельной Root-пробой через create_app(): 18/18 PASS
  (.local/stage3-combat/root_wiring_probe.py, результат root_wiring_probe_result_20261118-201845.json).
- Принятые отступления: лист чужому 403 not_owner / отсутствующий 404;
  combat-actions только dm_mode=ai (409 not_ai_session); сценарии — одна текущая
  public-версия на slug+язык. Обоснованы контрактом.
- Проверки Root: смок P перепрогнан 65/65 PASS
  (combat_smoke_result_20261118-201610.json; артефакты P не перезаписаны);
  pytest 815 passed / 26 deselected; integration 26 passed; ruff check+format
  118 чисто; mypy strict 118 Success (приватный кэш); lint-imports 3 kept.
  Ownership: P трогал только turns.py/tasks.py/realtime/router.py + два новых
  роутера; main.py/master.py/миграции/uv.lock не тронуты.
- Ревью цикла: narrate_combat чистит _combat_log (master.py:724) → while
  round_finished завершается; available_actions вне своего хода = [] (безопасно
  для dm-места); ActionError → Narrated visibility for_player; BillingStop/
  LLMError → публичный системный Narrated, замок снят, flush_pending в finally.
- Долг: pytest этапа не писались (решение); in-memory _combat_log/pending не
  восстанавливаются после рестарта воркера (как в консоли); browser-проверка
  боя — на приёмке F3 (UI ещё нет); schema.d.ts фронтенда перегенерировать
  после wiring (владелец F3).
- Смок-манипуляции P: очередь инициативы детерминированно переписана в снапшоте;
  action_error проверен форс-инъекцией ActionError; планки биллинга подняты env
  внутри смока. fake=True, платных вызовов нет.

### /demo: натуральные факелы, облака и чистая луна — 19.11.2026

- Принят Root только визуальный блок `/demo`: не `/login`, не кнопка имени/кубики, не вся #24, 3.4 или этап 3.
- Код в `frontend/src/demo/`: `GateAmbience.tsx`, `GateMoonlight.tsx`, `gate-ambience.css`, `gate-moonlight.css`, `demo.css`; `demo.source.css` — нетронутый архивный донор.
- Ассеты в `frontend/public/images/`: лицензированный stock `torch-flame-loop.webm` (`torch-flame-license.txt` без изменений), `gate-cloud-field.webp`, `castle-sky-mask.png`.
- Новые static/unlit фоны там же: `castle-gate-clear-moon.webp`, `castle-gate-clear-moon-unlit.webp`, 1536×1024; активный код использует новые URL.
- Оба факела — натуральные video, независимая фаза правого со сдвигом 2.6 с; фиксированный waiting 150 мс сохраняет кадр на границе loop.
- Root проверил desktop и mobile: по 3 реальных wrap каждого video с `bothPlaying=true`.
- CSS all-or-static переключает оба video и чистый фон вместе; reduced-motion/hidden — pause/resume.
- Облака: canvas 30 fps за точной sky mask, цикл 35 с; свет зависит от плотности облаков над луной, `MAX_LIGHT=0.28`, мобильный множитель 0.85 вместо 0.6; smoothing 600 ms / fade 1400 ms сохранены.
- Финальная правка по сообщению «освещение не меняется при закрытии луны» подтверждена и принята Root: раньше `screen` внутри отдельного stacking context почти не менял фон; теперь в `GateMoonlight.tsx` / `gate-moonlight.css` внешний `.gate-moonlight-illumination-layer` со `screen`, внутри raster `normal`, соседний cloud layer `normal`; UI/text/forms не затронуты.
- Оба ref синхронны; tick вызывает `hide` при reduced-motion и очищает классы/маски/переменные даже при задержке change event.
- Замер Root на естественном цикле (`.local/stage3-gate/ambience-2026-11-19T00-11-57-339Z-27036-browser-smoke-result.json`): door 20.161→26.248 (+30.2%), arch 14.475→18.490 (+27.7%), foreground stone 14.948→22.161 (+48.3%), button 163.764→163.764 (без изменений); correlation −0.977, UI changes 0.
- Чистая полная луна: центр (155.17, 99.03), r=30.03; запечённые облака убраны из обоих окон верхнего неба, замок/чаши/нижний туман сохранены.
- Root независимо подтвердил: хэши 8 оригиналов неизменны, вне sky patch изменений нет, небо новых фонов идентично.
- Независимое quality-review: GO, без P1/P2. Гейты Root: npm typecheck/lint/build — PASS, typecheck/lint повторно PASS; прежний warning >500 kB без изменений.
- Исторический clean-moon baseline 23:59:57 — до последующего усиления света: 46/46 PASS, 0 failed, 0 errors, 0 skipped, `complete=true`; отчёт: `.local/stage3-gate/ambience-2026-11-18T23-59-57-056Z-3116-browser-smoke-result.json`; 24 события `favicon.ico` 404 сохранены в отчёте и исключены как прежняя посторонняя проблема страницы, не ошибки эффекта.
- Последний полный browser-smoke (`.local/stage3-gate/ambience-2026-11-19T00-28-55-459Z-21900-browser-smoke-result.json`) НЕ зелёный: 45 passed, 1 failed, 1 CDP error `Inspected target navigated or closed` в visibility, 0 skipped; 24 события `favicon.ico` 404 учтены отдельно. Этот результат не заменяет исторический baseline 46/46.
- Отдельный прогон того же `realVisibility` без ослабления assertions в текущем owned Chrome: `.local/stage3-gate/ambience-2026-11-19T06-22-10-890Z-24460-browser-smoke-result.json`, scope `visibility-only`, 3 passed, 0 failed, 0 errors, 0 skipped; реальные `document.hidden` и trusted visibility, не mock. Причина прежней CDP ошибки не установлена; в отдельном прогоне она не воспроизвелась, это не полный прогон.
- Root просмотрел реальные desktop clear/covered и mobile PNG последнего полного отчёта.
- Новых зависимостей и постоянных тестов нет; постоянные тесты остаются долгом до завершения этапа.

### 3.4 frontend ПРИНЯТ Root 19.11 (задача #23; код F3/F4, браузерный цикл Root)

- F3 и F4 умерли на лимите 150 ходов; код F4 завершён (mock-seam удалён полностью,
  schema.d.ts перегенерирован, гейты зелёные), отчёта нет — приёмку и браузерный
  цикл Root выполнил лично 19.11 (fake-воркер, ai_dnd_test, платных вызовов нет).
- Браузерный цикл (скриншоты .local/stage3-frontend/root-18…root-26):
  /games live-список; экран игрока (лента, броски, боевая панель, форма действий
  заблокирована в бою с подсказкой, карточка героя, отряд); бой в 10a79c9f:
  «Закончить ход» → 202 → ходы гоблинов стримом (атака 21 vs КД 10, урон, хиты 0,
  unconscious, CombatEnded enemies), форма разблокирована, карточка 0/8;
  создание AI-сессии ad834188 через диалог (сценарий/язык/ведущий/импровизация) →
  «Пульт ведущего»; приглашение копируется из localStorage known-sessions;
  регистрация rootplay23 → письмо ConsoleMailer в backend.log → подтверждение
  токеном → вход; daily_free 500 кредитов сразу при регистрации; создание
  персонажа Торвальд + полная карточка по шаблону; вход по приглашению
  (код + селект персонажа) → место за столом; действие игрока → авто-закрытие
  окна (все player-места дали действия, turns.py submit_action) → fake-ход:
  проверка d20 15 vs 15 успех + рассказ в ленте живьём; повторный вход хостом;
  en-локаль: UI переведён, контент сессии остаётся ru (язык игры); /demo регрессия:
  врата → зал → настройка стола → экран игрока → анимация кубика (все 3 скина).
- Поведение уточнено: «Запустить ход ведущего» после уже прошедшего хода честно
  даёт 409 no_pending_actions (окно закрыто авто-закрытием по полноте игроков).
- Дефекты UI (не блокеры, переданы в #31): D1 — код ошибки хода показывается сырым
  («no_pending_actions») без перевода; D2 — пустой список персонажей в диалоге
  входа трактуется как «API недоступен» и показывает UUID-fallback.
- Терминология (решение пользователя 19.11): «Карточка персонажа» вместо
  «Лист персонажа» — Root исправил в ru/en локалях (openSheet, sheetLoadFailed,
  liveHint, character_invalid, sheet_template_not_found); проверено в браузере.
- UX-решение пользователя 19.11: вход за стол в 1-2 клика, создание персонажа
  с контекстом стола — задача #31 (карточка стола по invite-ссылке, автопосадка,
  редирект на создание с пре-выбранным сценарием и возвратом).
- Гейты Root после правок локалей: typecheck/lint/build зелёные (chunk-size
  warning давно существует). Тестовый долг: pytest этапа 3 по завершении этапа.
- Долги окружения: 13 боёв в старых сессиях F4 зависли на ходах существ после
  рестарта воркера (resume нет — ops-долг, кандидат: sweep при старте воркера);
  пароль f4player сброшен Root в ai_dnd_test для возврата хостом (тестовая база).

### 3.4 UX вход за стол ПРИНЯТ Root 19.11 (агент J31, задача #31)

- Состав: публичный GET /api/sessions/by-invite/{token} (capability, без auth,
  без setup/state; SessionPreviewResponse incl. sheet_template_id и
  taken_character_ids для фильтра «подходящих» по правилу join_session;
  seats_total=null до ёмкости #32); карточка стола InviteTableCard на
  /games?invite — посадка одним кликом, select при нескольких подходящих,
  «Создать персонажа» → /characters?scenarioVersionId&invite (диалог сам
  открыт, сценарий закреплён, баннер) → автовозврат ?characterId → автопосадка;
  invite сохраняется через анонимный редирект /login и регистрацию; D1
  (error.code ?? error.detail как ключ локализации), D2 (пустой список →
  создание, без UUID-fallback); namegen ru/en с родом (кнопка «Сгенерировать»);
  терминология «Мастер — ИИ/человек», «Я мастер», «Лидер команды» в локалях.
- Отступления J31 приняты: минимальные правки router.tsx/login-page.tsx вне
  владения (validateSearch + возврат с invite); seats_total=null; доп. поля
  превью (не dm-секреты).
- Приёмка Root: код-ревью всех швов; гейты зелёные (frontend typecheck/lint/build
  дважды; backend ruff/format/lint-imports/mypy 127/pytest 815); смок превью
  7/7 повторно (`.local/stage3-join/preview_smoke.py`, Root добавил
  WATCHER_ENABLED=false); браузер-цикл вживую по 5 пунктам отчёта + D2 +
  namegen (DOM-снапшоты; in-app браузер скрыт — click/screenshot недоступны,
  операции через evaluate_script). Подробности и тестовые данные:
  `.local/stage3-join/root_acceptance.md`.
- Дефект найден и исправлен Root: каталог сценариев отдаёт только последнюю
  версию, стол может быть на старой → контекст-диалог показывал UUID.
  Фикс: characters-page.tsx берёт title из публичного превью стола
  (contextScenario?.title ?? preview.scenario_title ?? versionId); проверено
  вживую, гейты повторены.
- Окружение: зависший uvicorn --reload разблокирован (TaskStop b7f3iy9qi →
  перезапуск b4cnarj90, тот же env); воркер baq4dh5v8 не тронут.
- Долги: маппинг ошибок combat-panel.tsx; лейбл «Ведущий» в EventFeed; pytest
  этапа; ёмкость стола seats_total — #32. Скриншоты приёмки ЗАКРЫТЫ 19.11
  (`.local/stage3-join/screenshots/accept-2026-11-18T23-55-59-*`, скрипт
  `portal-shots.mjs`, 5/5). Владение файлами J31 освобождено.

### 3.4 gate-визуал и кубик ПРИНЯТЫ Root 19.11 (задача #24; код G24 + агент пользователя)

- Визуальная приёмка по кадрам через raw CDP (headless Chrome 9223, свои
  таргеты): факелы/облака desktop 1440 (пара кадров, мерцание численно),
  800 и 390 (оба факела на кронштейнах), prefers-reduced-motion (статика),
  створки → зал, кубик 1/7/20 × янтарь/нефрит/аметист (рёбра сходятся,
  силуэт-шестиугольник, число в центре). Скрипты и кадры:
  `.local/stage3-gate/acceptance-shots.mjs`, `doors-probe.mjs`, `screenshots/`;
  итоги `.local/stage3-gate/root_acceptance.md`.
- Дефект найден и исправлен Root: правила перехода в demo.css были scoped
  descendant (`.codex-demo .screen-opening …`), а класс висит на корне
  (`codex-demo screen-opening`) → створки/фон/гашение не работали. Фикс:
  8 селекторов → compound `.codex-demo.screen-opening`; перепроверено
  семплами (+300 ms фон-зал и door-left, +400 ms полуоткрытые створки) и
  кадрами; гейты frontend зелёные. demo.source.css (донор) не тронут.
- Пламя: silhouette-фикс внешнего агента пользователя (G24b,
  `.local/stage3-gate/report-flame-fix.md`) в кадрах читается natural;
  эстетика — за пользователем. GateAmbience.tsx/gate-ambience.css Root не
  редактировал. Наблюдение: правый факел на ≥~1200px перекрыт панелью входа
  (layout демо, косметика, зона агента пользователя).
- Регресс боя: боевых фикстур в ai_dnd_test нет; шов dice-renderer.tsx:26
  делегирует в принятый demo/Dice — живой кадр боя отложен (долг).
- namegen «Сгенерировать» принят ранее в #31. Долги: живой кадр боя, pytest
  этапа. Владение G24 освобождено; задача #24 закрыта.

### 3.4 стартовая-портал ПРИНЯТА Root 19.11 (часть A #32, агент P32a) + центральная композиция #33 (Root)

- Часть A: `/` = gate-сцена демо с реальной авторизацией. Новые
  `pages/portal-page.tsx` (lazy), `components/auth-panel.tsx` (все 11 data-testid
  прежней login-page сохранены), `portal.css` (добавочные scoped-стили).
  Router: indexRoute → PortalPage (authenticated → /games), requireAuth и анонимный
  /games → '/', /login → '/' с invite, AppHeader скрыт только на '/'. Успешный вход
  → screen-opening (850ms, reduced-motion 0) → /games с invite; register/verify
  внутри gate без створок. i18n namespace portal ru/en полный, переключатель RU/EN
  в шапке gate. Отчёт и живые сценарии (a)–(g) 18/18: `.local/stage3-portal/report-start.md`,
  `acceptance.mjs`.
- Polish #33 (утверждено пользователем 19.11): панель по центру на створках
  (max-width 440, ≤900px — 400), компактный заголовок над ней (h1 clamp 34–54),
  подпись под панелью; факелы не перекрыты. Раскладка в portal.css scoped под
  `.codex-demo.portal-page` — /demo не затрагивается (проверено: grid сохранён).
  Живая проверка Root 16/16 (`composition.mjs`, свой headless Chrome): offset
  центрирования 0px (относительно clientWidth), зазор панель↔факелы 163/164px
  (1440), 173/174 (800), 157/158 (1024); мобильный 390 стек под картинкой;
  створки → зал после входа; en полный. Кадры `root33-2026-11-19T07-02-19-*`.
- favicon.svg (золотая ✦) + link в index.html, title «Портал историй · AI DnD» —
  устранён 404 /favicon.ico. Гейты Root: typecheck/lint/build зелёные.
- Замечание: при высоте 900px privacy-note/футер чуть ниже фолда — обычный скролл
  страницы (тематический скроллбар), CTA выше фолда; не дефект. Наблюдение #24
  «правый факел перекрыт панелью» относилось к layout ДЕМО; на реальной стартовой
  композиция центральная и факелы открыты (демо — зона агента пользователя).
- Итоги и фикстуры: `.local/stage3-portal/root_acceptance.md`. Protected-сессия
  ad834188… не тронута. Осталась часть B #32: «Зал историй» (/games) + открытый
  набор за столы (recruiting + seats_total, миграция Root).

### 3.4 часть B backend ПРИНЯТ Root 19.11 + демо-заглушка авторизации (запрос пользователя)

- Часть B backend (Root, интеграционный код): `game_sessions.recruiting bool
  default false` + `seats_total int null` + Index `ix_game_sessions_status_recruiting`
  (миграция `0012_open_recruiting`, применена ТОЛЬКО к ai_dnd_test, alembic check
  clean, round-trip). `GET /api/sessions/open-tables` (auth, зарегистрирован ДО
  `/{session_id}`): active+recruiting, пропускает invite_token None, скрывает
  заполненные столы (players ≥ seats_total), поля OpenTableItem (id, scenario,
  scenario_title, language, dm_mode, seats_total, seats_taken, host_name,
  invite_token), сортировка created_at desc. `PATCH /{id}/recruiting`
  {recruiting, seats_total 1..12|null} — только лидер (`_require_host`, 403
  host_only). join_session: проверка ёмкости ПОСЛЕ идемпотентного возврата
  существующего места и ДО проверок сценария/персонажа → 409 `table_full`.
  Preview by-invite отдаёт seats_total. Offline: ruff/mypy --strict
  --no-incremental чисто; OpenAPI → frontend/openapi.json → `npm run gen:api`
  (schema.d.ts содержит open-tables/recruiting). Live in-process смок 11/11
  (`.local/stage3-portal/be_open_set_smoke.py`, kiq заглушён, LLM не вызывался,
  созданные сессии удалены): `.local/stage3-portal/be-acceptance.md`.
- Демо-заглушка авторизации (запрос пользователя 19.11 «на время демо… без
  валидации и подтверждения, просто в базу писало»): backend
  `IdentitySettings.demo_auth` (env `AI_DND_DEMO_AUTH`, в backend/.env =true) ИЛИ
  `environment==DEV` → register сразу ставит email_verified_at и НЕ шлёт письмо;
  login пропускает гейт email_not_verified. Frontend `auth-panel.tsx`:
  `demoAuth = import.meta.env.DEV` → без вкладок login/register, поля
  «необязательно», пустые поля синтезируются (demo-…@demo.local / demo-pass-1 /
  «Путник»), register→login→ворота→/games, шага токена нет; privacy-note честно
  про запись в базу. Все 11 data-testid сохранены. ПРОД не ослаблен: флаги
  demo_auth/DEV в prod-сборке выключены, боевой путь register→verify→login цел.
  Тесты, утверждающие verify, отсутствуют (grep пуст) — безопасно.
- Проверка демо-формы в браузере (Root, свой headless Chrome :9224,
  `.local/stage3-portal/demo_form_check.mjs`): форма в демо-режиме (вкладки
  скрыты, имя показано, demo privacy-note); пустая отправка → /games, me
  verified=true имя «Путник»; вход с именем → /games, me «Демо-Путник»;
  console errors []. Кадры `demo-empty-games.png`, `demo-named-games.png`.
  Live-смок через vite-прокси: `demo_auth_smoke_vite.py` (register 201 True /
  login 200 / me 200).
- ТОПОЛОГИЯ DEV-СЕРВЕРОВ (важно): :8000 и :8020 — ЗОМБИ-сокеты упавших/убитых
  --reload серверов (netstat PID LISTENING, но процесс мёртв/не убивается;
  Windows-артефакт: убитый reloader-parent оставляет сокет). :8020 завис именно
  на авто-перезагрузке после правки router.py (WatchFiles «Reloading…» без нового
  startup), затем после одобренного пользователем kill стал зомби. Рабочий
  backend Root теперь на :8030 БЕЗ --reload (background b8r9jbsmt, лог
  `.local/stage3-portal/root-backend-8030.log`, env: DATABASE_URL=ai_dnd_test,
  REDIS 6379/0, FAKE_LLM=true, ENVIRONMENT=dev, DEMO_AUTH=true,
  BILLING_DAILY_FREE_CREDITS=100, BILLING_USER_DAILY_SPEND_CREDITS=1000,
  `.venv/Scripts/python.exe -m uvicorn ai_dnd.main:app --host 127.0.0.1 --port
  8030`). `frontend/vite.config.ts` proxy /api и /ws → 127.0.0.1:8030. Без
  --reload правки .py НЕ применяются на лету — нужен перезапуск (спрашивать
  пользователя). :8010 — собственный backend FE-субагента (fe32b-backend.log),
  не зависеть. Воркер baq4dh5v8 не тронут. Когда зомби :8000/:8020 очистятся
  (машинный kill/перезагрузка) — можно вернуть proxy на 8000.
- Часть B frontend ПРИНЯТА Root 19.11 (задача #34 закрыта; код субагента P32b-FE,
  который упёрся в лимит 150 ходов уже после написания всех файлов; проверку и
  доработку сделал Root). Зал `/games`: `pages/games-page.tsx` (788 строк),
  `hall.css` (402), `components/hall-table.tsx`, `components/hall-recruiting.tsx`,
  `api/client.ts` openTables/setRecruiting, локали games.hall ru/en. Объединяет
  «мои игры» + открытый набор; карточки-столы с флагами (лидер/мой/открытый
  набор) и местами, панель деталей, посадка тем же механизмом #31 (dialog
  invite → «Сесть за стол»), лидерский контроль набора (switch + stepper 1..12 +
  «без ограничения», оптимистично + reconcile). Create/join dialogs сохранены.
  Доработка Root: `SessionListItem` обогащён scenario_title/seats_total/
  seats_taken/host_name/invite_token (batch-запросы без N+1) + games-page берёт
  их для «моих» столов — иначе в свежем браузере (без запомненных invite) зал
  показывал сырой slug вместо названия истории. OpenAPI/schema.d.ts
  перегенерированы. Гейты: tsc/eslint/vite build чисто; backend ruff + mypy
  --strict --no-incremental (127 файлов) чисто.
- Браузерная приёмка зала (Root, свой headless Chrome :9225, кэш отключён через
  CDP — иначе ERR_CACHE_READ_FAILURE на пустом профиле):
  `.local/stage3-portal/hall_acceptance.mjs` + `stepper_probe.mjs`. 17 столов,
  названия локализованы, переключение деталей, контроль набора (toggle
  открыт/закрыт, лента open-tables появляется/исчезает), stepper 4→5→6 и
  «∞» (изолированная проба; в общем прогоне инкремент терялся из-за гонки
  обвязки, не дефект), посадка на не-защищённый стол → dialog → «Сесть за
  стол» → живая игра (экран игрока, лента, отряд, карточка), мобильный 390 без
  горизонтального overflow, /demo не тронут, console errors []. Кадры
  `hall-desktop.png`, `hall-leader-recruiting.png`, `hall-seating-dialog.png`,
  `hall-after-sit-game.png`, `hall-mobile.png`, `hall-demo-regression.png`.
  Protected-сессия ad834188… не тронута; мутации только на своих/FE-scratch
  столах. Тестовый долг: open-tables/recruiting/table_full, demo-auth гейты и
  зал покрыть на закрытии этапа 3.

### Директива пользователя 19.11 (вечер): 3.7 → README+Docker, добиваем этап, финальный LLM-прогон $0.3

- 3.7 «Выкладка для закрытой беты» УБРАН из плана; заменён на «README и
  Docker-упаковка (самостоятельное развёртывание)»: README, Dockerfile
  backend+worker (один образ), frontend nginx, compose postgres/redis/backend/
  worker/web, .env.example без секретов. VPS/Caddy/S3-бэкапы/Sentry/загрузка
  сценария файлом — бэклог после беты. Агент D35 (задача #35).
- «Делаем остальное»: #26 кнопки лидера (агент F26), чат вне игры + UX без
  сознания (backend Root готов, frontend агент после #26, задача #37), 3.5
  кубики (#38), 3.8 наблюдатели (backend агент O21 задача #21, frontend позже),
  тесты этапа целиком после финального прогона (#41).
- Инцидент 19.11 ~13:08: ходы вставали с billing_stop «месячный предохранитель
  платформы». Причина: в ai_dnd_test.llm_calls синтетические фикстуры биллинг-
  тестов (id 9–12, 16–18.11, $29.50/$1.20/$0.90/$0.06) — сумма месяца $31.66 >=
  global_monthly_usd $30; реальные расходы сегодня $0. Фикс БЕЗ удаления данных
  и рестартов: оверрайд spend_limits scope=global monthly_usd=100 (та же механика,
  что admin PUT /api/admin/billing/limits; пароль smoke-админов неизвестен,
  demo-логин пароль проверяет). check_global OK. Оверрайд снят не будет до
  решения; для прод-значений удалить строку spend_limits global. Воркер
  перезапущен с одобрения пользователя (b6ksk1fob, лог .local/stage3-player/
  worker-b.log, env как у 8030); старый воркер был мёртв с 11:40. Ручной
  браузер-тест пользователя + живой Monitor логов backend+воркера (19.11).
- Финальный прогон 3.4 ОБЯЗАТЕЛЬНО с реальным LLM после всех правок; бюджет
  $0.3 (явное решение пользователя 19.11). Фокус: поражение игрока в бою —
  UI без сознания, чат доступен, команды заблокированы, лишних блокировок нет.
- Чат вне игры backend (Root, задача #36): таблица session_chat_messages
  (миграция 0013 применена к ai_dnd_test), GET/POST /api/sessions/{id}/chat
  (пагинация before=id сообщения, limit≤200), WS-событие chat_message без seq
  (история через REST), мут блокирует чат (403 muted), кик/no_seat 403,
  без сознания НЕ блокирует. ruff/mypy --strict чисто, OpenAPI/schema.d.ts
  обновлены. Живой смок — после перезапуска 8030 (нужен approval).
- Гейт поражения backend (Root, #39): POST /actions теперь 403
  {"code":"incapacitated"} если актёр мёртв/hp≤0/prevents_actions-состояние
  (rules.combat.incapacitated_by). Боевые кнопки без сознания = только
  «Закончить ход» (движок). UI-часть (#37): баннер без сознания, локализованные
  состояния (сейчас party-panel показывает сырые id),notice-блокировка команд
  при открытом чате.
- Агенты в работе: D35 (#35), F26 (#26), O21 (#21 backend). Владение файлами
  разведено: D35 только новые файлы; F26 game/party-panel+game-page+game.css+
  locales; O21 backend/realtime; Root чат-бэкенд завершён до старта O21.
- Решение пользователя 19.11 (нокаут): нокаут ПЕРЕИСПОЛЬЗУЕТ мут, не дублирует
  логику: seats.mute_source ("leader"/"knockout", миграция 0014), при hp≤0/
  prevents_actions воркер после хода ставит muted=true source=knockout и шлёт
  seat_update (тот же бейдж/подсказка что у кнопки «Замьютить»); при подъёме
  снимается ТОЛЬКО knockout-мут, лидерский не трогается. Чат вне игры открыт
  и при муте, и без сознания (мут блокирует только команды/действия).
  Реализовано Root: models/broadcast/schemas (mute_source в SeatInfo и
  seat_update payload); ОСТАЛОСЬ после завершения O21 (он владеет realtime):
  service.seat_info/mute_seat/sync_knockout_mutes, снятие muted-гейта в
  post_chat, хуки sync в finally run_dm_turn/run_combat_step, миграция 0014
  на ai_dnd_test, OpenAPI/gen:api, гейты. Проба поражения обновлена под
  403 muted как основной путь.
- ДОДЕЛАНО Root после O21: service.seat_info/mute_seat (source leader)/
  sync_knockout_mutes (идемпотентно, лидерский мут не перетирает, авто-анмут
  при подъёме), post_chat без muted-гейта, хуки sync в finally run_dm_turn/
  run_combat_step; миграцию 0014 применил O21 к ai_dnd_test; ruff/mypy чисто;
  OpenAPI/schema.d.ts перегенерированы (chat/observe/spectators/mute_source).
- O21 (#21 backend) ЗАВЕРШЁН: observe/leave_observe, SPECTATOR_LIMIT=20,
  spectators в GET сессии, kick/mute расширены на spectator; ws/turns не
  менялись (фильтры уже корректны); пробы 31/31 unit + 45/45 live (8041,
  убит); долг: вход наблюдателя без seat_update (счётчик через GET),
  имя spectator в SeatInfo=None. Frontend наблюдателей — после #37/#38.
- F26 (#26) ЗАВЕРШЁН: leader-moderation.tsx (SeatControls/KickConfirmDialog/
  MutedBadge/KickedBanner), party-panel badge/controls, use-session-socket
  onSeatUpdate + стоп переподключений на 4401/4403, game-page wiring
  (оптимистичный мут с откатом, свой кик баннер+«В зал», notice muted);
  gates чисто; CDP 26/26+11/11+8/8, скриншоты moderation-*.png. F26 обнаружил
  8030 мёртвым и поднимал свой со старым кодом.
- Перезапуск 8030 РАЗРЕШЁН пользователем и выполнен Root: старый процесс убит,
  новый background bckilp6ij без --reload, env документированный (ai_dnd_test,
  fake LLM, demo auth, daily_free 100, user_daily 1000), лог
  .local/stage3-portal/root-backend-8030-b.log. ВОРКЕР baq4dh5v8 всё ещё со
  старым кодом: sync_knockout_mutes живёт в воркере — перед финальным LLM-
  прогоном нужен перезапуск воркера (и 8030) с AI_DND_FAKE_LLM=false и
  поднятыми лимитами AI_DND_BILLING_SESSION_DAILY_SPEND_CREDITS=3000 и
  USER_DAILY_SPEND_CREDITS=3000 ($0.3), отдельный approval.
- Проба поражения .local/stage3-final/defeat_probe.py: ALL_PASS на живом 8030
  (сессия 27f339db-eb0a-48e3-8a70-507a6965ab92, root31b player): combat-options
  только end_turn, /actions 403 muted, /chat 201 без сознания+мут, история ок;
  condition id в ruleset = stunned. Состояние оставлено для смока F37,
  --restore возвращает backup (guard от перезаписи backup есть).
- F37 (#37) ЗАПУЩЕН: чат-панель + UX нокаута (баннер, локализованные состояния,
  бейдж по source, notice причин, combat-подсказка, кикнутому combat скрыт),
  владелец game-page/chat-panel/party-panel/combat-panel/socket/portal-chat/
  locales; после него #38 кубики, затем frontend наблюдателей.

### F37 (#37) чат + UX нокаута ПРИНЯТ Root 19.11

- Ревью Root: chat-panel.tsx (REST-история PAGE_SIZE 50 + WS-дедуп mergeById,
  оптимистичный pending с откатом, connectNonce-перезагрузка при reconnect,
  scroll-anchor старых страниц, unread-jump, MAX_TEXT 500, Enter/Shift+Enter);
  game-page.tsx (myDowned = hp≤0 || PREVENTS_ACTIONS_CONDITIONS || mute_source
  knockout; DownedBanner; ActionForm disabled при combat/pending/muted/downed/
  kicked; notice kicked>downed>muted; ChatPanel скрыт только кикнутому;
  submitError 403 muted+downed → incapacitated); combat-panel.tsx (downed →
  только end_turn, kicked → скрыт). Дефектов нет.
- Смок `.local/stage3-portal/chat-smoke-result.json`: 54/54 ok, failed 0,
  consoleErrors пусто (defeat/leader/player). Фикстуры: leader root31b,
  player chat37-player-49321c@probe.test, defeat-сессия 27f339db (состояние
  нокаута оставлено пробой #39). Покрыто: UX поражения, чат REST/WS/дедуп/
  откат/пагинация, mute/unmute, mobile 390, en, demo-регрессия.
- Гейты frontend (Root, совместно с каруселью): typecheck/lint/build зелёные
  (build ✓ 4.63s, только прежний chunk-warning 759.64 kB).
- Долг: pytest/e2e этапа на закрытии (#41).

### Карусель зала + окно посадки под рядом ПРИНЯТА Root 19.11 (запрос пользователя)

- Запрос пользователя: столы в один ряд по 3, крутятся колёсом и стрелками
  (активны только когда есть куда крутить), окно присоединения открывается ПОД
  рядом и видно без ручного скролла (прежняя боковая панель была за фолдом).
- Состав (агент, отчёт `.local/stage3-hall-carousel/report.md`): новый
  `components/hall-carousel.tsx` (wheel non-passive deltaY→horizontal с
  preventDefault только когда ряд скроллится, на краях скроллится страница;
  стрелки canLeft/canRight через scroll+ResizeObserver, epsilon 1px, шаг =
  карточка+gap, smooth/reduced-motion auto); hall-table.tsx убраны hover/focus
  (выбор только кликом, toggle); games-page.tsx selectedId=null, панель под
  каруселью key={table.id}+scrollIntoView, чужой стол → InviteTableCard embedded
  (null-token → alert hall-invite-missing), свой → «Войти»+лидерские контролы;
  hall.css одна колонка min(1200px,92%), ряд nowrap overflow-x auto snap,
  flex-basis 3/2/1 по брейкпоинтам 899/559, панель max-width 620px centered;
  локали games:hall scrollLeft/scrollRight/inviteMissing/floorHint.
- Приёмка Root (браузер in-app, вьюпорт 526px → 1 карточка; десктоп ≥900px
  «ровно 3» подтверждён по CSS calc((100%−40px)/3)+gap20): колесо deltaY→
  scrollLeft+244 defaultPrevented страница не скроллится; стрелка вправо →
  scrollBy 426 smooth 4→430; right disabled в конце (max 4686), left disabled
  на true 0; клик → aria-pressed true, панель под рядом (top74≥rowBottom66)
  видна (scrollIntoView); чужой стол embedded-посадка (ветка «Создать
  персонажа» для аккаунта без персонажей); повторный клик закрывает; hover не
  выбирает; консоль чистая. Гейты typecheck/lint/build зелёные.
- Минорный нюанс (бэклог полировки, не дефект): padding ряда 4px + snap start →
  стартовая scrollLeft=4 > epsilon 1, левая стрелка на визуальном старте активна
  (4px хода). Кандидат: учитывать padding-left в canLeft или убрать horiz padding.
- ПРИНЯТО Root 19.11. Долг: pytest/e2e зала на закрытии этапа (#41).

### Кнопка «Встать из-за стола» (self-leave) ПРИНЯТА Root 19.11 (запрос пользователя, задача #8)

- Запрос пользователя: «нужна явная кнопка "Встать из-за стола", а то сесть за
  стол можно, а покинуть уже нельзя». Full-stack self-leave (агент, отчёт
  `.local/stage3-leave-seat/report.md`).
- Backend: `service.leave_seat` + `DELETE /api/sessions/{id}/seat` (204). Своё
  место по user_id удаляется БЕЗ SessionKickRow (в отличие от kick — сесть снова
  можно); dm → 422 cannot_leave_host; нет места → 404 seat_not_found; audit
  «seat.leave»; publish_seat_update removed=True; пересчёт окна действий как в
  kick_seat. Сокет ушедшего закрывается 4403 (ws.py). openapi.json + schema.d.ts
  перегенерированы (leave_seat).
- Frontend: `leaveSeat` в leader-moderation.tsx; кнопка «Встать из-за стола»
  (leave-table, LogOutIcon) в header-actions game-page ТОЛЬКО при seat.role!='dm'
  (player+spectator); confirm-диалог (leave-table-dialog/confirm/cancel, рамка
  kick-dialog): «Встать из-за стола?» / «Вы покинете стол. Сесть снова можно
  будет через зал.» / «Остаться» / «Встать из-за стола»; confirmLeave → invalidate
  session/sessions/open-tables + navigate /games; leavingRef подавляет flash
  KickedBanner; ошибка → leave-table-error role=alert. Локали game ns ru/en
  (leaveTable/Title/Body/Confirm/Cancel/Errors). F37/карусель не тронуты.
- Попутно агент: whitespace-фикс W292 в 6 untracked alembic-миграциях 0009–0014
  (ruff --fix/format, логика цела — Root проверил 0014 mute_source) + удаление
  повреждённого .mypy_cache (регенерируемый). Ради зелёных штатных гейтов.
- Приёмка Root: ревью кода; гейты backend ruff/format/mypy 196/lint-imports 3-0/
  pytest 815, frontend typecheck/lint/build — зелёные. Живой браузер (root31b):
  player 050d055b — кнопка видна, диалог, «Остаться» без изменений, confirm →
  /games без kicked-баннера, DELETE 204, место удалено, бан 0, audit seat.leave 1;
  dm 7db3bb37 — кнопки НЕТ, пульт ведущего на месте; API dm→422, no-seat→404,
  re-join → 409 character_taken (НЕ бан, персонаж занят elsewhere). Консоль чистая.
- Окружение 19.11: скачок питания убил backend 8030/воркер/vite. Рестарт 8030
  РАЗРЕШЁН пользователем. Подняты: backend 8030 (ai_dnd_test, fake LLM, demo auth,
  daily_free 100, user_daily 1000, watcher off, без --reload, лог
  .local/stage3-leave-seat/backend-8030.log), воркер (worker.log), vite (порт 5174;
  5173 занят zombie [::1]; vite на IPv6 localhost → браузер http://localhost:5174).
  Инфра postgres(pg18)/redis healthy. Cold-start нюанс: health redis timeout 2s
  отменяет первый коннект → redis «error» до первого реального redis-запроса
  (DELETE /seat прогрел → health ok). НЕ дефект продукта.
- ПРИНЯТО Root 19.11. Долг: pytest/e2e self-leave (#41); EN UI вживую не снимался.

### 3.5 3D-кубики (#38) ПРИНЯТЫ Root 19.11 (агент dice3d-agent)

- Объём: 3D-адаптер `frontend/src/game/dice-3d.tsx` на `@3d-dice/dice-box-threejs@0.0.12`
  за сеймом `DiceRenderer` (kind 2d|3d, props не менялись). Детерминированная остановка
  на серверном значении: forced-нотация `1d20@N` (физика честная, после settle библиотека
  форсирует верхнюю грань, reason="forced") + верификация адаптером (расхождение/WebGL-
  ошибка → 2D-фолбэк, неверная грань не показывается). Singleton WebGL-сцена на журнал
  (лимит контекстов браузера): newest eventId анимирует, старые карточки — статичный 2D.
  Replay-guard по eventId. Lazy-chunk (React.lazy+Suspense+error boundary): dice-3d-*.js
  555 kB отдельным chunk, лобби/портал/демо three не тянут.
- Настройка «Меньше анимации»: store `stores/settings.ts` (zustand+persist aidnd.settings),
  toggle в шапке игры (game-page.tsx:412, aria-pressed/testid/tooltip, ru/en). Дефолтный
  kind: 3D на capable-десктопе; 2D при lessAnimation / prefers-reduced-motion / pointer:coarse
  / viewport≤768 / hardwareConcurrency<4 (телефоны не тормозят по построению). Скины
  amber/jade/amethyst customColorset (texture:none, без внешних ассетов). 2D `Dice.tsx` сохранён.
- Гейты Root: typecheck/lint/build зелёные (index 766 kB прежний warning). Живой браузер:
  мобильный дефолт 2D (viewport 526), toggle+persist+EN вживую, 3D-адаптер прямым монтажом —
  rollComplete notation="1d20@13" rollValue=13 reason="forced" (грань 13 на скриншоте
  root-3d-13-amethyst.png), replay-guard (повтор eventId → 0 бросков), lazy-chunk не в лобби,
  консоль чистая. Отчёт `.local/stage3-dice/report.md` (+приёмка Root).
- НЕ тронуты: backend, uv.lock (агентом кубиков), openapi/schema.d.ts, demo/Dice.tsx.
- Долг: pytest/e2e 3D (#41); top-level three в package.json не используется кодом (библиотека
  бандлит свой three r143) — можно убрать отдельным решением; 3D в реальной карточке на
  десктопном viewport ≥769 вживую не снимался (browser-use viewport фиксирован 526px;
  адаптер проверен прямым монтажом с теми же CSS). ПРИНЯТО Root 19.11.

### D35 (#35) Docker-упаковка — финальная приёмка Root 19.11 (задача #3 ЗАКРЫТА)

- Пересобраны образы после карусели/leave-seat: `docker compose --env-file
  .local/docker-smoke.env build backend web` → exit 0 (прежняя сборка backend упала
  на useradd из-за buildkit EOF при скачке питания; повтор прошёл, слои из кэша).
  backend:local + web:local Built.
- Изолированный смок-стек поднят (`up -d`, порт 8090, БД ai_dnd_smoke, FAKE_LLM=true,
  demo auth): db/redis healthy → backend healthy (alembic upgrade head + /health ok) →
  worker + web. Порты db/redis наружу не публикуются; dev-стек (8030/5174, infra
  pg18/redis8) не затронут — отдельный проект compose.
- `smoke.sh` — все 11 шагов зелёные: web 200 (SPA), seed --no-index (shadow-over-millford.ru),
  CSRF, register 201 (email_verified), login 200, каталог, POST /api/sessions/ 201,
  ping-worker → pong, worker выполнил dm_open_session → dm_open_done status=ok (fake LLM,
  полный цикл AI-DM хода в Docker), health-proxy 200.
- Актуальность образов: DELETE /seat dm → 422 cannot_leave_host (leave_seat в backend-образе);
  web-бандл содержит leave-table. Отчёт `.local/docker35/report.md`, артефакты в .local/docker35/.
- Ревью упаковки без замечаний: docker-compose.yml (pgvector pg16, один образ backend/worker,
  entrypoint миграции+uvicorn / taskiq, depends_on healthy, model-cache том), backend/frontend
  Dockerfile, nginx.conf (/api+/ws прокси, Upgrade, SPA try_files, immutable-ассеты, 32m),
  .dockerignore (секреты .env* исключены), README (полный, соответствует compose), .env.example без секретов.
- НАХОДКА/ДОЛГ (на решение пользователя, не блокирует): backend-образ **28.4 GB** —
  torch 2.15.0+cu132 (CUDA, слой зависимостей 12.3 GB) + chown -R дублирует .venv (слой 6.3 GB).
  Для CPU-эмбеддингов CUDA не нужна: CPU-torch (~200-300 MB) + COPY --chown сократили бы образ
  до ~2-3 GB. Трогает uv.lock (frozen — только по отдельному решению) и Dockerfile → Root
  автономно НЕ делал. Web-образ 126 MB — норма.
- Смок-стек остановлен (`down` без -v; тома/образы сохранены). ПРИНЯТО Root 19.11.
  git commit/push не делал, uv.lock не менял, dev-серверы не перезапускал.

### Оптимизация Docker-образа backend — приёмка Root 19.11 (задача #9 ЗАКРЫТА)

Решение пользователя «давай докера оптимизируем» (19.11) — явное разрешение тронуть
uv.lock/Dockerfile/pyproject. Исполнитель: субагент. Отчёт `.local/docker35/optimization-report.md`.

- Результат: **28.4 GB → 2.25 GB** (−92%). Независимо проверено Root:
  `docker images ai-dnd-portal/backend:local` → 3a06be0370e5, 2.25GB;
  `docker history` → финальный образ содержит ОДИН слой `COPY --chown=app:app /app/backend`
  1.63GB + content 442kB + useradd 73.7kB; слоя `chown -R` (6.3GB) и CUDA-слоя
  `uv sync` (12.3GB) в финальном образе НЕТ (multi-stage builder отброшен).
- backend/pyproject.toml: `torch>=2.15.0` — ПРЯМАЯ зависимость; `[[tool.uv.index]]
  pytorch-cpu url=https://download.pytorch.org/whl/cpu explicit=true`;
  `[tool.uv.sources] torch={index="pytorch-cpu"}`. Обоснование: uv 0.11 `torch-backend`
  действует только на `uv pip`, не на `uv lock`; sources не применяется к чисто
  транзитивному torch → нужна прямая зависимость + explicit-индекс.
- backend/uv.lock: `grep -c 'name = "nvidia-'` → **0** (было 43); torch `2.15.0+cpu`
  (linux/win) и `2.15.0` cpu-wheel (darwin), source=whl/cpu; triton/cuda-* удалены.
- backend/Dockerfile: multi-stage (builder uv sync → final `COPY --chown=app:app
  --from=builder`), `install -d -o app -g app /home/app/.cache` (фикс root-owned
  тома model-cache → PermissionError при encode). Раскладка /app/backend+/app/content,
  entrypoint (миграции+uvicorn / taskiq worker), USER app uid 10001, EXPOSE 8000 сохранены.
- Смок исполнителя 11/11 (артефакты `.local/docker35/smoke-final.log` и др. проверены
  Root: backend healthy, register/session HTTP 201, ping-worker pong, worker
  `dm_open_done status=ok`). CPU-encode в контейнере: torch 2.15.0+cpu,
  cuda_available False, encode (1,384) — по отчёту, согласуется с lock/образом.
- Гейты backend (отчёт, `uv run --no-sync`): ruff/format/mypy/lint-imports зелёные,
  pytest 815 passed / 26 deselected. Python-исходники оптимизацией НЕ менялись.
- ПРИНЯТО Root 19.11. git commit/push не делал. Dev .venv НЕ пересинкан (в dev
  по-прежнему CUDA-torch, функционально совместим); dev-серверы не перезапускались.
- НА РЕШЕНИЕ ПОЛЬЗОВАТЕЛЯ (не блокирует): (1) пересинк dev .venv под CPU-lock +
  рестарт dev-backend/воркера (по разрешению); (2) `docker image prune` — вернуть
  ~28GB dangling-слоёв старого образа (деструктивно, по разрешению). Смок-стек
  остановлен (`down` без -v), тома сохранены.

### 3.9 frontend /admin волна 2 ПРИНЯТА Root 19.11 — задача #30 ЗАКРЫТА (агент Ad5)

- Объём: вкладки sessions (список+деталь: seats/setup/state/журнал
  useInfiniteQuery, stop/resume/message через confirm), scenarios (visibility
  select+confirm), complaints (status-фильтр, resolve-диалог 1..500), ops
  (health/watcher run+ack/analytics days 7/14/30/90); ADMIN_TABS.roles +
  фильтр по роли (модератор = только scenarios+complaints, недоступный ?tab=
  → первая доступная без редиректа); router пускает admin+moderator;
  nav-ссылки для обеих ролей; api/admin.ts += 14 вызовов; JsonBlock;
  CORE_ACTIONS += 7 кодов; i18n ru/en (188 ключей проверены скриптом Root).
  Отчёт `.local/stage3-admin/report-frontend-wave2.md`, приёмка
  `.local/stage3-admin/root_acceptance-wave2.md`.
- Проверки Root: ревью всех файлов; контракты сверены (events since exclusive
  seq>since; возврат visibility=ScenarioItem, resolve=ComplaintItem); гейты
  typecheck/lint/build зелёные; API: защищённая сессия ad834188 active/seq9/
  5 мест, сценарий public восстановлен, аудит содержит все 6 кодов волны 2,
  модератору 403 на sessions/ops/users и 200 на scenarios/complaints; UI:
  админ 7 вкладок + все блоки ops/sessions/scenarios/complaints/audit,
  модератор 2 вкладки без админ-контента, игрок → /games; консоль чистая.
- Принятое отступление: visibility проверена round-trip public→unlisted→public
  на единственном сценарии (он же сценарий защищённой сессии); исходное
  восстановлено, сессия не менялась. Долг: отдельный сценарий консолью для
  модерационных проверок.
- Фикстуры: модератор ad5-mod-20261119@probe.test / ModProbe#30x, игрок
  ad5-player-20261119@probe.test / PlayerProbe#30x, свежая сессия
  96f2613b-fd33-4c65-bd96-744a78804d08 (active, seq 2 — полигон stop/message),
  жалоба 7ef59787 (resolved). Скрипт .local/stage3-admin/wave2-fixtures.mjs.
- Расхождения с PLAN-чеклистом: /api/admin/expenses в backend НЕТ (устаревшая
  формулировка) — расходы покрыты аналитикой ops + аудит billing.adjust;
  per-user/per-session лимиты read-only; скрытие/бан контента вне 3.9 (7.1).
- Долги: ack алерта вживую (алертов не было), «загрузить ещё» >200 событий,
  pytest/e2e этапа. Панель браузера оставлена под админом на /admin.
- Ownership освобождён: components/admin/*, admin-page.tsx, api/admin.ts,
  router.tsx (adminRoute), app-header.tsx (nav-admin), locales admin-ns.

### 3.9 frontend /admin волна 1 ПРИНЯТА Root 19.11 (агент Ad4a, задача #30)

- Объём: api/admin.ts (10 ручек #27, типы из schema.d.ts), admin-page.tsx
  (реестр ADMIN_TABS users/billing/audit, role=tab, ?tab=, ленивый монтаж),
  common.tsx (useAdminErrorText/Pager/QueryError/formatDateTime/классы),
  users-tab (поиск+пагинация, confirm-диалоги role/ban/reset), billing-tab
  (fuses, global-лимиты PUT/DELETE, read-only таблица лимитов, adjust-диалог),
  audit-tab (фильтры action/actor_id, raw-fallback для кодов волны 2).
  Отчёт `.local/stage3-admin/report-frontend-core.md`, приёмка
  `.local/stage3-admin/root_acceptance-wave1.md`.
- Принятые отступления: регистрация ns 'admin' в i18n/index.ts (1 строка);
  ключ admin:navLink; редактирование лимитов только global-scope;
  интерполяция {{revoked}}. Дефект remount notice лимитов исправлен
  исполнителем (notice поднят в BillingTab, key=updated_at).
- Проверки Root: построчное ревью 7 файлов; гейты typecheck/lint/build зелёные
  (chunk-warning существующий); живая spot-проверка админом
  rootwire-adm-20261118-221101@probe.test: users 115/20 строк, поиск, диалог
  роли open/cancel, fuses daily 0/100 + monthly 31.66/1000, DELETE global-лимита
  через confirm, audit-фильтр billing.adjust → 5 переведённых строк; не-админ
  root31b → /admin редирект /games без nav-ссылки. Скриншот
  `.local/stage3-admin/screenshots/admin-users-root.png`.
- Фикстуры: moderator root30-pu5y69@probe.test (750 credits, пароль в отчёте
  не указан — волне 2 регистрировать своего); global-лимит удалён; сессия
  ad834188… (#31) не тронута.
- Волна 2 ПРИНЯТА Root 19.11 (см. секцию выше): вкладки
  sessions/scenarios/complaints/ops + role-filter.
  Ключевой факт guards: sessions-ручки надзора ADMIN-only, scenarios/complaints
  — admin+moderator (ModDep), ops — admin-only. Модератору видны только
  «Сценарии» и «Жалобы»; router.tsx beforeLoad и ADMIN_TABS фильтровать по роли.
  Схемы #28/#29 уже в schema.d.ts (regen не нужен). Аудит-коды волны 2:
  session.stop/session.resume/session.message/scenario.visibility/
  complaint.resolve/ops.watcher_run/ops.alert_ack.
- Долги: pytest этапа; полный live-цикл мутаций волны 1 — на свежих фикстурах
  волны 2; per-user/per-session лимиты read-only (расширение по решению).

### 3.9 надзор и операционка ПРИНЯТЫ Root 19.11 (агенты Ad2/Ad3, задачи #28/#29)

- Ad2 #28 (admin/supervision.py + realtime-швы): 9 ручек /api/admin — сессии
  list/detail/events/stop/message (admin), сценарии list/visibility и жалобы
  list/resolve (admin+moderator); журнал надзора без фильтра видимости; stop
  публикует session_stopped control после коммита; join/action/turn/combat-action
  остановленной сессии → 409, задачи воркера no-op skip; аудит seat.mute/seat.kick
  в транзакции мутации; жалобы пользователя POST /api/sessions/{id}/complaints.
  Принятые отступления: аудит session.message отдельной транзакцией после
  доставки (DbJournal коммитит сам); join 409 строкой через SessionError-путь
  realtime (формат остальных ошибок не менялся); очередь жалоб DESC + resolved_at.
- Ad3 #29 (admin/ops.py + ai/watcher.py + deepseek.fetch_balance): 5 ручек
  /api/admin/ops — health (db/redis/очередь XLEN+XPENDING stream/окна хода/
  последний ok-ход по llm_calls; worker.status=unknown без heartbeat), watcher
  state/run/ack, analytics по дням UTC (4 агрегата, union_all auth+seats).
  Watcher: порог AI_DND_WATCHER_ALERT_USD=5, interval 300s, алерты ops:alerts
  (LPUSH+LTRIM 100) с дедупликацией по kind (сброс при восстановлении и после
  ack последнего); алерты только админка (решение пользователя). Принятые
  отступления: stream вместо list, httpx2, причина ошибки fetch только в логе.
- Wiring Root (main.py): build_supervision_router + build_ops_router включены;
  watcher_loop стартует в lifespan только при AI_DND_WATCHER_ENABLED=true и
  наличии ключа, гасится cancel+await при остановке. Флаг enabled добавлен Root
  в WatcherSettings (default false — смоки/тесты не поднимают фоновый цикл и не
  ходят в сеть); в backend/.env включён true. В оба смока Root добавил
  setdefault ENABLED=false для воспроизводимости.
- Проверки Root: смоки перепрогнаны — надзор 68/68
  (supervision_smoke_result_20261118-220136.json), операционка 29/29
  (ops_smoke_result_20261118-220148.json); собственная проба wiring 17/17
  (root_wiring_probe_result_20261118-221101.json: все 14 ручек + жалобы в
  openapi настоящего create_app(), 401/403/200 по ролям через identity-cookie,
  stop/resume, watcher lifespan вкл/выкл). Гейты: ruff/format 127, lint-imports
  3 kept, mypy strict 127, pytest 815, integration 26.
- Ограничение аналитики (долг): active_users считается по дате входа
  (auth_sessions.created_at) ∪ посадки (seats.created_at) за UTC-день — это
  «новые входы/посадки», не полный DAU (вошедший ранее и играющий сегодня не
  учитывается). Кандидат: добавить llm_calls.user_id как маркер активности.
- Долги: heartbeat воркера нет; pytest этапа отложен; offset-пагинация.
  Живой endpoint баланса DeepSeek ПРОВЕРЕН 19.11 после рестарта: статус ok,
  баланс 9.37 USD (порог 5), формат balance_infos[].total_balance подтверждён.
- Окружение после рестарта 19.11 (одобрено пользователем): uvicorn --reload
  порт 8000 (ai_dnd_test, fake, логи .local/stage3-player/backend.log), ОДИН
  fake taskiq worker (worker.log); задвоенный воркер под uv-python устранён.
  Админ-ручки #27/#28/#29 и watcher активны вживую.
- Ownership освобождён: admin/{supervision,ops}.py, ai/watcher.py,
  ai/providers/deepseek.py, realtime/{router,service,turns,broadcast}.py, tasks.py.

### 3.9 ядро админки ПРИНЯТО Root 19.11 (агент Ad1, задача #27)

- Состав Ad1: admin/{audit,service,router}.py — 10 эндпоинтов контракта
  (users list/search, role, ban, reset-sessions, billing adjust/limits/fuses, audit),
  фабрика build_admin_router(GetCurrentUser), CSRF на весь роутер, guard по свежему
  UserRow (роль+бан); каждая мутация + аудит в одной транзакции, adjust кладёт
  audit id в credit_ledger.ref_id. identity: login ban → 403 "banned", deps ban → 401,
  revoke_all_user_sessions; billing: admin_adjust (reason admin_adjust вне
  SPEND_REASONS — баланс да, расход нет), effective-override хелперы, reserve читает
  user-override внутри транзакции под advisory-блокировкой.
- Wiring Root: main.py include build_admin_router(identity_get_current_user);
  db/models.py реестр пополнен admin-моделями И SessionKickRow (пропуск 0009);
  бан в WS-seam закрыт фильтром UserRow.banned_at.is_(None) в get_session_user
  (identity/sessions.py) — HTTP и WS сразу; живое WS-соединение не рвётся мгновенно (долг).
  Guard вынесен Root в admin/guard.py (require_role(get_current_user, *roles)) —
  общий для #28/#29, admin/router.py переведён на него.
- Проверки Root: смок Ad1 перепрогнан 47/47
  (.local/stage3-admin/admin_core_smoke_result_20261118-211308.json); собственная
  проба wiring 17/17 (root_wiring_probe_result_20261118-212008.json: роуты через
  openapi, реестр 0009/0010, бан HTTP 401 + WS 4401 + login 403, разбан+повторный
  вход, аудит, fuses); гейты ruff/format/lint-imports 3 kept/mypy 123/pytest 815/
  integration 26.
- Схема 0011 complaints готова Root и применена к обеим БД для #28;
  game_sessions.status и scenarios.visibility/status уже существовали — новых
  колонок надзор не требует.
- Долги: pytest ядра админки отложен (долг этапа); бан не рвёт живое WS;
  аудит модерации лидера — #28.
- Ловушка окружения: этот FastAPI хранит включённые роутеры как _IncludedRouter
  без .path — проверять состав роутов только через app.openapi()["paths"].

### 3.4 модерация лидера ПРИНЯТА Root 19.11 (агент M, задача #25; работа 18.11)

- Решение пользователя 18.11: лидер команды (host_id) модерирует стол.
  Состав (только realtime/): router.py — POST /{id}/moderation/mute {seat_id,
  muted} и /moderation/kick {seat_id} внутри существующего build_router
  (wiring main.py не нужен); service.py — mute_seat/kick_seat/_require_host,
  join → 403 kicked до создания места, seat_info.muted; schemas.py — SeatInfo.muted
  и схемы модерации; broadcast.publish_seat_update (без seq); ws.py — seq-less
  verbatim, seat_update removed своего места → close(4403); turns.py post_action
  → 403 muted после role-проверки (combat-actions мут НЕ блокирует — бой не встаёт).
- Схема Root: миграция 0009 (seats.muted, session_kicks) на обеих БД; кик —
  INSERT session_kicks ON CONFLICT DO NOTHING + DELETE места (seat_reads CASCADE)
  в одной транзакции, затем пересчёт окна try_close_and_dispatch (stale/locked/
  empty игнорируются); кикнутый актёр в бою авто-играется тактикой движка
  (существующий путь _has_human_player_seat=false).
- Проверки Root: смок M перепрогнан 40/40 PASS
  (moderation_smoke_result_20261118-205907.json; артефакты M целы); ruff
  check/format 123 чисто, lint-imports 3 kept, mypy strict 123 Success,
  pytest 815 passed, integration 26 passed. Ownership: realtime/* + два
  whitespace-автофикса ruff в untracked admin/ (зона Ad1, безобидно).
- Принятые отступления: несуществующая сессия → 404 до host_only; seat_update
  доходит всем клиентам verbatim (UI сам решает реакцию), 4403 только кикнутому.
- Долг: модерация без журнала/seq/seat_reads (аudit придёт с #28); мут не
  отзывает уже принятое в окно действие; кикнутый актёр остаётся в setup/state
  (авто-игра); pytest этапа отложены. Кнопки UI — задача #26 после F4.
- Смок M: все task.kiq запатчены (живой воркер F4 задач не получал), планки
  биллинга env внутри смока, fake=True, БД только ai_dnd_test.

### 3.2 фронтенд ПРИНЯТ Root 18.11 (агент F2, задача #19)

- Достроено внутри frontend/: Vite proxy /api и /ws (ws:true) → 127.0.0.1:8000;
  типы из OpenAPI без сервера (app.openapi() → openapi.json → openapi-typescript
  → src/api/schema.d.ts; openapi.json в .gitignore); реальный auth-flow
  (GET /api/auth/csrf → {token}, X-CSRF-Token + credentials на мутациях, ретрай
  при csrf_failed, разбор detail:{code}); регистрация → экран подтверждения с
  ручным вводом токена (dev: ссылка в логе ConsoleMailer) → вход → /api/me →
  PATCH /api/me → logout; профиль показывает реальный GET /api/billing/balance.
- «Мои игры»: реальные POST /api/sessions/ и /join; созданные сессии — в
  localStorage (list-эндпоинтов в API нет). «Мои персонажи» — mock с badge.
  i18n ru/en на всех страницах, тёмная тема, кириллические шрифты. Демо Codex
  (/demo, Dice.tsx/demo.ts) сохранено и бросается (скрин d20=20).
- Проверки Root: npm typecheck/lint/build зелёные (build — только warning о
  chunk >500 kB, долг код-сплита); ревью vite.config.ts/api/client.ts;
  скриншоты golden path `.local/stage3-frontend/01…17-*.png` (register 201,
  verify 200, login 200, me 200/401, balance 200, sessions 201, join 200,
  logout 200 — сеть подтверждена агентом). Браузерный перепрогон Root — после
  приёмки оркестрации O (пока O правит realtime, приложение поднимать рано).
- Долг/решения на потом: ~~daily_free лениво~~ РЕШЕНО пользователем 18.11:
  daily_free начисляется СРАЗУ при регистрации (identity/router.py register и
  OAuth-callback → CreditLedger.grant_daily_free, идемпотентно по UTC-дате);
  wiring-смок расширен до 30/30 (register_grants_daily_free,
  daily_free_idempotent), перепрогон PASS, pytest 815, ruff/mypy identity и
  lint-imports чисто. Списки игр/персонажей ждут list-эндпоинтов API;
  chunk-сплит билда; тесты этапа 3 — по завершении. В
  .local/stage3-frontend/ остались артефакты остановленного агента F (чужие
  скриншоты, node_modules) — не чистить.

- Решение пользователя: «что можно делать параллельно — давай делать на субагентах,
  надо успеть по максимуму». Четыре потока: A — 3.1 identity, F — 3.2 фронтенд,
  R — 3.3 ядро realtime (seats/API/WS), B — 3.6 кредиты.
- Решения пользователя 18.11: (1) uv.lock — разрешены authlib, argon2-cffi,
  itsdangerous, aiosmtplib (третье изменение lock с разрешения); (2) фронтенд
  этапа 3 строим ВНУТРИ frontend/ — демо Codex развивать до целевого стека
  (Tailwind/shadcn/TanStack/i18next), логику Dice.tsx/demo.ts сохранить; прежний
  запрет «не трогать frontend/» снят этим решением; (3) OAuth-ключей и SMTP нет —
  dev-режим: письмо подтверждения в лог, OAuth без ключей 503.
- Разведение владения: миграции 0006 (identity) / 0007 (seats, +host_id/
  invite_token в game_sessions) / 0008 (credit_ledger); агенты НЕ трогают
  db/models.py, main.py, config.py, чужие пакеты; auth — seam-зависимости
  (identity/deps.get_current_user — целевая, у R/B заглушки). Смоки агентов —
  create_all против ai_dnd_test + шим users (id UUID PK), alembic upgrade не
  гоняют до сборки цепочки. Тесты этапа — по завершении (долг фиксируется).
- Приёмка Root ВЫПОЛНЕНА (см. «Wiring каркаса ПРИНЯТ» выше): цепочка alembic
  0005→0006→0007→0008, реестр db/models.py, wiring роутеров в main.py,
  auth-заглушки заменены на identity; новые import-linter контракты не нужны
  (существующие защищают только чистоту rules/sheets.formulas). BillingGuard —
  в оркестрацию хода (следующая задача 3.3), не в глобальный gateway.
- **3.6 ядро принято Root 18.11** (агент B): billing/ (ledger append-only,
  advisory-lock на пользователя, reserve/charge/refund/settle с инвариантами ref,
  daily_free идемпотентен по UTC-дате; BillingGuard в стиле BudgetGateway —
  предохранитель→лимит сессии→атомарный резерв→вызов→расчёт, обрыв потока
  возвращает резерв, без плательщика fail-closed; BillingSettings env
  AI_DND_BILLING_*). Курс 1 кредит=$0.0001; дефолты: daily_free 500, резерв 50,
  пользователь 1000/день и 10000/мес, сессия 500/день, предохранители $2/день и
  $30/мес. Смок 23/23 PASS (прогон Root), pytest 815 passed, ruff/mypy чисто.
  Коллизия параллельных агентов в общей ai_dnd_test: шим users смока B сломался
  о реальную users identity (display_name NOT NULL) — Root сделал вставку
  адаптивной по information_schema. Bounded: незакрытые резервы считаются
  расходом (консервативно); факт>резерва допускает временный минус; DeepSeek
  watcher — протокол+заглушка. Осталось: wiring guard в gateway/main, реальный
  watcher, админ-страница фронта. Тестовый долг billing — по завершении этапа 3.
- **3.1 принято Root 18.11** (агент A): identity/ (argon2id пароли, сессии
  sha256-токен в httpOnly-cookie SameSite=Lax/Secure не-dev, CSRF double-submit
  cookie+X-CSRF-Token на все мутации вкл. register/login, роли user/moderator/
  admin, locale, ConsoleMailer/SmtpMailer, Authlib Discord/Google без ключей 503,
  seam get_current_user/require_role в deps.py, install_identity(app) для
  wiring). Миграция 0006 (users/oauth_accounts/auth_sessions). uv add: authlib
  1.9.9, argon2-cffi 25.1.0, itsdangerous 2.2.0, aiosmtplib 5.1.3 (разрешено).
  Смок 26/26 PASS (прогон Root), pytest 815 passed, ruff/mypy 178 файлов чисто.
  Bounded: email паттерном без email-validator; OAuth-привязка к существующему
  email только при email_verified провайдера; argon2 в asyncio.to_thread.
  Для боевого режима в backend/.env: AI_DND_SECRET_KEY, ENVIRONMENT=prod,
  BASE_URL, DISCORD/GOOGLE_CLIENT_ID/SECRET (redirect {base}/api/auth/oauth/
  {provider}/callback), SMTP_*. Тестовый долг identity (CSRF-matrix, TTL, OAuth
  mock, гонка email, timing-safe login) + rate limiting по Redis (арх. 14) —
  по завершении этапа 3. ВАЖНО для сборки: в ai_dnd_test гибридная users
  (шим billing + ADD COLUMN identity) — перед alembic upgrade снести шим-таблицы
  в тестовой базе.

## Бюджет, окружение и материалы

- Новая серия на полные прогоны (одобрена пользователем 18.11): лимит 60 вызовов
  / $0.50. Полный EN-прогон израсходовал 43 / $0.043213; осталось 17 вызовов
  / $0.456787. Расширение бюджета пользователь отклонил («не нужно»); повторные
  полные прогоны не запускать. Прежняя серия 150/$0.50 закрыта на 141/$0.262361.
  DB округляет стоимость; остаток не является биллинговым балансом.
- История и подробное ревью: [dm-quality](reviews/2026-11-16-dm-quality.md).
  Старые результаты: `.local/demo-playthrough-2026-11-16/{baseline_ru,postfix_full,postfix_probes}`;
  новые: `.local/demo-playthrough-2026-11-17/{protocol_probes,protocol_full}`.
  `.local` игнорируется Git, но файлы существуют в workspace; не очищать.
- Live-бюджет 2.5 (одобрен пользователем 18.11): $0.1 на тестовый прогон импорта;
  израсходовано $0.0154587 (импорт $0.0045567 + игра по импортированному $0.010902).
  Остаток этой серии не переносить в другие прогоны без отдельного решения.
- Live-бюджет проб 2.3/2.4 (одобрен пользователем 18.11): первая серия $0.2 —
  израсходовано $0.094316 (run1+run2); после фикса промпта сводки отдельное
  решение «фикс + перепрогон, бюджет $0.2» — израсходовано $0.128981 (run3+run4).
  Итого $0.223297; обе серии в пределах лимитов, остатки не переносить.
- `backend/.venv` готов. `uv.lock` изменён дважды с разрешения пользователя 18.11
  (sentence-transformers + pgvector для 2.2; python-docx + pypdf для 2.5); дальше
  без отдельного решения не менять.
  Docker Desktop + WSL Ubuntu-24.04; compose `infra/docker-compose.yml`, PostgreSQL/Redis,
  миграция БД 0005 (head).
  PostgreSQL/Redis healthy через WSL на момент offline-проверки. Перед live проверить health; при необходимости запустить:
  `wsl.exe -d Ubuntu-24.04 -- docker compose -f /mnt/c/Users/user/PycharmProjects/PythonProject/ai_dnd/infra/docker-compose.yml up -d postgres redis`.
  В Git Bash путь `/mnt/c/...` искажается — добавлять `MSYS_NO_PATHCONV=1`.
  GUI Docker Desktop запускать только скрытым окном. Общий mypy cache при
  параллельных запусках повреждался: использовать приватный cache/no-incremental.
  API-ключ хранится только в локальном ignored `backend/.env`, не переносить.
- Не полностью механизированы rite three-round timer, bowl action, fear и
  advantage author hints; full 5e не заявлять. `SessionEnded` нет, спасение NPC
  для прототипа оценивается по фактам и повествованию; полного resume/RNG нет.
  P2: агентность ИИ, лишние checks и скрип колеса; 1.8 оценён (live 10/10 PASS).

Прочитай AGENTS.md, docs/HANDOFF.md и актуальный раздел docs/PLAN.md. Этап 1 закрыт
18.11 (1.7 демо до финала ru/en; 1.8 каталог 20 ситуаций покрыт полностью, offline 6/6
+ live 10/10 PASS, замеры стоимости, решение по моделям). **Этап 2 закрыт 18.11**:
2.1 хранение в базе (миграция 0004, publish/load, version pinning, консоль --from-db;
smoke 9/9 + 8/8), 2.2 индексация (миграция 0005, rag-нарезка/эмбеддинги, scenario_chunks,
индексация в воркере, консоль seed/reindex; smoke 13/13), 2.3 гибридный поиск + контекст
v2 (RRF, фильтр видимости в SQL, lookup_lore, бюджет токенов, кэш байт-в-байт; smoke
11/11 + 12/12), 2.4 память кампании (дословное окно N=12 + rolling-сводка раз в K=10
ходов, событие SummaryUpdated, журнал фактов; smoke 16/16, промпт не растёт на 130
ходах), 2.5 импорт из файла (importer: md/docx/pdf → structured LLM → YAML + отчёт;
смоки 17/17 + 14/14 + 10/10, live-приёмка $0.0154587 из $0.1, фикстура
well-of-forgotten-names). Ревью 2.1–2.3 (3 P1 + 4 P2) и 2.5 (4 P2) закрыты.
**Тестовый долг этапа 2 закрыт 18.11**: 175 новых тестов (importer 70, rag 53,
game 28, integration 24), pytest 815 passed + 26 integration, mypy/ruff 146,
import-linter 3. **Live-пробы 2.3/2.4 закрыты 18.11**: кэш dm_tools ≥ baseline,
факт вне сцены без dm-утечек, память PASS после фикса промпта сводки (личные
детали персонажей); $0.223297 двумя сериями по $0.2. Отчёт:
`docs/reviews/2026-11-18-stage-2-tests.md`. Следующий — этап 3 (веб, закрытая
бета: OAuth, фронтенд поверх API, WebSocket, экран игрока). Тесты этапов писать по
завершении этапа целиком (см. ROADMAP): оставшийся тестовый долг — 1.4–1.8. Готовые
пробы не повторять; новые платные прогоны только после отдельного решения;
frontend-изменения Codex не трогать; uv.lock без решения не менять
(sentence-transformers/pgvector и python-docx/pypdf добавлены по решениям 18.11).

## Независимое ревью 2.1–2.3 · 18.11.2026 (исправления закрыты)

Astra и Root подтвердили три P1: публичный факт из предварительного поиска терялся
перед повествованием при `stay`, локализованные имена статблоков срывали индексацию
`five-guardians`, а версия без совместимого предыдущего индекса могла получить пустой
индекс при отчёте об успехе. Четыре P2 касались запуска воркера из публикации, изменения
видимости повторным `seed`, несовпадения авторской ссылки шаблона с закреплённой версией
и успешного завершения `reindex --wait` при ошибке воркера.

Все три P1 и четыре P2 исправлены и перепроверены 18.11; при перепроверке дополнительно
закрыт P2 видимости повторной публикации без флага (см. «Итог приёмки» ниже). Приёмка
2.1–2.3 закрыта по офлайн-критериям. Доказательства: `.local/review_fixes_smoke_20261118.py` 10/10 (публичный
факт в narration при `stay`, dm отсекается; все 6 статблоков five-guardians локализуются),
`.local/index_reuse_smoke_20261118.py` 4/4 (без индекса/другая модель → полная индексация,
совместимая предыдущая → reuse), `.local/p2_fixes_smoke_20261118.py` 8/8 (видимость, шаблон,
reindex --wait, seed ставит задачу с предыдущей версией). End-to-end воркер: `seed
five-guardians.ru --wait` → 95 сущностей/166 фрагментов/6 статблоков без второй команды.
Регрессий нет: smoke контекста 2.3 12/12, памяти 2.4 16/16; ruff/mypy (80 файлов)/
import-linter (3 kept)/pytest 664 passed. Live-пробы (ответ по факту вне сцены, доля кэша
относительно 1.8, семантика памяти) выполнены и закрыты 18.11 — см. раздел «тесты этапа +
live-пробы 2.3/2.4» выше. Подробно — [отчёт ревью](reviews/2026-11-18-stage-2.1-2.3.md).

## Актуальный статус после независимой перепроверки исправлений 2.1–2.3

18.11 Astra и Root подтвердили закрытие семи исходных случаев. Приёмка исправлений
открыта по новому P2: повторный `seed` без `--visibility` записывает `public`
для ранее приватного сценария. При отсутствии флага следует сохранить имеющуюся
видимость, при явном значении — применить его. Проверка через CLI с настоящим YAML
и подставной БД воспроизвела переход `private → public`. Ruff и mypy прошли.
Условия проверки и открытые live-критерии приведены в [отчёте](reviews/2026-11-18-stage-2.1-2.3.md).

## Итог приёмки исправлений 2.1–2.3 · 18.11.2026

Семь исходных замечаний и дополнительный P2 видимости закрыты по офлайн-проверкам.
Повторная публикация без указания видимости сохраняет прежнее значение через CLI
и прямой вызов. Новая публикация сохраняет прежние значения по умолчанию для
каждой точки входа. Root подтвердил исправление в 36 разовых проверках, Astra
независимо проверила его в 14. Измерение полного контекста, live-ответ по факту
вне сцены и сравнение доли кэша с 1.8 остаются открыты. [Отчёт ревью](reviews/2026-11-18-stage-2.1-2.3.md)

## Планирование: новый этап 4 «Предметы и экипировка» (решение 18.11)

По решению пользователя добавлен отдельный этап 4 «Предметы и экипировка» — делается,
когда готов веб-скелет (этап 3). Прежние этапы 4/5/6 (Живой DM/Редакторы/Портал)
сдвинуты в 5/6/7; PLAN, ROADMAP и ссылка на карту в ARCHITECTURE перенумерованы.
Состав этапа 4: механика предметов (носимые, заточки оружия, одноразовые — свитки/эликсиры),
кукла (paper doll) в карточке, базовый сет картинок предметов (генерация), вкладка
«Предметы» в сценарии (соотнесение предмет↔картинка + парсинг свойств), переключатель
«в сценарии нужны предметы» (выкл → кукла и инвентарь скрыты, предметы только в памяти
без визуала). Подэтапы 4.1–4.6 в PLAN. Оценка ROADMAP ~10–14 активных дней. Решение по
архитектуре механики: модификаторы экипировки в движке (переиспользовать `Effect`/
`_Modifiers`), а не пересчёт карточки; носимые и заточки — общим слоем. Сейчас этап
запланирован, не начат; текущая работа — этап 2 (2.5 импорт).

## Планирование: подэтап 3.9 «Админка» (решение 18.11)

По решению пользователя вся админка заложена отдельным подэтапом 3.9 в этапе 3
(нужна для управления закрытой бетой на боевом сервере). Состав: раздел `/admin`
с серверной проверкой роли `admin`/`moderator` и журналом аудита; пользователи
(роли, бан/разбан, сброс сессий); кредиты и биллинг (корректировка баланса, история,
лимиты, страница расходов — API `/api/admin/expenses` уже готов); надзор за сессиями
(состав/состояние/остановка/отладка); сценарии и модерация (публикация/видимость,
очередь жалоб, скрытие); операционка (здоровье очередей/воркера, watcher баланса
DeepSeek, мониторинг/оповещения, аудит); базовая аналитика. Дубли убраны ссылками:
расходы и watcher в 3.6, модерация в 7.1, мониторинг в 7.5 → ядро в 3.9. ROADMAP:
этап 3 18–28 → 21–32 активных дней, итог ~85–122. Только планирование, код не менялся.
