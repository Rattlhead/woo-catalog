from woo_catalog.exporter import export_attributes, export_catalog, export_categories, export_tags


class FakeWooClient:
    def __init__(self):
        self.paths = []
        self.responses = {
            "products/categories": [
                {"id": 10, "name": "Clothing", "slug": "clothing", "parent": 0, "description": ""}
            ],
            "products/tags": [{"id": 20, "name": "New", "slug": "new", "description": ""}],
            "products/attributes": [
                {"id": 30, "name": "Color", "slug": "pa_color", "type": "select", "order_by": "name", "has_archives": False}
            ],
            "products/attributes/30/terms": [{"id": 31, "name": "Red", "slug": "red"}],
            "products": [
                {
                    "id": 40,
                    "sku": "OZN-40",
                    "name": "T-shirt",
                    "type": "simple",
                    "status": "publish",
                    "regular_price": "1000",
                    "sale_price": "",
                    "manage_stock": True,
                    "stock_quantity": 4,
                    "categories": [{"id": 10, "name": "Clothing", "slug": "clothing"}],
                    "tags": [{"id": 20, "name": "New", "slug": "new"}],
                    "attributes": [{"id": 30, "name": "Color", "options": ["Red"], "visible": True, "variation": False, "position": 0}],
                    "images": [{"src": "https://cdn.example/image.jpg", "name": "T-shirt", "alt": "Front", "position": 0}],
                }
            ],
        }
    def list_all(self, path):
        self.paths.append(path)
        return self.responses[path]


def test_export_normalizes_woo_responses_into_catalog_snapshot():
    snapshot = export_catalog(FakeWooClient())

    assert snapshot.model_dump(exclude_none=True) == {
        "schema_version": 1,
        "categories": [{"name": "Clothing", "slug": "clothing"}],
        "tags": [{"name": "New", "slug": "new"}],
        "attributes": [{"name": "Color", "slug": "pa_color", "type": "select", "order_by": "name", "has_archives": False, "terms": ["Red"]}],
        "products": [
            {
                "sku": "OZN-40", "name": "T-shirt", "status": "publish", "regular_price": "1000", "sale_price": "",
                "manage_stock": True, "stock_quantity": 4,
                "categories": [{"name": "Clothing", "slug": "clothing"}],
                "tags": [{"name": "New", "slug": "new"}],
                "attributes": [{"scope": "global", "name": "Color", "slug": "pa_color", "options": ["Red"], "visible": True, "variation": False, "position": 0}],
            }
        ],
    }


def test_category_only_export_does_not_request_products_or_other_taxonomies():
    client = FakeWooClient()

    snapshot = export_categories(client)

    assert snapshot.model_dump(exclude_none=True) == {
        "schema_version": 1,
        "categories": [{"name": "Clothing", "slug": "clothing"}],
        "tags": [],
        "attributes": [],
        "products": [],
    }
    assert client.paths == ["products/categories"]


def test_tag_only_export_does_not_request_products_or_other_taxonomies():
    client = FakeWooClient()

    snapshot = export_tags(client)

    assert [item.slug for item in snapshot.tags] == ["new"]
    assert client.paths == ["products/tags"]


def test_attribute_only_export_keeps_terms_without_requesting_products():
    client = FakeWooClient()

    snapshot = export_attributes(client)

    assert snapshot.attributes[0].terms == ["Red"]
    assert client.paths == ["products/attributes", "products/attributes/30/terms"]


def test_export_preserves_local_product_attributes():
    client = FakeWooClient()
    client.responses["products"][0]["attributes"].append(
        {
            "id": 0,
            "name": "Engraving",
            "options": ["Yes"],
            "visible": True,
            "variation": False,
            "position": 1,
        }
    )

    snapshot = export_catalog(client)

    assert snapshot.products[0].attributes[-1].model_dump(exclude_none=True) == {
        "scope": "local",
        "name": "Engraving",
        "options": ["Yes"],
        "visible": True,
        "variation": False,
        "position": 1,
    }
