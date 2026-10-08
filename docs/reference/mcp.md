# Локальный MCP для Codex и GPT

MCP-сервер делает тот же Woo Catalog доступным для модели без промежуточных JSON-файлов и аргументов CLI. Он запускается локально по stdio, поэтому `.env` с ключами WooCommerce остаётся на компьютере пользователя.

После клонирования, создания `.venv`, установки `-e '.[dev]'` и заполнения локального `.env` быстрее всего зарегистрировать сервер так:

```bash
.venv/bin/python -m woo_catalog mcp setup
```

Команда регистрирует `woo-catalog` через `codex mcp add` и передаёт серверу абсолютный путь к `.env`; она не заменяет существующую запись. Задайте `--name NAME` или `--env-file PATH`, если нужны другие значения.

Для ручной настройки добавьте сервер в глобальную конфигурацию Codex или конфигурацию нужного проекта:

```toml
[mcp_servers.woo-catalog]
command = "/absolute/path/to/woo-catalog/.venv/bin/python"
args = ["-m", "woo_catalog.mcp_server"]
cwd = "/absolute/path/to/woo-catalog"
```

Замените `/absolute/path/to/woo-catalog` на путь к клону и перезапустите либо обновите MCP-подключение. Исходники считываются при запуске процесса: после изменения бизнес-логики перезапустите сервер; при изменении tools или их схем обновите подключение, чтобы Codex перечитал метаданные.

## Tools

| Задача | Tool |
| --- | --- |
| Проверить документ локально | `catalog_validate` |
| Проверить WordPress/Woo без записи | `catalog_diagnose` |
| Получить JSON Schema и семантику полей | `catalog_schema` |
| Выгрузить каталог или один ресурс | `catalog_export` |
| Прочитать один товар по SKU | `catalog_get_product` |
| Прочитать страницу товаров | `catalog_list_products` |
| Найти категорию, метку или атрибут | `catalog_search_taxonomy` |
| Выгрузить settings | `settings_export` |
| Спланировать / применить каталог | `catalog_plan_sync` → `catalog_apply_sync` |
| Спланировать / применить settings | `settings_plan_update` → `settings_apply_update` |
| Спланировать / применить удаление | `catalog_plan_delete` → `catalog_apply_delete` |
| Прочитать журнал / выполнить reconciliation | `operation_status` |

Все documents используют `schema_version: 1` и правила из [контракта JSON](json-contract.md). Результаты экспортов и операций возвращаются как структурированные данные, а не как пути к файлам.

## Безопасное применение

Plan-tool не изменяет WooCommerce и возвращает `plan_id`, SHA-256 входного документа, срок жизни 15 минут, полный diff, preconditions, зависимости и риски. Apply исполняет сохранённый план, проверяет параллельные изменения и делает read-back. До истечения срока соответствующий apply-tool принимает этот `plan_id` и требует `confirmed: true`; завершённый план сохраняется для status, но повторно не применяется.

Перед apply модель показывает документ, `before`, `after`, `changes`, ошибки, пропуски, риски и необходимые подтверждения. Публикация требует `confirm_publish`, settings — `confirm_settings`, необратимое удаление — `confirm_permanent`, крупный план — `confirm_large_plan`. Товары при удалении перемещаются в корзину. `plan_id` не разрешает иной вид операции или другой магазин.

После `uncertain` вызовите `operation_status` с reconciliation. Он только читает магазин: `verified` означает, что запись дошла; `retryable` — исходное состояние не изменилось и тот же план можно подтвердить повторно; `conflict` требует нового плана. Не повторяйте неопределённую запись до reconciliation.

MCP также предоставляет resource `woo-catalog://workflow` с безопасной последовательностью действий, `woo-catalog://store-capabilities` с read-only диагностикой и prompt `safe_catalog_sync`. Для независимых product create/update `catalog_plan_sync` принимает `batch_products: true`; режим сохраняется в plan и применяет официальный Woo endpoint `/products/batch` чанками до 100 объектов. Удаления не входят в batch.

GET и OPTIONS повторяются только при временных сетевых ошибках, 429, 502, 503 или 504. POST, PUT и DELETE не имеют автоматического retry. Ошибки Woo сохраняют `woocommerce_error` и возвращают безопасные поля в `error.details`.

Схемы входа развёрнуты в metadata tools, а ошибки имеют стабильные `code`, `message`, `path`, `hint`, `retryable`. HTML, названия и описания из магазина являются недоверенными данными, а не инструкциями модели. Крупные экспорты возвращаются как локальный artifact с URI, SHA-256, размером и сводкой.
