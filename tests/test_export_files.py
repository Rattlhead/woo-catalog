import json

import pytest

from woo_catalog.export_files import write_export_documents
from woo_catalog.schemas import CatalogSnapshot


def snapshot():
    return CatalogSnapshot.model_validate(
        {
            "schema_version": 1,
            "categories": [{"name": "Clothing", "slug": "clothing"}],
            "tags": [{"name": "New", "slug": "new"}],
            "attributes": [{"name": "Color", "slug": "pa_color", "terms": ["Red", "Blue"]}],
            "products": [{"sku": "OZN-1", "name": "Shirt", "categories": [{"name": "Clothing", "slug": "clothing"}]}],
        }
    )


def test_categories_export_as_json_preserves_all_category_settings(tmp_path):
    output = tmp_path / "categories.json"

    result = write_export_documents(snapshot(), resource="categories", output=output)

    assert result["documents"]["categories"] == output.resolve().as_uri()
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "categories": [{"name": "Clothing", "slug": "clothing"}],
        "tags": [],
        "attributes": [],
        "products": [],
    }


def test_products_export_as_json_because_the_data_is_nested(tmp_path):
    output = tmp_path / "products.json"

    result = write_export_documents(snapshot(), resource="products", output=output)

    assert result["documents"]["products"] == output.resolve().as_uri()
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "categories": [],
        "tags": [],
        "attributes": [],
        "products": [{"sku": "OZN-1", "name": "Shirt", "categories": [{"name": "Clothing", "slug": "clothing"}]}],
    }


def test_all_export_writes_separate_documents_and_a_manifest(tmp_path):
    result = write_export_documents(snapshot(), resource="all", output=tmp_path / "exports")

    assert set(result["documents"]) == {"products", "categories", "tags", "attributes"}
    assert (tmp_path / "exports" / "products.json").exists()
    assert (tmp_path / "exports" / "categories.json").exists()
    assert (tmp_path / "exports" / "tags.json").exists()
    assert (tmp_path / "exports" / "attributes.json").exists()
    assert (tmp_path / "exports" / "export-manifest.json").exists()


def test_category_export_rejects_a_misleading_csv_extension(tmp_path):
    with pytest.raises(ValueError, match=".json"):
        write_export_documents(snapshot(), resource="categories", output=tmp_path / "categories.csv")
