from woo_catalog.planners import CatalogPlanner
from woo_catalog.schemas import CatalogChange


class PlannerClient:
    def __init__(self, *, product=None, categories=None, tags=None, attributes=None):
        self.product, self.categories, self.tags, self.attributes = product, categories or [], tags or [], attributes or []
        self.list_calls = []

    def list_all(self, path, params=None):
        self.list_calls.append((path, params))
        if path == "products": return [self.product] if self.product and params == {"sku": self.product["sku"]} else []
        if path == "products/categories": return self.categories
        if path == "products/tags": return self.tags
        if path == "products/attributes": return self.attributes
        if path.endswith("/terms"): return []
        raise AssertionError(f"Unexpected list path: {path}")


def change(**values): return CatalogChange.model_validate({"schema_version": 1, **values})
def operation(plan, resource, identity): return next(item for item in plan.operations if item.resource == resource and item.identity == identity)


def test_variable_product_does_not_create_implicit_category():
    plan = CatalogPlanner(PlannerClient(product={"id": 1, "sku": "A", "type": "variable", "categories": []})).plan(change(products=[{"sku": "A", "categories": [{"name": "New", "slug": "new"}]}]))
    assert not any(item.resource == "category" and item.action == "create" for item in plan.operations)
    assert operation(plan, "product", "A").action == "error"


def test_product_reference_does_not_rename_existing_category():
    client = PlannerClient(product={"id": 1, "sku": "A", "type": "simple", "categories": []}, categories=[{"id": 7, "slug": "shirts", "name": "Store name"}])
    plan = CatalogPlanner(client).plan(change(products=[{"sku": "A", "categories": [{"slug": "shirts", "name": "LLM name"}]}]))
    assert not any(item.resource == "category" and item.action == "update" for item in plan.operations)
    assert operation(plan, "product", "A").after["categories"] == [{"name": "Store name", "slug": "shirts"}]


def test_unknown_parent_is_a_preflight_error():
    plan = CatalogPlanner(PlannerClient()).plan(change(categories=[{"slug": "child", "name": "Child", "parent_slug": "missing"}]))
    assert operation(plan, "category", "child").message == "unknown parent_slug: missing"


def test_new_product_requires_explicit_status_before_dependencies_are_planned():
    plan = CatalogPlanner(PlannerClient()).plan(change(products=[{"sku": "A", "name": "New", "categories": [{"name": "Unused", "slug": "unused"}]}]), create_products=True)
    assert operation(plan, "product", "A").message == "status is required to create a product"
    assert not any(item.resource == "category" for item in plan.operations)


def test_name_only_update_does_not_list_taxonomies():
    client = PlannerClient(product={"id": 1, "sku": "A", "type": "simple", "name": "Old"})
    plan = CatalogPlanner(client).plan(change(products=[{"sku": "A", "name": "New"}]))
    assert operation(plan, "product", "A").changes == {"name": {"before": "Old", "after": "New"}}
    assert client.list_calls == [("products", {"sku": "A"})]


def test_merge_tags_preserves_current_assignments():
    client = PlannerClient(product={"id": 1, "sku": "A", "type": "simple", "tags": [{"id": 4, "name": "Old", "slug": "old"}]}, tags=[{"id": 4, "name": "Old", "slug": "old"}])
    plan = CatalogPlanner(client).plan(change(products=[{"sku": "A", "tags_mode": "merge", "tags": [{"name": "New", "slug": "new"}]}]))
    assert operation(plan, "product", "A").after["tags"] == [{"name": "Old", "slug": "old"}, {"name": "New", "slug": "new"}]
