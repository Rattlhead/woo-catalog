# Метки

## Endpoint

| Операция | WooCommerce endpoint |
| --- | --- |
| Экспорт | `GET products/tags` |
| Создание | `POST products/tags` |
| Обновление | `PUT products/tags/{id}` |

Идентификатор метки в JSON — `slug`.

## Команды

```bash
.venv/bin/python -m woo_catalog tags export --output tags.json
.venv/bin/python -m woo_catalog tags import --input tags.json --output plan.json
.venv/bin/python -m woo_catalog apply --plan-id PLAN_ID --output result.json
```

Отдельная команда не читает и не изменяет товары. Входной файл содержит только `schema_version: 1` и массив `tags`.

## Поля `tags[]`

| Поле | Тип | Назначение |
| --- | --- | --- |
| `name`, `slug` | непустые строки | обязательны; `slug` — идентификатор |
| `description` | строка | описание метки |
