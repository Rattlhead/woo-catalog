"""Original immediate deletion API retained for Python callers."""

from __future__ import annotations

from collections.abc import Callable

from ..client import WooClient
from ..schemas import DeleteRequest, Operation, RunReport


class CatalogDeleter:
    def __init__(self, client: WooClient):
        self.client = client

    def run(
        self,
        request: DeleteRequest,
        *,
        apply: bool,
        confirm_permanent: Callable[[list[str]], bool],
    ) -> RunReport:
        operations, targets = self._plan(request)
        permanent = [target for target in targets if target[3]]
        if not apply:
            return RunReport(mode="dry_run", operations=operations, summary=self._summary(operations))
        if permanent and not confirm_permanent([identity for _, identity, _, _ in permanent]):
            for operation in operations:
                if operation.action == "delete" and operation.status == "planned":
                    operation.status = "skipped"
                    operation.message = "deletion cancelled by user"
            return RunReport(mode="apply", operations=operations, summary=self._summary(operations))
        for operation, _, path, force in targets:
            try:
                self.client.delete(path, force=force)
                operation.status = "applied"
            except Exception as error:
                operation.action = "error"
                operation.status = "error"
                operation.message = str(error)
        return RunReport(mode="apply", operations=operations, summary=self._summary(operations))

    def _plan(self, request: DeleteRequest) -> tuple[list[Operation], list[tuple[Operation, str, str, bool]]]:
        operations: list[Operation] = []
        targets: list[tuple[Operation, str, str, bool]] = []
        self._plan_products(request.products, operations, targets)
        self._plan_taxonomy("category", "products/categories", request.categories, operations, targets)
        self._plan_taxonomy("tag", "products/tags", request.tags, operations, targets)
        self._plan_attributes(request, operations, targets)
        return operations, targets

    def _plan_products(self, skus: list[str], operations: list[Operation], targets: list[tuple[Operation, str, str, bool]]) -> None:
        for sku in skus:
            rows = self.client.list_all("products", params={"sku": sku})
            if not rows:
                operations.append(Operation(resource="product", identity=sku, action="no_op", status="skipped", message="not found"))
                continue
            operation = Operation(resource="product", identity=sku, action="delete", status="planned", message="move to trash")
            operations.append(operation)
            targets.append((operation, sku, f"products/{rows[0]['id']}", False))

    def _plan_taxonomy(self, resource: str, path: str, slugs: list[str], operations: list[Operation], targets: list[tuple[Operation, str, str, bool]]) -> None:
        rows = {row.get("slug"): row for row in self.client.list_all(path)}
        for slug in slugs:
            row = rows.get(slug)
            if row is None:
                operations.append(Operation(resource=resource, identity=slug, action="no_op", status="skipped", message="not found"))
                continue
            operation = Operation(resource=resource, identity=slug, action="delete", status="planned", message="permanent deletion requires confirmation")
            operations.append(operation)
            targets.append((operation, slug, f"{path}/{row['id']}", True))

    def _plan_attributes(self, request: DeleteRequest, operations: list[Operation], targets: list[tuple[Operation, str, str, bool]]) -> None:
        rows = {row.get("slug"): row for row in self.client.list_all("products/attributes")}
        for requested in request.attributes:
            row = rows.get(requested.slug)
            if row is None:
                operations.append(Operation(resource="attribute", identity=requested.slug, action="no_op", status="skipped", message="not found"))
                continue
            terms = {term.get("name"): term for term in self.client.list_all(f"products/attributes/{row['id']}/terms")}
            for name in requested.terms:
                term = terms.get(name)
                if term is None:
                    operations.append(Operation(resource="attribute_term", identity=f"{requested.slug}:{name}", action="no_op", status="skipped", message="not found"))
                    continue
                operation = Operation(resource="attribute_term", identity=f"{requested.slug}:{name}", action="delete", status="planned", message="permanent deletion requires confirmation")
                operations.append(operation)
                targets.append((operation, operation.identity, f"products/attributes/{row['id']}/terms/{term['id']}", True))
            if not requested.terms:
                operation = Operation(resource="attribute", identity=requested.slug, action="delete", status="planned", message="permanent deletion requires confirmation")
                operations.append(operation)
                targets.append((operation, requested.slug, f"products/attributes/{row['id']}", True))

    @staticmethod
    def _summary(operations: list[Operation]) -> dict[str, int]:
        summary = {"delete": 0, "no_op": 0, "error": 0}
        for operation in operations:
            summary[operation.action] = summary.get(operation.action, 0) + 1
        return summary
