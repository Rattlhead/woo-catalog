# Глобальные атрибуты

## Endpoint

| Операция | WooCommerce endpoint |
| --- | --- |
| Экспорт атрибутов | `GET products/attributes` |
| Экспорт значений | `GET products/attributes/{id}/terms` |
| Создание атрибута | `POST products/attributes` |
| Обновление атрибута | `PUT products/attributes/{id}` |
| Создание значения | `POST products/attributes/{id}/terms` |

Идентификатор атрибута в JSON — `slug`; значение (term) идентифицируется названием.

## Команды

```bash
.venv/bin/python -m woo_catalog attributes export --output attributes.json
.venv/bin/python -m woo_catalog attributes import --input attributes.json --output plan.json
.venv/bin/python -m woo_catalog apply --plan-id PLAN_ID --output result.json
```

Отдельная команда не читает и не изменяет товары. Входной файл содержит только `schema_version: 1` и массив `attributes`.

## Поля `attributes[]`

| Поле | Тип / значение по умолчанию | Назначение |
| --- | --- | --- |
| `name`, `slug` | непустые строки | обязательны; `slug` — идентификатор |
| `type` | только `select`; по умолчанию `select` | тип глобального атрибута |
| `order_by` | `menu_order` (по умолчанию), `name`, `name_num`, `id` | сортировка значений |
| `has_archives` | boolean, по умолчанию `false` | включить архивы |
| `terms` | массив строк, по умолчанию `[]` | создать недостающие значения |

`terms` добавляет отсутствующие значения, но не удаляет уже существующие. Чтобы назначить атрибут товару, используйте объект из [поля `products[].attributes`](products.md#поля-products).
