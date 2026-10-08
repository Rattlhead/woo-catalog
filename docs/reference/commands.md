# Команды, режимы и отчёты

## Общие правила вывода

Каждая команда сохраняет результат в `--output` и печатает в stdout ровно одну локальную ссылку `file://` на созданный документ. Добавьте `--verbose`, чтобы после ссылки напечатать JSON результата.

| Флаг | Где доступен | Значение |
| --- | --- | --- |
| `--input PATH` | `validate`, все `import` | Входной JSON. Обязателен. |
| `--output PATH` | все команды | Файл результата; для `export --resource all` — каталог. Обязателен. В командах экспорта абсолютный путь используется как указан, а относительный сохраняется внутри `exports/<host>/`, где `<host>` берётся из `WOO_BASE_URL`. |
| `--env-file PATH` | все операции с WooCommerce | Файл ключей; по умолчанию `.env`. |
| `--plan` | `import`, `settings import`, `delete`, импорты справочников | Явно создать сохранённый план; без `--apply` это и так режим по умолчанию. |
| `--apply` | те же команды | Переходный однопроцессный plan+apply; надёжнее отдельная команда `apply --plan-id`. |
| `--state-file PATH` | plan/apply/status | SQLite с планами и журналом; по умолчанию `.woo-catalog/state.sqlite3`. |
| `--allow-large-plan` | команды планирования | Разрешить план свыше безопасного лимита; apply затем требует `--confirm-large-plan`. |
| `--verbose` | все команды | Напечатать сохранённый JSON после `file://` URI. |

## `diagnose`

```bash
.venv/bin/python -m woo_catalog diagnose --output diagnostics.json
```

Выполняет только GET/OPTIONS к WordPress REST index, Woo catalog, settings и product batch. Отчёт показывает доступность маршрутов и безопасные HTTP-детали. Проверка не делает тестовую запись, поэтому `write_permission` всегда имеет значение `not_probed`.

## `mcp setup`

```bash
.venv/bin/python -m woo_catalog mcp setup
```

Регистрирует локальный stdio MCP-сервер в Codex через `codex mcp add`. Использует имя `woo-catalog`, текущий Python и абсолютный путь к `.env`; этот путь передаётся серверу через окружение, поэтому подключение работает из другого проекта. Команда не создаёт отчётный JSON и печатает результат Codex. Если `.env` отсутствует или имя уже зарегистрировано, ничего не заменяется.

## `validate`

```bash
.venv/bin/python -m woo_catalog validate --input desired.json --output validation.json
```

Проверяет JSON локально, без HTTP. Результат содержит канонический `document` с сохранённой разницей между отсутствующим полем и явным `null`, а также `report`. Код завершения: `0` для корректной схемы, `2` при ошибке схемы.

## `export`

```bash
.venv/bin/python -m woo_catalog export --resource all --output exports
.venv/bin/python -m woo_catalog export --resource products --output products.json
```

`--resource` принимает `products`, `categories`, `tags`, `attributes` или `all` (по умолчанию). При `all` создаются четыре JSON-файла и `export-manifest.json`; при одном ресурсе создаётся один JSON.

Абсолютный `--output` пишет ровно в указанное место. Относительный `--output` считается от папки сайта `exports/<host>/`, например при `WOO_BASE_URL=https://shop.example` команда `--output catalog` создаст документы в `exports/shop.example/catalog/`.

## `import`

```bash
.venv/bin/python -m woo_catalog import --input desired.json --output plan.json --plan
.venv/bin/python -m woo_catalog import --input desired.json --output batch-plan.json --batch-products
.venv/bin/python -m woo_catalog apply --plan-id PLAN_ID --output apply.json
.venv/bin/python -m woo_catalog status --plan-id PLAN_ID --output status.json
```

`--match-by sku` поддерживается всегда и уже выбран по умолчанию. `--create-products` разрешает создать новый **simple**-товар, когда переданы `sku`, `name` и `status`.

`--batch-products` сохраняет в плане opt-in режим официального Woo endpoint `products/batch`; он затрагивает только независимые create/update товаров и объединяет не более 100 операций. Apply использует сохранённый режим, а удаления остаются последовательными.

План хранит точные payload и preconditions 15 минут. Просмотрите `operations[].before`, `after`, `changes`, `risks`, `message` и `required_confirmations`. Команда `apply` сама является общим подтверждением; риски требуют соответствующие флаги: `--confirm-publish`, `--confirm-settings`, `--confirm-permanent`, `--confirm-large-plan`. Если использован нестандартный `--state-file`, передайте тот же путь в `apply` и `status`.

`status --reconcile` читает WooCommerce для операций в состоянии `uncertain`. Он не повторяет запись. Повторный `apply` разрешён только после результата `retryable`; `conflict` требует нового плана.

Для операции только над одной сущностью используйте её отдельную команду, а не общий `import`: [категории](categories.md#команды), [метки](tags.md#команды), [атрибуты](attributes.md#команды).

## Результаты и коды завершения

План содержит `plan_id`, `state`, `document_sha256`, `expires_at`, сводку и массив операций. Результат apply содержит итоговое состояние и состояния операций: `verified`, `conflict`, `blocked_dependency`, `failed`, `failed_verification`, `uncertain` или `retryable`. Status добавляет журнал событий.

Код `0` означает проверенный успех или план без preflight-ошибок. Код `1` означает runtime-ошибку, `partial`, `conflict`, `uncertain` или `failed`; код `2` — ошибку входной схемы. Экспорт возвращает `1` при ошибке API. Stdout остаётся одной ссылкой; предупреждение совместимого прямого `--apply` идёт в stderr.
