# Инфраструктура

Сервисы для локальной разработки — в [docker-compose.yml](docker-compose.yml). Сам бэкенд (api и worker) запускается локально через `uv`, см. [README в корне](../README.md).

| Сервис | Образ | Адрес | Зачем |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg18` | `localhost:5432` | Основная база. Пользователь, пароль и база — `ai_dnd` |
| `redis` | `redis:8-alpine` | `localhost:6379` | Очередь задач, pub/sub, блокировки |
| `s3` | `chrislusf/seaweedfs` | `http://localhost:8333` | S3-совместимое хранилище файлов (SeaweedFS). Локально без авторизации |

Порты открыты только на `127.0.0.1`.

## Команды

Из корня репозитория:

```bash
docker compose -f infra/docker-compose.yml up -d      # запустить
docker compose -f infra/docker-compose.yml ps         # состояние
docker compose -f infra/docker-compose.yml logs -f    # логи
docker compose -f infra/docker-compose.yml down       # остановить, данные остаются
docker compose -f infra/docker-compose.yml down -v    # остановить и удалить данные
```

## База для тестов

При первом запуске скрипт из [postgres/init](postgres/init) создаёт вторую базу — `ai_dnd_test`. С ней работают интеграционные тесты, чтобы не трогать данные разработки.

Скрипт выполняется только при создании тома `postgres-data`. Если том появился раньше скрипта, базу можно создать вручную:

```bash
docker compose -f infra/docker-compose.yml exec postgres createdb -U ai_dnd ai_dnd_test
```
