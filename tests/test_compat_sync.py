from woo_catalog.schemas import CatalogSnapshot
from woo_catalog.sync import CatalogSynchronizer


class FakeWooClient:
    def __init__(self, products_by_sku=None, fail_product_sku=None, fail_lookup_sku=None):
        self.products_by_sku = products_by_sku or {}
        self.fail_product_sku, self.fail_lookup_sku = fail_product_sku, fail_lookup_sku
        self.writes, self.next_id = [], 100
        self.categories, self.tags, self.attributes, self.terms = [], [], [], {}

    def list_all(self, path, params=None):
        if path == "products/categories": return list(self.categories)
        if path == "products/tags": return list(self.tags)
        if path == "products/attributes": return list(self.attributes)
        if path == "products":
            if (params or {}).get("sku") == self.fail_lookup_sku:
                from woo_catalog.client import WooClientError
                raise WooClientError("WooCommerce request failed (503)")
            return self.products_by_sku.get((params or {}).get("sku"), [])
        if path.endswith("/terms"): return list(self.terms.get(path, []))
        raise AssertionError(f"Unexpected list path: {path}")

    def post(self, path, payload):
        self.writes.append(("POST", path, payload))
        if path == "products" and payload["sku"] == self.fail_product_sku:
            from woo_catalog.client import WooClientError
            raise WooClientError("WooCommerce request failed (500)")
        result = {"id": self.next_id, **payload}; self.next_id += 1
        if path == "products":
            result.setdefault("type", "simple"); self.products_by_sku[result["sku"]] = [result]
        elif path == "products/categories": self.categories.append(result)
        elif path == "products/tags": self.tags.append(result)
        elif path == "products/attributes": self.attributes.append(result)
        elif path.endswith("/terms"): self.terms.setdefault(path, []).append(result)
        return result

    def put(self, path, payload):
        self.writes.append(("PUT", path, payload))
        for rows in self.products_by_sku.values():
            if rows and path == f"products/{rows[0]['id']}":
                rows[0].update(payload); return dict(rows[0])
        return {"id": 1, **payload}


def snapshot(*products):
    return CatalogSnapshot.model_validate({"schema_version": 1, "products": list(products)})


def test_dry_run_plans_implicit_taxonomy_creation_without_writes():
    client = FakeWooClient()
    report = CatalogSynchronizer(client).run(snapshot({"sku": "OZN-1", "name": "Shirt", "status": "draft", "categories": [{"name": "Clothing", "slug": "clothing"}], "tags": [{"name": "New", "slug": "new"}], "attributes": [{"name": "Color", "slug": "pa_color", "options": ["Red"]}]}), apply=False, create_products=True)
    assert client.writes == []
    assert {(operation.resource, operation.action) for operation in report.operations} >= {("category", "create"), ("tag", "create"), ("attribute", "create"), ("attribute_term", "create"), ("product", "create")}


def test_apply_continues_after_one_product_fails():
    client = FakeWooClient(fail_product_sku="OZN-1")
    report = CatalogSynchronizer(client).run(snapshot({"sku": "OZN-1", "name": "First", "status": "draft"}, {"sku": "OZN-2", "name": "Second", "status": "draft"}), apply=True, create_products=True)
    assert [write[2]["sku"] for write in client.writes if write[1] == "products"] == ["OZN-1", "OZN-2"]
    assert any(operation.identity == "OZN-1" and operation.status == "error" for operation in report.operations)
    assert any(operation.identity == "OZN-2" and operation.status == "applied" for operation in report.operations)


def test_apply_uses_empty_categories_array_to_clear_existing_categories():
    client = FakeWooClient(products_by_sku={"OZN-1": [{"id": 10, "type": "simple", "sku": "OZN-1", "categories": [{"id": 3, "name": "Old", "slug": "old"}]}]})
    CatalogSynchronizer(client).run(snapshot({"sku": "OZN-1", "categories": []}), apply=True)
    assert ("PUT", "products/10", {"categories": []}) in client.writes


def test_apply_skips_new_product_without_create_products_flag():
    client = FakeWooClient()
    report = CatalogSynchronizer(client).run(snapshot({"sku": "OZN-NEW", "name": "New item"}), apply=True, create_products=False)
    assert client.writes == []
    assert report.summary["products_skipped_new"] == 1


def test_skipped_new_product_does_not_create_its_implicit_taxonomies():
    client = FakeWooClient()
    CatalogSynchronizer(client).run(snapshot({"sku": "OZN-NEW", "name": "New item", "categories": [{"name": "Only for new product", "slug": "new-only"}]}), apply=True, create_products=False)
    assert client.writes == []


def test_lookup_error_for_one_sku_does_not_prevent_other_product_updates():
    client = FakeWooClient(products_by_sku={"OZN-2": [{"id": 2, "type": "simple", "sku": "OZN-2", "name": "Old"}]}, fail_lookup_sku="OZN-1")
    report = CatalogSynchronizer(client).run(snapshot({"sku": "OZN-1", "name": "Unavailable"}, {"sku": "OZN-2", "name": "Updated"}), apply=True)
    assert ("PUT", "products/2", {"name": "Updated"}) in client.writes
    assert any(operation.identity == "OZN-1" and operation.action == "error" for operation in report.operations)
