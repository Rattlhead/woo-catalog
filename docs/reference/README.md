# Справочник для быстрого поиска

Читайте только строку, соответствующую задаче, затем указанный файл. Все пути API ниже относятся к WooCommerce REST API `wc/v3`.

| Нужно сделать | Команда / ключевой аргумент | Читать |
| --- | --- | --- |
| Проверить JSON без сети | `validate --input FILE --output FILE` | [Команды](commands.md#validate) |
| Выгрузить весь каталог | `export --resource all --output DIRECTORY` | [Команды](commands.md#export) |
| Выгрузить только товары | `export --resource products --output FILE` | [Товары](products.md#экспорт) |
| Спланировать создание или обновление | `import --input FILE --output FILE --plan` | [Товары](products.md#импорт) |
| Применить просмотренный план | `apply --plan-id ID --output FILE` | [Команды](commands.md#import) |
| Проверить журнал / восстановить uncertain | `status --plan-id ID [--reconcile]` | [Команды](commands.md#import) |
| Разрешить создание товара | `import ... --plan --create-products` | [Товары](products.md#создание-товара) |
| Прочитать один SKU без полного экспорта | MCP `catalog_get_product` | [MCP](mcp.md#tools) |
| Искать справочник с пагинацией | MCP `catalog_search_taxonomy` | [MCP](mcp.md#tools) |
| Понять поле товара | JSON-объект `products[]` | [Товары](products.md#поля-products) |
| Работать с категориями без товаров | `categories export/import` | [Категории](categories.md) |
| Работать с метками без товаров | `tags export/import` | [Метки](tags.md) |
| Работать с глобальными атрибутами и terms | `attributes export/import` | [Атрибуты](attributes.md) |
| Спланировать удаление явного списка | `delete --input FILE --output FILE --plan` | [Удаление](deletion.md) |
| Экспортировать или изменить настройки магазина | `settings export/import` | [Настройки](settings.md) |
| Использовать каталог из Codex/GPT | локальный MCP `woo-catalog` | [MCP](mcp.md) |
| Понять общие правила JSON | `schema_version`, запрет неизвестных полей, очистка массивом | [Контракт JSON](json-contract.md) |
| Расшифровать результат или код завершения | `--verbose`, `summary`, `operations` | [Команды](commands.md#результаты-и-коды-завершения) |

## Быстрые инварианты

- Идентификатор товара — только `sku`; `--match-by sku` является единственным допустимым значением.
- `schema_version` всегда равен `1`; неизвестные поля и `images` запрещены.
- Без `--apply` импорт сохраняет план. Без `--create-products` товар с новым SKU получает `skipped_new`.
- Отдельный импорт категории, метки или атрибута принимает только одноимённый массив и не читает товары.

## Карта файлов

- [commands.md](commands.md) — синтаксис CLI, общие флаги, режимы и отчёты.
- [products.md](products.md) — товары, их поля и endpoint `products`.
- [categories.md](categories.md) — категории и endpoint `products/categories`.
- [tags.md](tags.md) — метки и endpoint `products/tags`.
- [attributes.md](attributes.md) — глобальные атрибуты, terms и endpoint `products/attributes`.
- [json-contract.md](json-contract.md) — правила, одинаковые для всех входных JSON.
- [deletion.md](deletion.md) — мягкое удаление товаров и подтверждённое окончательное удаление справочников.
- [settings.md](settings.md) — экспорт и точечное изменение групп WooCommerce settings.
- [mcp.md](mcp.md) — локальный MCP-сервер для Codex/GPT.
