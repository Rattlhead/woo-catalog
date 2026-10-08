"""Product-specific diff and dependency construction for catalog plans."""

from __future__ import annotations

from typing import Any

from ..planning import PlannedOperation, Precondition, stable_hash
from ..schemas import Product, ProductAttribute


class ProductPlanningMixin:
    def _plan_product(self, product: Product, current: dict[str, Any] | None, categories: dict[str, dict[str, Any]], tags: dict[str, dict[str, Any]], attributes: dict[str, dict[str, Any]], dependencies: dict[tuple[str, str], PlannedOperation]) -> PlannedOperation:
        creating = current is None
        payload: dict[str, Any] = {}
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        depends_on: list[str] = []
        ignored = {"sku", "categories", "categories_mode", "tags", "tags_mode", "attributes", "attributes_mode"}
        for field in product.model_fields_set - ignored:
            payload[field] = getattr(product, field)
            if current is not None:
                before[field] = current.get(field)
            after[field] = getattr(product, field)
        if creating:
            payload.update({"sku": product.sku, "name": product.name, "type": "simple", "status": product.status})
            after.update({"sku": product.sku, "name": product.name, "type": "simple", "status": product.status})
        if "categories" in product.model_fields_set:
            semantic, request, refs = self._product_taxonomy_value(product.categories or [], (current or {}).get("categories", []), product.categories_mode or "replace", categories, dependencies, "category")
            payload["categories"], before["categories"], after["categories"] = request, self._semantic_refs((current or {}).get("categories", [])), semantic
            depends_on.extend(refs)
        if "tags" in product.model_fields_set:
            semantic, request, refs = self._product_taxonomy_value(product.tags or [], (current or {}).get("tags", []), product.tags_mode or "replace", tags, dependencies, "tag")
            payload["tags"], before["tags"], after["tags"] = request, self._semantic_refs((current or {}).get("tags", [])), semantic
            depends_on.extend(refs)
        if "attributes" in product.model_fields_set:
            semantic, request, refs = self._product_attribute_value(product.attributes or [], (current or {}).get("attributes", []), product.attributes_mode or "replace", attributes, dependencies)
            payload["attributes"], before["attributes"], after["attributes"] = request, self._semantic_current_attributes((current or {}).get("attributes", []), attributes), semantic
            depends_on.extend(refs)
        changes = self._diff(before, after)
        action = "create" if creating else ("update" if changes else "no_op")
        risks = {"publish"} if product.status == "publish" and (creating or current.get("status") != "publish") else set()
        relevant_before = before if current is not None else {"exists": False}
        return PlannedOperation(
            operation_id=self._operation_id("product", product.sku, action), resource="product", identity=product.sku,
            action=action, path="products" if creating else f"products/{current['id']}", payload=payload if action != "no_op" else None,
            before=before or None, after=after or None, changes=changes,
            precondition=Precondition(exists=current is not None, identity=product.sku, resource_id=current.get("id") if current else None, relevant_sha256=stable_hash(relevant_before)),
            depends_on=list(dict.fromkeys(depends_on)), risks=risks,
        )

    def _product_taxonomy_value(self, desired: list[Any], current: list[dict[str, Any]], mode: str, known: dict[str, dict[str, Any]], operations: dict[tuple[str, str], PlannedOperation], resource: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        semantic_by_slug = {item.get("slug"): {"name": item.get("name"), "slug": item.get("slug")} for item in current}
        request_by_slug = {item.get("slug"): {"id": item["id"]} for item in current if item.get("slug") and item.get("id")}
        if mode == "replace":
            semantic_by_slug, request_by_slug = {}, {}
        dependencies: list[str] = []
        for item in desired:
            operation = operations[(resource, item.slug)]
            if item.slug in known:
                request_by_slug[item.slug] = {"id": known[item.slug]["id"]}
                semantic_name = known[item.slug].get("name", item.name)
                if operation.action == "update" and operation.after is not None:
                    semantic_name = operation.after.get("name", semantic_name)
            else:
                request_by_slug[item.slug] = {"id": {"$ref": operation.operation_id}}
                semantic_name = (operation.after or {}).get("name", item.name)
            semantic_by_slug[item.slug] = {"name": semantic_name, "slug": item.slug}
            if operation.action in {"create", "update", "error"}:
                dependencies.append(operation.operation_id)
        return list(semantic_by_slug.values()), list(request_by_slug.values()), dependencies

    def _product_attribute_value(self, desired: list[ProductAttribute], current: list[dict[str, Any]], mode: str, known: dict[str, dict[str, Any]], operations: dict[tuple[str, str], PlannedOperation]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        semantic = self._semantic_current_attributes(current, known) if mode == "merge" else []
        request = [dict(item) for item in current] if mode == "merge" else []
        semantic_by_key = {self._attribute_key(item): item for item in semantic}
        request_by_key = {self._request_attribute_key(item, known): item for item in request}
        dependency_ids: list[str] = []
        for item in desired:
            key = (item.scope, item.slug if item.scope == "global" else item.name)
            semantic_item = item.model_dump(exclude_none=True, exclude={"scope"})
            semantic_item["scope"] = item.scope
            request_item: dict[str, Any] = {"name": item.name, "options": item.options, "visible": item.visible, "variation": item.variation, "position": item.position}
            if item.scope == "local":
                request_item["id"] = 0
            else:
                assert item.slug is not None
                operation = operations[("attribute", item.slug)]
                if item.slug in known:
                    request_item["id"] = known[item.slug]["id"]
                    canonical_name = known[item.slug].get("name", item.name)
                    if operation.action == "update" and operation.after is not None:
                        canonical_name = operation.after.get("name", canonical_name)
                else:
                    request_item["id"] = {"$ref": operation.operation_id}
                    canonical_name = (operation.after or {}).get("name", item.name)
                request_item["name"] = canonical_name
                semantic_item["name"] = canonical_name
                if operation.action in {"create", "update", "error"}:
                    dependency_ids.append(operation.operation_id)
                prefix = f"{item.slug}:"
                dependency_ids.extend(operation.operation_id for (resource, identity), operation in operations.items() if resource == "attribute_term" and identity.startswith(prefix) and operation.action == "create")
            semantic_by_key[key] = semantic_item
            request_by_key[key] = request_item
        return list(semantic_by_key.values()), list(request_by_key.values()), dependency_ids

    @staticmethod
    def _semantic_current_attributes(current: list[dict[str, Any]], known: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {item.get("id"): item for item in known.values()}
        result: list[dict[str, Any]] = []
        for item in current:
            common = {"name": item.get("name"), "options": item.get("options", []), "visible": item.get("visible", False), "variation": item.get("variation", False), "position": item.get("position", 0)}
            result.append({"scope": "local", **common} if item.get("id", 0) == 0 else {"scope": "global", "slug": by_id.get(item.get("id"), {}).get("slug"), **common})
        return result

    @staticmethod
    def _semantic_refs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"name": item.get("name"), "slug": item.get("slug")} for item in items]

    @staticmethod
    def _attribute_key(item: dict[str, Any]) -> tuple[Any, Any]:
        scope = item.get("scope", "global")
        return scope, item.get("slug") if scope == "global" else item.get("name")

    @staticmethod
    def _request_attribute_key(item: dict[str, Any], known: dict[str, dict[str, Any]]) -> tuple[Any, Any]:
        if item.get("id", 0) == 0:
            return "local", item.get("name")
        by_id = {row.get("id"): slug for slug, row in known.items()}
        return "global", by_id.get(item.get("id"))
