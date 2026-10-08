# Товары

## Endpoint и идентификатор

| Операция | WooCommerce endpoint | Идентификатор |
| --- | --- | --- |
| Найти | `GET products?sku={sku}` | `sku` |
| Создать | `POST products` | Новый `sku` |
| Обновить | `PUT products/{id}` | Найденный по `sku` `id` |
| Экспортировать | `GET products` | — |

Поддерживаются только существующие или создаваемые товары типа `simple`. Новый SKU по умолчанию пропускается; передайте `--create-products`, чтобы создать его.

## Импорт

```bash
.venv/bin/python -m woo_catalog import --input products.json --output plan.json
.venv/bin/python -m woo_catalog apply --plan-id PLAN_ID --output result.json
```

Для существующего товара достаточно `sku` и только изменяемых полей. `--match-by sku` — единственный способ сопоставления. Не включённые во входной файл SKU не изменяются.

### Создание товара

```bash
.venv/bin/python -m woo_catalog import --input products.json --output plan.json --plan --create-products
```

Для создания обязательны `sku`, `name` и явный `status`; создаётся `simple`-товар. Если флага нет, операция будет `skipped_new`. Товар не типа `simple` приводит к операции `error`. Создание сразу в `publish` и перевод существующего товара в `publish` требуют `--confirm-publish` при apply.

## Поля `products[]`

| Поле | Тип / допустимые значения | Использовать, когда |
| --- | --- | --- |
| `sku` | непустая строка, обязательно | всегда; ключ поиска |
| `name` | строка | изменить имя; обязательно для создания |
| `description` | строка | изменить полное HTML-описание |
| `short_description` | строка | изменить короткое описание |
| `slug` | строка | изменить URL slug |
| `status` | `draft`, `pending`, `private`, `publish` | изменить статус публикации |
| `regular_price`, `sale_price` | пустая или неотрицательная десятичная строка | изменить или очистить цену, например `"1990.00"` |
| `manage_stock` | boolean | включить/выключить складской учёт |
| `stock_quantity` | целое число ≥ 0 | изменить остаток при `manage_stock: true` |
| `categories` | массив ссылок `{name, slug}` | назначить или очистить категории |
| `categories_mode` | `replace` или `merge` | заменить набор или добавить к текущему |
| `tags`, `tags_mode` | ссылки и `replace`/`merge` | заменить/очистить или дополнить метки |
| `attributes`, `attributes_mode` | атрибуты и `replace`/`merge` | заменить/очистить или дополнить атрибуты |

Пример точечного обновления цены и остатков:

```json
{"schema_version": 1, "products": [{"sku": "OZN-12345", "regular_price": "1990.00", "manage_stock": true, "stock_quantity": 7}]}
```

Ссылки `categories` и `tags` требуют непустые `name` и `slug`. Для массивов режим по умолчанию — `replace`; пустой массив очищает набор. `merge` сначала читает текущие назначения и сохраняет их.

Глобальный объект `attributes[]` использует `scope: "global"`, `name`, `slug` и `options`. Локальный использует `scope: "local"`, `name` и `options`, но не содержит `slug`. Оба вида принимают `visible`, `variation` и `position`. Указание глобального справочника создаёт недостающий справочник и terms только для валидного обновляемого или разрешённого к созданию SKU.

## Экспорт

```bash
.venv/bin/python -m woo_catalog export --resource products --output products.json
```

Экспорт сохраняет вложенные категории, метки и различие глобальных/локальных атрибутов. `images` намеренно не включаются.
