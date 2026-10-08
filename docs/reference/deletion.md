# Удаление каталоговых данных

Команда принимает только явно перечисленные объекты и по умолчанию создаёт сохранённый план без записей.

```bash
.venv/bin/python -m woo_catalog delete --input delete.json --output plan.json
.venv/bin/python -m woo_catalog apply --plan-id PLAN_ID --output result.json --confirm-permanent
```

```json
{
  "schema_version": 1,
  "products": ["OZN-12345"],
  "categories": ["old-category"],
  "tags": ["old-tag"],
  "attributes": [
    {"slug": "pa_color", "terms": ["Red"]},
    {"slug": "pa_size", "terms": []}
  ]
}
```

`products` использует SKU и при применении перемещает товар в корзину. Категории, метки, атрибуты и terms не имеют корзины: план фиксирует их числовые ID, preconditions и связанные товары, а отдельный apply требует `--confirm-permanent`. Пустой `terms` выбирает весь глобальный атрибут; непустой — только указанные terms. Список затронутых SKU ограничен 20 элементами и сопровождается `count`, `truncated` и `complete`.

Переходная команда `delete ... --apply` использует тот же сохранённый план, но запрашивает `yes` в stdin и предупреждает об отсутствии отдельного шага просмотра.
