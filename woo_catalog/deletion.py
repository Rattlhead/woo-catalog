"""Explicit, dry-run-first deletion of WooCommerce catalog resources."""

from __future__ import annotations

from typing import Any

from .client import WooClient
from .planning import ExecutionPlan, PlanLimitError, PlannedOperation, Precondition, stable_hash
from .schemas import DeleteRequest


MAX_PLAN_OPERATIONS = 100
MAX_AFFECTED_IDENTITIES = 20


class DeletionPlanner:
    def __init__(self, client: WooClient, *, store_identity: str = "unknown") -> None:
        self.client = client
        self.store_identity = store_identity

    def plan(self, request: DeleteRequest, *, allow_large_plan: bool = False) -> ExecutionPlan:
        explicit_counts = [
            len(request.products),
            len(request.categories),
            len(request.tags),
            len(request.attributes),
            *(len(attribute.terms) for attribute in request.attributes),
        ]
        oversized = any(count > MAX_PLAN_OPERATIONS for count in explicit_counts)
        if oversized and not allow_large_plan:
            raise PlanLimitError(
                f"document contains {max(explicit_counts)} explicit deletion entities; "
                f"limit is {MAX_PLAN_OPERATIONS}"
            )
        operations: list[PlannedOperation] = []
        for sku in request.products:
            rows = self.client.list_all("products", params={"sku": sku})
            row = rows[0] if rows else None
            if row is None:
                operations.append(self._no_op("product", sku, "not found"))
            else:
                operations.append(self._delete_operation("product", sku, f"products/{row['id']}", row, permanent=False))
        if request.categories:
            operations.extend(self._plan_taxonomy("category", "products/categories", request.categories))
        if request.tags:
            operations.extend(self._plan_taxonomy("tag", "products/tags", request.tags))
        if request.attributes:
            operations.extend(self._plan_attributes(request))
        executable = sum(operation.action == "delete" for operation in operations)
        required = {"confirmed"}
        warnings: list[str] = []
        if any("permanent" in operation.risks for operation in operations):
            required.add("confirm_permanent")
        if executable > MAX_PLAN_OPERATIONS or oversized:
            if executable > MAX_PLAN_OPERATIONS and not allow_large_plan:
                raise PlanLimitError(
                    f"plan contains {executable} executable operations; limit is {MAX_PLAN_OPERATIONS}"
                )
            required.add("confirm_large_plan")
            warnings.append(f"large plan contains {executable} executable operations")
        return ExecutionPlan.create(
            kind="delete",
            document=request,
            store_identity=self.store_identity,
            operations=operations,
            required_confirmations=required,
            warnings=warnings,
        )

    def _plan_taxonomy(self, resource: str, path: str, slugs: list[str]) -> list[PlannedOperation]:
        rows = {row.get("slug"): row for row in self.client.list_all(path)}
        operations: list[PlannedOperation] = []
        query_key = "category" if resource == "category" else "tag"
        for slug in slugs:
            row = rows.get(slug)
            if row is None:
                operations.append(self._no_op(resource, slug, "not found"))
                continue
            operation = self._delete_operation(resource, slug, f"{path}/{row['id']}", row, permanent=True)
            operation.details["affected_products"] = self._affected_products(params={query_key: row["id"]})
            operations.append(operation)
        return operations

    def _plan_attributes(self, request: DeleteRequest) -> list[PlannedOperation]:
        rows = {row.get("slug"): row for row in self.client.list_all("products/attributes")}
        operations: list[PlannedOperation] = []
        for requested in request.attributes:
            row = rows.get(requested.slug)
            if row is None:
                operations.append(self._no_op("attribute", requested.slug, "not found"))
                continue
            terms = {
                term.get("name"): term
                for term in self.client.list_all(f"products/attributes/{row['id']}/terms")
            }
            for name in requested.terms:
                term = terms.get(name)
                identity = f"{requested.slug}:{name}"
                if term is None:
                    operations.append(self._no_op("attribute_term", identity, "not found"))
                    continue
                operation = self._delete_operation(
                    "attribute_term",
                    identity,
                    f"products/attributes/{row['id']}/terms/{term['id']}",
                    term,
                    permanent=True,
                )
                operation.details["affected_products"] = self._affected_attribute_products(row["id"], name)
                operations.append(operation)
            if not requested.terms:
                operation = self._delete_operation(
                    "attribute",
                    requested.slug,
                    f"products/attributes/{row['id']}",
                    row,
                    permanent=True,
                )
                operation.risks.add("delete_entire_attribute")
                operation.details["affected_products"] = self._affected_attribute_products(row["id"], None)
                operations.append(operation)
        return operations

    def _delete_operation(
        self,
        resource: str,
        identity: str,
        path: str,
        row: dict[str, Any],
        *,
        permanent: bool,
    ) -> PlannedOperation:
        identity_field = "sku" if resource == "product" else ("name" if resource == "attribute_term" else "slug")
        before = {"id": row["id"], identity_field: row.get(identity_field, identity)}
        return PlannedOperation(
            operation_id=f"{resource}:{identity}:delete",
            resource=resource,
            identity=identity,
            action="delete",
            path=path,
            before=before,
            after=None,
            changes={"exists": {"before": True, "after": False}},
            precondition=Precondition(
                exists=True,
                identity=identity,
                resource_id=row["id"],
                relevant_sha256=stable_hash(before),
            ),
            risks={"permanent"} if permanent else set(),
            details={"deletion_mode": "permanent" if permanent else "trash"},
        )

    def _affected_products(self, *, params: dict[str, int]) -> dict[str, object]:
        try:
            products = self.client.list_all("products", params=params)
        except Exception:
            return {"count": 0, "items": [], "truncated": False, "complete": False}
        return self._affected_summary(products)

    def _affected_attribute_products(self, attribute_id: int, term: str | None) -> dict[str, object]:
        try:
            products = self.client.list_all("products")
        except Exception:
            return {"count": 0, "items": [], "truncated": False, "complete": False}
        affected = []
        for product in products:
            for attribute in product.get("attributes", []):
                if attribute.get("id") != attribute_id:
                    continue
                if term is not None and term not in attribute.get("options", []):
                    continue
                affected.append(product)
                break
        return self._affected_summary(affected)

    @staticmethod
    def _affected_summary(products: list[dict[str, Any]]) -> dict[str, object]:
        identities = [product.get("sku") or str(product.get("id")) for product in products]
        return {
            "count": len(identities),
            "items": identities[:MAX_AFFECTED_IDENTITIES],
            "truncated": len(identities) > MAX_AFFECTED_IDENTITIES,
            "complete": True,
        }

    @staticmethod
    def _no_op(resource: str, identity: str, message: str) -> PlannedOperation:
        return PlannedOperation(
            operation_id=f"{resource}:{identity}:no_op",
            resource=resource,
            identity=identity,
            action="no_op",
            message=message,
        )


from .compat.deletion import CatalogDeleter

__all__ = ["CatalogDeleter", "DeletionPlanner"]
