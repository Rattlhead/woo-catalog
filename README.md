# Woo Catalog — локальный MCP-сервер для WooCommerce

Woo Catalog подключает WooCommerce к Codex и другим MCP-клиентам. Локальный Python-сервер работает по stdio и предоставляет инструменты для чтения каталога, проверки данных, экспорта и безопасного планирования изменений. Сервер поддерживает товары, категории, метки, атрибуты и настройки магазина.

Изменения сначала формируются в план с точным сравнением состояния магазина. Применение требует явного подтверждения, проверяет условия плана и перечитывает изменённые данные. Ключи API хранятся в локальном `.env` и не попадают в ответы MCP. Сервер не повторяет запросы на запись автоматически.

В проект также входит CLI для пакетной работы с версионированными JSON-файлами. Этот режим позволяет подготовить и проверить данные без передачи API-ключей нейросети или скрипту.

## Быстрый старт

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env

# Проверить JSON: сеть и магазин не затрагиваются.
.venv/bin/python -m woo_catalog validate --input desired.json --output validation.json

# Проверить WordPress/WooCommerce endpoints и доступность batch без записи.
.venv/bin/python -m woo_catalog diagnose --output diagnostics.json

# Создать сохранённый план: магазин только читается.
.venv/bin/python -m woo_catalog import --input desired.json --output plan.json --plan

# Для независимых create/update товаров можно сохранить opt-in batch-режим Woo.
.venv/bin/python -m woo_catalog import --input desired.json --output plan.json --batch-products

# Просмотреть plan.json, затем применить ровно этот plan_id и проверить журнал.
.venv/bin/python -m woo_catalog apply --plan-id PLAN_ID --output apply-report.json
.venv/bin/python -m woo_catalog status --plan-id PLAN_ID --output status.json

# Выгрузить и затем точечно применить настройки магазина.
.venv/bin/python -m woo_catalog settings export --output settings.json
.venv/bin/python -m woo_catalog settings import --input settings.json --output settings-plan.json --plan

# Спланировать удаление. Необратимый apply потребует --confirm-permanent.
.venv/bin/python -m woo_catalog delete --input delete.json --output delete-report.json
```

Заполните `.env` ключами из WooCommerce → **Settings → Advanced → REST API**. Используются HTTPS и REST API `wc/v3`.
Для команд экспорта относительный `--output` сохраняется внутри `exports/<host>/`, где `<host>` берётся из `WOO_BASE_URL`; абсолютный путь используется без изменений.

## Использование из Codex через MCP

После нового клона репозитория подготовьте окружение в его корне:

```bash
git clone <repository-url> woo-catalog
cd woo-catalog
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Заполните `.env` ключами нужного магазина. Не добавляйте этот файл в Git.

Для быстрой регистрации сервера в Codex выполните из корня клона:

```bash
.venv/bin/python -m woo_catalog mcp setup
```

Команда вызывает `codex mcp add`, регистрирует имя `woo-catalog` и передаёт серверу абсолютный путь к `.env`. Она не перезаписывает существующую запись с таким именем. Для другого имени или файла ключей используйте `mcp setup --name NAME --env-file PATH`.

Затем добавьте сервер в глобальную конфигурацию Codex (для всех проектов) или в конфигурацию конкретного проекта. Замените оба `/absolute/path/to/woo-catalog` на абсолютный путь к созданному клону:

```toml
[mcp_servers.woo-catalog]
command = "/absolute/path/to/woo-catalog/.venv/bin/python"
args = ["-m", "woo_catalog.mcp_server"]
cwd = "/absolute/path/to/woo-catalog"
```

Перезапустите или обновите MCP-подключение в Codex. Сервер использует `.env` только локально. Модель получает структурированные документы и отчёты, но не ключи API. Перед любой записью он обязан вызвать `*_plan_*`, показать точные `before`, `after`, `changes`, риски и ошибки, получить явное подтверждение и только затем вызвать соответствующий `*_apply_*`. После записи следует проверить `operation_status`; при `uncertain` сначала выполнить reconciliation, не повторяя запись вслепую. Полный список tools — в [справочнике MCP](docs/reference/mcp.md). Готовый prompt и диагностика находятся в [инструкции оператора](docs/agent-guides/operator-quickstart.md).

## Найти нужный аргумент, JSON-поле или endpoint

Откройте [индекс справочника](docs/reference/README.md): в нём одна таблица «задача → команда → файл». Подробные правила совместимости находятся в [CONTRACTS.md](CONTRACTS.md).

## Лицензия

Проект распространяется по лицензии [MIT](LICENSE).
