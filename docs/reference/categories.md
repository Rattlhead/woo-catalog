# Категории

## Endpoint

| Операция | WooCommerce endpoint |
| --- | --- |
| Экспорт | `GET products/categories` |
| Создание | `POST products/categories` |
| Обновление | `PUT products/categories/{id}` |

Идентификатор категории в JSON — `slug`.

## Команды

```bash
.venv/bin/python -m woo_catalog categories export --output categories.json
.venv/bin/python -m woo_catalog categories import --input categories.json --output plan.json
.venv/bin/python -m woo_catalog apply --plan-id PLAN_ID --output result.json
```

Отдельная команда не читает и не изменяет товары. Входной файл содержит только `schema_version: 1` и массив `categories`; непустой `products`, `tags` или `attributes` — ошибка.

## Поля `categories[]`

| Поле | Тип / допустимые значения | Назначение |
| --- | --- | --- |
| `name`, `slug` | непустые строки | обязательны; `slug` — идентификатор |
| `parent_slug` | строка или `null` | родительская категория; `null` делает категорию корневой |
| `description` | строка | описание |
| `display` | `default`, `products`, `subcategories`, `both` | режим отображения |
| `image` | объект WooCommerce image | изображение категории |

Если родитель из того же файла тоже создаётся, он обрабатывается первым. Неизвестный `parent_slug` приводит к `error` для категории.
