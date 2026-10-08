from woo_catalog.deletion import CatalogDeleter
from woo_catalog.schemas import DeleteRequest


class FakeWooClient:
    def __init__(self): self.deleted = []
    def list_all(self, path, params=None):
        responses = {"products/categories": [{"id": 10, "slug": "clothing"}], "products/tags": [], "products/attributes": [{"id": 30, "slug": "pa_color"}], "products/attributes/30/terms": [{"id": 31, "name": "Red"}]}
        return [{"id": 1, "sku": "OZN-1"}] if path == "products" and params == {"sku": "OZN-1"} else responses[path]
    def delete(self, path, *, force=False): self.deleted.append((path, force)); return {"id": 1}


def test_delete_dry_run_plans_soft_product_and_permanent_taxonomy_deletion():
    request = DeleteRequest.model_validate({"schema_version": 1, "products": ["OZN-1"], "categories": ["clothing"], "attributes": [{"slug": "pa_color", "terms": ["Red"]}]})
    report = CatalogDeleter(FakeWooClient()).run(request, apply=False, confirm_permanent=lambda _: False)
    assert [(item.resource, item.identity, item.action, item.status) for item in report.operations] == [("product", "OZN-1", "delete", "planned"), ("category", "clothing", "delete", "planned"), ("attribute_term", "pa_color:Red", "delete", "planned")]


def test_delete_cancels_every_operation_when_permanent_deletion_is_not_confirmed():
    request = DeleteRequest.model_validate({"schema_version": 1, "products": ["OZN-1"], "categories": ["clothing"]})
    client = FakeWooClient(); report = CatalogDeleter(client).run(request, apply=True, confirm_permanent=lambda _: False)
    assert client.deleted == []
    assert all(item.status == "skipped" and item.message == "deletion cancelled by user" for item in report.operations)


def test_delete_uses_trash_for_products_and_force_only_after_confirmation():
    request = DeleteRequest.model_validate({"schema_version": 1, "products": ["OZN-1"], "categories": ["clothing"]})
    client = FakeWooClient(); CatalogDeleter(client).run(request, apply=True, confirm_permanent=lambda ids: ids == ["clothing"])
    assert client.deleted == [("products/1", False), ("products/categories/10", True)]
