# Настройки магазина

```bash
.venv/bin/python -m woo_catalog settings export --output settings.json
.venv/bin/python -m woo_catalog settings import --input settings.json --output plan.json
.venv/bin/python -m woo_catalog apply --plan-id PLAN_ID --output result.json --confirm-settings
```

Экспорт сохраняет все доступные API группы, но из каждой настройки берёт только изменяемые `id` и `value`; его можно сразу использовать для импорта. При необходимости оставьте только настройки, которые нужно изменить:

```json
{
  "schema_version": 1,
  "settings": [
    {
      "group_id": "general",
      "settings": [{"id": "woocommerce_currency", "value": "RUB"}]
    }
  ]
}
```

Импорт меняет только переданные `id` и `value`. План показывает значение до и после. Неизвестная, read-only или недоступная настройка попадает в preflight как `error`; apply невозможен до исправления документа. Любая запись настроек требует `--confirm-settings`.
