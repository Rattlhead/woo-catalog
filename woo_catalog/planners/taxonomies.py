"""Category, tag, attribute, and attribute-term planning."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from ..planning import PlannedOperation, Precondition, stable_hash
from ..schemas import Attribute, Category, Tag


class TaxonomyPlanningMixin:
    def _invalid_category_parents(self, desired: OrderedDict[str, Category], current: dict[str, dict[str, Any]]) -> dict[str, str]:
        invalid: dict[str, str] = {}
        for slug, item in desired.items():
            if item.parent_slug and item.parent_slug not in desired and item.parent_slug not in current:
                invalid[slug] = f"unknown parent_slug: {item.parent_slug}"
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(slug: str) -> None:
            if slug in visited or slug in invalid:
                return
            if slug in visiting:
                invalid[slug] = "category parent cycle"
                return
            visiting.add(slug)
            parent = desired[slug].parent_slug
            if parent in desired:
                visit(parent)
                if parent in invalid:
                    invalid.setdefault(slug, "category parent cycle")
            visiting.remove(slug)
            visited.add(slug)

        for slug in desired:
            visit(slug)
        return invalid

    def _plan_categories(self, desired: OrderedDict[str, Category], explicit: OrderedDict[str, Category], current: dict[str, dict[str, Any]], invalid: dict[str, str]) -> list[PlannedOperation]:
        operations: list[PlannedOperation] = []
        for slug, item in desired.items():
            if slug in invalid:
                operations.append(self._error("category", slug, invalid[slug]))
                continue
            existing = current.get(slug)
            if existing is not None and slug not in explicit:
                operations.append(self._no_op("category", slug, existing))
                continue
            payload = item.model_dump(exclude_unset=True, exclude_none=True, exclude={"parent_slug", "image"})
            if item.image is not None:
                payload["image"] = item.image
            dependencies: list[str] = []
            if "parent_slug" in item.model_fields_set:
                if item.parent_slug is None:
                    payload["parent"] = 0
                elif item.parent_slug in current:
                    payload["parent"] = current[item.parent_slug]["id"]
                else:
                    parent_id = self._operation_id("category", item.parent_slug, "create")
                    payload["parent"] = {"$ref": parent_id}
                    dependencies.append(parent_id)
            before = self._select_before(existing, payload)
            after = {**before, **payload} if existing else dict(payload)
            operations.append(self._make_change_operation("category", slug, "products/categories", existing, payload, before, after, dependencies))
        return operations

    def _plan_taxonomies(self, desired: OrderedDict[str, Category | Tag], explicit: OrderedDict[str, Category | Tag], current: dict[str, dict[str, Any]], *, resource: str, path: str) -> list[PlannedOperation]:
        operations: list[PlannedOperation] = []
        for slug, item in desired.items():
            existing = current.get(slug)
            if existing is not None and slug not in explicit:
                operations.append(self._no_op(resource, slug, existing))
                continue
            payload = item.model_dump(exclude_unset=True, exclude_none=True)
            before = self._select_before(existing, payload)
            after = {**before, **payload} if existing else payload
            operations.append(self._make_change_operation(resource, slug, path, existing, payload, before, after))
        return operations

    def _plan_attributes(self, desired: OrderedDict[str, Attribute], explicit: OrderedDict[str, Attribute], current: dict[str, dict[str, Any]]) -> tuple[list[PlannedOperation], list[PlannedOperation]]:
        attribute_operations: list[PlannedOperation] = []
        term_operations: list[PlannedOperation] = []
        for slug, item in desired.items():
            existing = current.get(slug)
            payload = item.model_dump(exclude_unset=True, exclude_none=True, exclude={"terms"})
            if existing is not None and slug not in explicit:
                attribute_operation = self._no_op("attribute", slug, existing)
            else:
                before = self._select_before(existing, payload)
                after = {**before, **payload} if existing else payload
                attribute_operation = self._make_change_operation("attribute", slug, "products/attributes", existing, payload, before, after)
            attribute_operations.append(attribute_operation)
            known_terms = {term["name"] for term in self.client.list_all(f"products/attributes/{existing['id']}/terms")} if existing else set()
            for term in item.terms:
                identity = f"{slug}:{term}"
                if term in known_terms:
                    term_operations.append(PlannedOperation(operation_id=self._operation_id("attribute_term", identity, "no_op"), resource="attribute_term", identity=identity, action="no_op"))
                    continue
                dependency = attribute_operation.operation_id if attribute_operation.action == "create" else None
                attribute_id: int | dict[str, str] = existing["id"] if existing else {"$ref": attribute_operation.operation_id}
                term_operations.append(PlannedOperation(
                    operation_id=self._operation_id("attribute_term", identity, "create"), resource="attribute_term", identity=identity, action="create",
                    path=f"products/attributes/{self._path_ref(attribute_id)}/terms", payload={"name": term}, before=None, after={"name": term},
                    changes={"name": {"before": None, "after": term}},
                    precondition=Precondition(exists=False, identity=identity, relevant_sha256=stable_hash({"exists": False})),
                    depends_on=[dependency] if dependency else [],
                ))
        return attribute_operations, term_operations
