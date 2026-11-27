# Отчёт импорта: module.md

- Исходный файл: module.md
- Язык: ru

## Сущности

- scenes: 2
- npcs: 1
- items: 0
- clues: 1
- handouts: 0
- encounters: 0
- lore: 0
- statblocks: 0

## Проблемы валидации

- scenes.square.exits[0]: unknown_scene (scene='nowhere')

## Свойства предметов

предметов нет

## Заметки модели

### Допущение

- `scenes.square.checks.look` — DC взят 12.

## Вызовы модели

- Вызовов: 1
- Стоимость: $0

## Что дальше

1. Проверить и поправить YAML: `module.ru.yaml`.
2. Положить его в `content/scenarios/module.ru.yaml`.
3. Опубликовать: `ai-dnd-play seed --scenario module --lang ru`.
4. Играть: `ai-dnd-play play --scenario module --lang ru --from-db`.
