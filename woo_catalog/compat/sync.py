"""Compatibility facade for the original in-process catalog API."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from ..client import WooClient
from ..execution import PlanExecutor
from ..plan_store import PlanStore
from ..planners import CatalogPlanner
from ..planning import Confirmations, ExecutionPlan, ExecutionResult
from ..schemas import CatalogChange, CatalogSnapshot, Operation, RunReport


class CatalogSynchronizer:
    """Preserve the original Python API while using the durable pipeline."""

    def __init__(self, client: WooClient):
        self.client = client

    def run(
        self,
        snapshot: CatalogSnapshot,
        *,
        apply: bool,
        match_by: str = "sku",
        create_products: bool = False,
    ) -> RunReport:
        if match_by != "sku":
            raise ValueError("Only match_by='sku' is supported")
        change = CatalogChange.model_validate(
            snapshot.model_dump(mode="json", exclude_unset=True, exclude_none=False)
        )
        plan = CatalogPlanner(self.client, store_identity="compatibility-facade").plan(
            change,
            create_products=create_products,
        )
        if not apply:
            return self._report(plan)

        with TemporaryDirectory(prefix="woo-catalog-plan-") as directory:
            store = PlanStore(Path(directory) / "state.sqlite3")
            store.save(plan)
            result = PlanExecutor(self.client, store).apply(
                plan.plan_id,
                Confirmations(confirmed=True, confirm_publish=True),
            )
        return self._report(plan, result)

    @classmethod
    def _report(cls, plan: ExecutionPlan, result: ExecutionResult | None = None) -> RunReport:
        result_by_id = {item.operation_id: item for item in (result.operations if result else [])}
        operations: list[Operation] = []
        for planned in plan.operations:
            execution = result_by_id.get(planned.operation_id)
            if execution is None:
                status = "error" if planned.action == "error" else (
                    "planned" if planned.action in {"create", "update", "delete"} else "skipped"
                )
                action, message, execution_details = planned.action, planned.message, {}
            elif execution.state == "verified":
                status = "applied" if planned.action in {"create", "update", "delete"} else "skipped"
                action, message, execution_details = planned.action, planned.message, execution.details
            else:
                status, action = "error", "error"
                message = execution.details.get("message") or execution.state
                execution_details = execution.details
            operations.append(Operation(
                resource=planned.resource,
                identity=planned.identity,
                action=action,
                status=status,
                message=message,
                details={
                    "before": planned.before,
                    "after": planned.after,
                    "changes": planned.changes,
                    "risks": sorted(planned.risks),
                    **planned.details,
                    **execution_details,
                },
            ))
        return RunReport(
            mode="apply" if result else "dry_run",
            operations=operations,
            summary=cls._summary(operations),
        )

    @staticmethod
    def _summary(operations: list[Operation]) -> dict[str, int]:
        summary = {
            action: sum(operation.action == action for operation in operations)
            for action in ("create", "update", "delete", "no_op", "skipped_new", "error")
        }
        summary.update({
            "products_created": sum(operation.resource == "product" and operation.action == "create" for operation in operations),
            "products_updated": sum(operation.resource == "product" and operation.action == "update" for operation in operations),
            "products_skipped_new": sum(operation.resource == "product" and operation.action == "skipped_new" for operation in operations),
        })
        return summary
