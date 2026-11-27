# Ревью: надёжность промптов и памяти (25.11.2026)

> Оригинал этого документа был обнулён крахом файловой системы 25.11 ~14:23
> (5432 байт NUL); содержимое в логах Codex не сохранилось. Это краткое
> восстановление из `docs/HANDOFF.md` и фактического кода, проверенного Root.
> Детальные формулировки исходного ревью утрачены.

Скоуп: замечания по промптам/памяти/шлюзу; `free` по решению пользователя не менялся.
Работа локальная, без коммитов и деплоя; VM не затрагивалась.

## A. Память и история

- Память восстанавливается из БД по публичным `Narrated`/`PlayerActed`: ограниченный
  хвост, абсолютный ordinal и `last_seq`, последние 12 пар ролей, несуммаризированный
  мост в первом user-сообщении.
- Старый журнал (130 ходов, cursor=0) догоняется батчами до 64; максимум две успешные
  сводки на job; preflight уменьшает слишком большой батч (64/32 → 16), курсор
  сохраняется; пустая сводка отклоняется.
- Ошибка генерации после `Narrated` — мягкая; `SummaryCommitError` останавливает job
  до reload, не создавая нового служебного `Narrated`.
- Redis lease проверяет токен перед commit (защита от перезаписи устаревшим владельцем).

## B. Терминальный бой (потеря `PartyDefeated`)

Проблема: после `CombatEnded` новая ошибка `finish_reason=length` (или `BillingStop`)
могла оставить бой закрытым без финального `Narrated` и без маркера `PartyDefeated`.

Исправление (проверено Root в коде и smoke):
- `GameMaster.finish_terminal_combat_fallback()` — резервный финал RU/EN без вызова
  модели, только если в combat log есть `CombatEnded`.
- `_commit_combat_narration()` — атомарный append `Narrated` (+ `PartyDefeated` при
  поражении) одним журнальным снимком.
- `narrate_combat()` — при `LLMError` на оконченном бое и при пустом логе с `CombatEnded`
  вызывает fallback; обрезанный текст (`output_truncated`) в журнал не попадает.
- web-слой `realtime/turns.py` — `BillingStop` в цикле `round_finished` закрывает бой
  через fallback, иначе пробрасывается.

## C. Приватность промптов

- Публичные system/opening/turn промпты нарратора больше не получают DM synopsis,
  notes, NPC secrets/personality, скрытые DM entities, prepared checks, `state.facts`
  и глобальный каталог; planner сохраняет полный контекст.
- Раскрытые DM entities доступны после настоящего reveal.
- Нарративный ledger `lookup_lore` — только public; `note_fact`, сырые аргументы и
  `author_hint` исключены.
- Target/delta — через whitelist фактических публичных событий, без `ActorsJoined`
  и тактик.

## Шлюз и бюджет

- Оценка входа по UTF-8 байтам и framing (не точный tokenizer): app budget 131072 +
  резерв `max_tokens+1024`; import/sheet_gen override 524288 под `MAX_IMPORT_CHARS`.
- Narration `max_tokens` 1600 (было 800); температуры и thinking не менялись.
- `finish_reason=length` → `output_truncated` без обычного финального StreamEnd;
  подтверждённая платная стоимость учитывается и при ошибке structured JSON,
  `BillingGuard` её закрывает. Typed RU/EN ошибки не повторяют committed effects.

## Проверки (финальные, Root, 25.11 вечер)

- `.local/terminal_combat_smoke_20261125.py` (временные PG :55437 `combat_smoke`,
  Redis :55438): PASS 3/3 — defeat/length, defeat/billing_stop, victory/stop;
  атомарность маркера, отсутствие обрезанного текста, идемпотентность повторного шага.
- `.local/prompt_memory_pg_smoke_20261125.py`: PASS (26 jobs, bridge, отказ summary,
  legacy 130 двумя батчами, adaptive oversize, пустая сводка/курсор, post-commit
  publish failure → reload/replay, private/public и target/delta).
- `.local/gateway_budget_smoke_20261125.py`: PASS.
- pytest 819 passed / 26 deselected; ruff check + format PASS; import-linter
  3 kept / 0 broken; mypy src — только предсуществующие `scenarios/store.py:135,138`
  (чужая ветка).

## Инцидент и восстановление

Крах ~14:23 обнулил `game/master.py` и этот документ. `master.py` восстановлен
реплеем 16 патчей из логов Codex на dangling blob `b0e6298a`; байткод после патча 14
идентичен `master.cpython-312.pyc`, финальный размер совпадает. Единственное
дополнительное изменение — `ruff format` последнего патча. Артефакты:
`.local/recovery-20261125/`.

## Долг

- Live QA нарратора с реальным DeepSeek не проводилась: нарратор лишился части
  private personality и DM hooks, семантической гарантии нет (платный прогон — по
  решению пользователя).
- Постоянные тесты этапа отложены до работающего прототипа (решение пользователя);
  смоки выше — разовые.
- Предсуществующие ошибки mypy `scenarios/store.py:135,138` — чужая ветка v0.2.