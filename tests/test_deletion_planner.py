from woo_catalog.deletion import DeletionPlanner
from woo_catalog.schemas import DeleteRequest


class RelationshipClient:
    def list_all(self, path, params=None):
        if path == "products/categories": return [{"id": 10, "slug": "clothing", "name": "Clothing"}]
        if path == "products/tags": return []
        if path == "products/attributes": return [{"id": 30, "slug": "pa_color", "name": "Color"}]
        if path == "products/attributes/30/terms": return [{"id": 31, "name": "Red", "slug": "red"}]
        if path == "products" and params == {"category": 10}: return [{"id": 1, "sku": "SKU-1"}, {"id": 2, "sku": "SKU-2"}]
        if path == "products": return []
        raise AssertionError(f"Unexpected list path: {path} {params}")


def test_delete_plan_binds_id_and_reports_relations():
    plan = DeletionPlanner(RelationshipClient(), store_identity="https://shop.example").plan(DeleteRequest.model_validate({"schema_version": 1, "categories": ["clothing"]}))
    operation = plan.operations[0]
    assert operation.path == "products/categories/10"
    assert operation.precondition.resource_id == 10
    assert operation.details["affected_products"] == {"count": 2, "items": ["SKU-1", "SKU-2"], "truncated": False, "complete": True}
    assert plan.required_confirmations == {"confirmed", "confirm_permanent"}


def test_empty_terms_marks_entire_attribute_deletion():
    plan = DeletionPlanner(RelationshipClient()).plan(DeleteRequest.model_validate({"schema_version": 1, "attributes": [{"slug": "pa_color", "terms": []}]}))
    assert {"permanent", "delete_entire_attribute"} <= next(item for item in plan.operations if item.resource == "attribute").risks
