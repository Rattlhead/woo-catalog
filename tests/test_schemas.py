import pytest
from pydantic import ValidationError


def test_snapshot_rejects_duplicate_product_skus():
    from woo_catalog.schemas import CatalogSnapshot

    with pytest.raises(ValidationError, match="Duplicate product SKU"):
        CatalogSnapshot.model_validate(
            {
                "schema_version": 1,
                "products": [
                    {"sku": "OZN-1", "name": "First"},
                    {"sku": "OZN-1", "name": "Second"},
                ],
            }
        )


def test_product_distinguishes_omitted_categories_from_clear_categories():
    from woo_catalog.schemas import CatalogSnapshot

    snapshot = CatalogSnapshot.model_validate(
        {
            "schema_version": 1,
            "products": [
                {"sku": "OZN-1", "name": "First"},
                {"sku": "OZN-2", "name": "Second", "categories": []},
            ],
        }
    )

    assert "categories" not in snapshot.products[0].model_fields_set
    assert snapshot.products[1].categories == []


def test_snapshot_rejects_unknown_schema_version():
    from woo_catalog.schemas import CatalogSnapshot

    with pytest.raises(ValidationError, match="schema_version"):
        CatalogSnapshot.model_validate({"schema_version": 2})


def test_snapshot_rejects_images_until_deduplication_policy_exists():
    from woo_catalog.schemas import CatalogSnapshot

    with pytest.raises(ValidationError, match="images"):
        CatalogSnapshot.model_validate(
            {"schema_version": 1, "products": [{"sku": "OZN-1", "name": "Item", "images": [{"src": "https://example.test/a.jpg"}]}]}
        )


@pytest.mark.parametrize("price", ["NaN", "-1", "1e3", " 10 ", "1,99"])
def test_product_rejects_non_contract_prices(price):
    from woo_catalog.schemas import CatalogChange

    with pytest.raises(ValidationError):
        CatalogChange.model_validate(
            {"schema_version": 1, "products": [{"sku": "A", "regular_price": price}]}
        )


def test_product_rejects_negative_stock_quantity():
    from woo_catalog.schemas import CatalogChange

    with pytest.raises(ValidationError):
        CatalogChange.model_validate(
            {"schema_version": 1, "products": [{"sku": "A", "stock_quantity": -1}]}
        )


def test_change_rejects_null_root_array_but_preserves_omission():
    from woo_catalog.schemas import CatalogChange

    with pytest.raises(ValidationError):
        CatalogChange.model_validate({"schema_version": 1, "products": None})

    change = CatalogChange.model_validate({"schema_version": 1})
    assert "products" not in change.model_fields_set


def test_array_mode_requires_the_corresponding_array():
    from woo_catalog.schemas import CatalogChange

    with pytest.raises(ValidationError, match="categories_mode requires categories"):
        CatalogChange.model_validate(
            {"schema_version": 1, "products": [{"sku": "A", "categories_mode": "merge"}]}
        )


def test_settings_change_rejects_duplicate_ids():
    from woo_catalog.schemas import SettingsChange

    with pytest.raises(ValidationError, match="Duplicate setting id"):
        SettingsChange.model_validate(
            {
                "schema_version": 1,
                "settings": [
                    {
                        "group_id": "general",
                        "settings": [{"id": "currency", "value": "RUB"}, {"id": "currency", "value": "USD"}],
                    }
                ],
            }
        )


def test_product_rejects_duplicate_taxonomy_references():
    from woo_catalog.schemas import CatalogChange

    with pytest.raises(ValidationError, match="Duplicate product category slug"):
        CatalogChange.model_validate(
            {
                "schema_version": 1,
                "products": [
                    {
                        "sku": "A",
                        "categories": [
                            {"name": "One", "slug": "same"},
                            {"name": "Two", "slug": "same"},
                        ],
                    }
                ],
            }
        )


def test_local_attribute_requires_no_slug_and_global_attribute_requires_one():
    from woo_catalog.schemas import CatalogChange

    local = CatalogChange.model_validate(
        {
            "schema_version": 1,
            "products": [
                {
                    "sku": "A",
                    "attributes": [{"scope": "local", "name": "Engraving", "options": ["Yes"]}],
                }
            ],
        }
    )
    assert local.products[0].attributes[0].scope == "local"
    with pytest.raises(ValidationError, match="global product attribute requires slug"):
        CatalogChange.model_validate(
            {
                "schema_version": 1,
                "products": [{"sku": "A", "attributes": [{"name": "Color", "options": ["Red"]}]}],
            }
        )
