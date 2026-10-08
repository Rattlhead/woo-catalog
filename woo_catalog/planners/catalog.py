"""Orchestration for read-only construction of catalog execution plans."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from ..client import WooClient
from ..planning import ExecutionPlan, PlanLimitError, PlannedOperation
from ..schemas import Attribute, CatalogChange, Category, Product, Tag
from .common import PlanningOperationsMixin
from .products import ProductPlanningMixin
from .taxonomies import TaxonomyPlanningMixin


MAX_PLAN_OPERATIONS = 100


class CatalogPlanner(ProductPlanningMixin, TaxonomyPlanningMixin, PlanningOperationsMixin):
    """Build an immutable, dependency-aware plan without mutating WooCommerce."""

    def __init__(self, client: WooClient, *, store_identity: str = "unknown") -> None:
        self.client = client
        self.store_identity = store_identity

    def plan(self, change: CatalogChange, *, create_products: bool = False, allow_large_plan: bool = False) -> ExecutionPlan:
        explicit_counts = {
            "products": len(change.products or []),
            "categories": len(change.categories or []),
            "tags": len(change.tags or []),
            "attributes": len(change.attributes or []),
        }
        oversized = {name: count for name, count in explicit_counts.items() if count > MAX_PLAN_OPERATIONS}
        if oversized and not allow_large_plan:
            name, count = next(iter(oversized.items()))
            raise PlanLimitError(f"document contains {count} explicit {name}; limit is {MAX_PLAN_OPERATIONS}")

        products = change.products or []
        product_states: dict[str, tuple[str, dict[str, Any] | None]] = {}
        early_product_operations: dict[str, PlannedOperation] = {}
        for product in products:
            try:
                rows = self.client.list_all("products", params={"sku": product.sku})
                current = rows[0] if rows else None
            except Exception as error:
                product_states[product.sku] = ("error", None)
                early_product_operations[product.sku] = self._error("product", product.sku, str(error))
                continue
            if current is None and not create_products:
                product_states[product.sku] = ("skipped", None)
                early_product_operations[product.sku] = PlannedOperation(
                    operation_id=self._operation_id("product", product.sku, "skipped_new"),
                    resource="product", identity=product.sku, action="skipped_new",
                    message="create_products is required to add this SKU",
                )
            elif current is None and product.name is None:
                product_states[product.sku] = ("error", None)
                early_product_operations[product.sku] = self._error("product", product.sku, "name is required to create a product")
            elif current is None and product.status is None:
                product_states[product.sku] = ("error", None)
                early_product_operations[product.sku] = self._error("product", product.sku, "status is required to create a product")
            elif current is not None and current.get("type") != "simple":
                product_states[product.sku] = ("error", current)
                early_product_operations[product.sku] = self._error("product", product.sku, "existing product is not simple")
            elif "stock_quantity" in product.model_fields_set and self._effective_manage_stock(product, current) is False:
                product_states[product.sku] = ("error", current)
                early_product_operations[product.sku] = self._error("product", product.sku, "stock_quantity requires manage_stock to be true")
            else:
                product_states[product.sku] = ("valid", current)

        valid_products = [product for product in products if product_states[product.sku][0] == "valid"]
        explicit_categories = OrderedDict((item.slug, item) for item in change.categories or [])
        explicit_tags = OrderedDict((item.slug, item) for item in change.tags or [])
        explicit_attributes = OrderedDict((item.slug, item) for item in change.attributes or [])
        categories = OrderedDict(explicit_categories)
        tags = OrderedDict(explicit_tags)
        attributes = OrderedDict(explicit_attributes)
        for product in valid_products:
            for item in product.categories or []:
                categories.setdefault(item.slug, Category(name=item.name, slug=item.slug))
            for item in product.tags or []:
                tags.setdefault(item.slug, Tag(name=item.name, slug=item.slug))
            for item in product.attributes or []:
                if item.scope == "local":
                    continue
                assert item.slug is not None
                existing = attributes.get(item.slug)
                terms = list(existing.terms) if existing else []
                terms.extend(term for term in item.options if term not in terms)
                attributes[item.slug] = Attribute(name=item.name, slug=item.slug, terms=terms) if existing is None else existing.model_copy(update={"terms": terms})

        current_categories = self._by_slug(self.client.list_all("products/categories")) if categories else {}
        current_tags = self._by_slug(self.client.list_all("products/tags")) if tags else {}
        needs_product_attributes = any(
            "attributes" in product.model_fields_set
            and any(item.get("id", 0) != 0 for item in (product_states[product.sku][1] or {}).get("attributes", []))
            for product in valid_products
        )
        current_attributes = self._by_slug(self.client.list_all("products/attributes")) if attributes or needs_product_attributes else {}
        invalid_parents = self._invalid_category_parents(categories, current_categories)
        category_operations = self._plan_categories(categories, explicit_categories, current_categories, invalid_parents)
        tag_operations = self._plan_taxonomies(tags, explicit_tags, current_tags, resource="tag", path="products/tags")
        attribute_operations, term_operations = self._plan_attributes(attributes, explicit_attributes, current_attributes)
        dependency_operations = category_operations + tag_operations + attribute_operations + term_operations
        dependency_by_identity = {(operation.resource, operation.identity): operation for operation in dependency_operations}
        product_operations = [
            early_product_operations[product.sku] if product.sku in early_product_operations else self._plan_product(
                product, product_states[product.sku][1], current_categories, current_tags, current_attributes, dependency_by_identity
            )
            for product in products
        ]
        operations = dependency_operations + product_operations
        executable_count = sum(item.action in {"create", "update", "delete"} for item in operations)
        required = {"confirmed"}
        warnings: list[str] = []
        if any("publish" in item.risks for item in operations):
            required.add("confirm_publish")
        if executable_count > MAX_PLAN_OPERATIONS or oversized:
            if executable_count > MAX_PLAN_OPERATIONS and not allow_large_plan:
                raise PlanLimitError(f"plan contains {executable_count} executable operations; limit is {MAX_PLAN_OPERATIONS}")
            required.add("confirm_large_plan")
            warnings.append(
                f"large plan contains {executable_count} executable operations"
                + (f" and oversized input groups: {', '.join(sorted(oversized))}" if oversized else "")
            )
        return ExecutionPlan.create(
            kind="catalog", document=change, store_identity=self.store_identity, operations=operations,
            required_confirmations=required, warnings=warnings,
        )

    @staticmethod
    def _effective_manage_stock(product: Product, current: dict[str, Any] | None) -> bool:
        return product.manage_stock is True if "manage_stock" in product.model_fields_set else bool((current or {}).get("manage_stock"))

    @staticmethod
    def _by_slug(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {row["slug"]: row for row in rows if row.get("slug")}
