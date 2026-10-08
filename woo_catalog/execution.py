"""Apply saved plans with precondition checks, read-back, and reconciliation."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .client import WooClient, WooClientError
from .plan_store import PlanStore
from .planning import (
    ConfirmationError,
    Confirmations,
    ExecutionPlan,
    ExecutionResult,
    OperationResult,
    PlannedOperation,
    PlanStateError,
    stable_hash,
)


class PlanExecutor:
    def __init__(self, client: WooClient, store: PlanStore) -> None:
        self.client = client
        self.store = store

    def apply(self, plan_id: str, confirmations: Confirmations) -> ExecutionResult:
        planned = self.store.get(plan_id)
        missing = planned.required_confirmations - confirmations.enabled_names()
        if missing:
            raise ConfirmationError(f"missing confirmations: {', '.join(sorted(missing))}")
        prior_status = self.store.status(plan_id)
        prior_events = {event["operation_id"]: event for event in prior_status["events"]}
        retrying = prior_status["state"] in {"failed", "partial"}
        plan = self.store.begin_apply(plan_id)
        results: list[OperationResult] = []
        state_by_operation: dict[str, str] = {}
        resolved_ids: dict[str, int] = {}
        index = 0
        batch_index = 0
        while index < len(plan.operations):
            operation = plan.operations[index]
            if (
                plan.context.get("execution_mode") == "batch_products"
                and not retrying
                and self._is_batch_candidate(operation, state_by_operation)
            ):
                candidates = self._contiguous_batch_candidates(plan.operations, index, state_by_operation)
                batch_results = self._apply_product_batch(candidates, resolved_ids, batch_index)
                batch_index += 1
                for candidate, result in zip(candidates, batch_results, strict=True):
                    state_by_operation[candidate.operation_id] = result.state
                    results.append(result)
                    self.store.update_operation(plan_id, result.operation_id, result.state, result.details)
                index += len(candidates)
                continue
            prior = prior_events.get(operation.operation_id) if retrying else None
            if prior is not None and prior["state"] != "retryable":
                if prior["state"] == "verified":
                    try:
                        current = self._read(operation)
                    except WooClientError as error:
                        current = None
                        state = "failed"
                        details = {"retry_skipped": True, "message": str(error)}
                    else:
                        state = "verified" if self._matches_after(operation, current, resolved_ids) else "conflict"
                        details = {"retry_skipped": True, "actual": self._safe_actual(current)}
                    if state == "verified" and isinstance(current, dict) and isinstance(current.get("id"), int):
                        resolved_ids[operation.operation_id] = current["id"]
                    result = OperationResult(operation_id=operation.operation_id, state=state, details=details)
                else:
                    result = OperationResult(
                        operation_id=operation.operation_id,
                        state=prior["state"],
                        details=prior["details"],
                    )
            elif any(state_by_operation.get(dependency) != "verified" for dependency in operation.depends_on):
                result = OperationResult(
                    operation_id=operation.operation_id,
                    state="blocked_dependency",
                    details={"depends_on": operation.depends_on},
                )
            else:
                result = self._apply_operation(operation, resolved_ids)
            state_by_operation[operation.operation_id] = result.state
            results.append(result)
            self.store.update_operation(plan_id, result.operation_id, result.state, result.details)
            index += 1
        final_state = self._final_state(results)
        execution = ExecutionResult(plan_id=plan_id, state=final_state, operations=results)
        self.store.finish(plan_id, final_state, execution.model_dump(mode="json"))
        return execution

    @staticmethod
    def _is_batch_candidate(operation: PlannedOperation, states: dict[str, str]) -> bool:
        return (
            operation.resource == "product"
            and operation.action in {"create", "update"}
            and all(states.get(dependency) == "verified" for dependency in operation.depends_on)
        )

    def _contiguous_batch_candidates(
        self,
        operations: list[PlannedOperation],
        start: int,
        states: dict[str, str],
    ) -> list[PlannedOperation]:
        candidates: list[PlannedOperation] = []
        for operation in operations[start : start + 100]:
            if not self._is_batch_candidate(operation, states):
                break
            candidates.append(operation)
        return candidates

    def _apply_product_batch(
        self,
        operations: list[PlannedOperation],
        resolved_ids: dict[str, int],
        batch_index: int,
    ) -> list[OperationResult]:
        results: dict[str, OperationResult] = {}
        ready: list[tuple[PlannedOperation, dict[str, Any] | None]] = []
        for operation in operations:
            try:
                current = self._read(operation)
            except WooClientError as error:
                results[operation.operation_id] = OperationResult(
                    operation_id=operation.operation_id,
                    state="failed",
                    details={"message": str(error), "phase": "precondition_read", "execution": "batch"},
                )
                continue
            if operation.action == "create" and current is not None and self._matches_after(
                operation, current, resolved_ids
            ):
                if isinstance(current.get("id"), int):
                    resolved_ids[operation.operation_id] = current["id"]
                results[operation.operation_id] = OperationResult(
                    operation_id=operation.operation_id,
                    state="verified",
                    details={"already_applied": True, "actual": self._safe_actual(current), "execution": "batch"},
                )
                continue
            if not self._precondition_matches(operation, current):
                results[operation.operation_id] = OperationResult(
                    operation_id=operation.operation_id,
                    state="conflict",
                    details={"actual": self._safe_actual(current), "execution": "batch"},
                )
                continue
            ready.append((operation, current))
        if ready:
            payload = {"create": [], "update": []}
            for operation, current in ready:
                value = self._resolve_refs(deepcopy(operation.payload or {}), resolved_ids)
                if operation.action == "create":
                    payload["create"].append(value)
                else:
                    assert current is not None and isinstance(current.get("id"), int)
                    payload["update"].append({"id": current["id"], **value})
            try:
                self.client.post_batch("products/batch", {key: value for key, value in payload.items() if value})
            except WooClientError as error:
                state = "uncertain" if error.request_may_have_been_sent else "failed"
                for operation, _ in ready:
                    results[operation.operation_id] = OperationResult(
                        operation_id=operation.operation_id,
                        state=state,
                        details={"message": str(error), "execution": "batch", "batch_index": batch_index},
                    )
            else:
                for operation, _ in ready:
                    try:
                        actual = self._read(operation)
                    except WooClientError as error:
                        results[operation.operation_id] = OperationResult(
                            operation_id=operation.operation_id,
                            state="uncertain",
                            details={"message": str(error), "phase": "read_back", "execution": "batch", "batch_index": batch_index},
                        )
                        continue
                    if isinstance(actual, dict) and isinstance(actual.get("id"), int):
                        resolved_ids[operation.operation_id] = actual["id"]
                    results[operation.operation_id] = OperationResult(
                        operation_id=operation.operation_id,
                        state="verified" if self._matches_after(operation, actual, resolved_ids) else "failed_verification",
                        details={"actual": self._safe_actual(actual), "execution": "batch", "batch_index": batch_index},
                    )
        return [results[operation.operation_id] for operation in operations]

    def reconcile(self, plan_id: str) -> ExecutionResult:
        plan = self.store.get(plan_id)
        status = self.store.status(plan_id)
        latest = {event["operation_id"]: event for event in status["events"]}
        results: list[OperationResult] = []
        resolved_ids: dict[str, int] = {}
        for operation in plan.operations:
            prior = latest.get(operation.operation_id)
            if prior is None:
                results.append(OperationResult(operation_id=operation.operation_id, state="pending"))
                continue
            if prior["state"] != "uncertain":
                if prior["state"] == "verified":
                    current = self._read(operation)
                    if isinstance(current, dict) and isinstance(current.get("id"), int):
                        resolved_ids[operation.operation_id] = current["id"]
                results.append(
                    OperationResult(
                        operation_id=operation.operation_id,
                        state=prior["state"],
                        details=prior["details"],
                    )
                )
                continue
            try:
                current = self._read(operation)
            except WooClientError as error:
                state = "uncertain"
                details = {"reconciled": False, "message": str(error)}
                result = OperationResult(operation_id=operation.operation_id, state=state, details=details)
                self.store.update_operation(plan_id, operation.operation_id, state, details)
                results.append(result)
                continue
            if self._matches_after(operation, current, resolved_ids):
                state = "verified"
                details = {"reconciled": True, "actual": self._safe_actual(current)}
                if isinstance(current, dict) and isinstance(current.get("id"), int):
                    resolved_ids[operation.operation_id] = current["id"]
            elif self._precondition_matches(operation, current):
                state = "retryable"
                details = {"reconciled": True, "message": "the original state is unchanged"}
            else:
                state = "conflict"
                details = {"reconciled": True, "actual": self._safe_actual(current)}
            result = OperationResult(operation_id=operation.operation_id, state=state, details=details)
            self.store.update_operation(plan_id, operation.operation_id, state, details)
            results.append(result)
        final_state = self._final_state(results)
        execution = ExecutionResult(plan_id=plan_id, state=final_state, operations=results)
        self.store.finish(plan_id, final_state, execution.model_dump(mode="json"))
        return execution

    def _apply_operation(
        self,
        operation: PlannedOperation,
        resolved_ids: dict[str, int],
    ) -> OperationResult:
        if operation.action == "error":
            return OperationResult(
                operation_id=operation.operation_id,
                state="failed",
                details={"phase": "preflight", "message": operation.message or "preflight error"},
            )
        try:
            current = self._read(operation)
        except WooClientError as error:
            return OperationResult(
                operation_id=operation.operation_id,
                state="failed",
                details={"message": str(error), "phase": "precondition_read"},
            )
        if operation.action == "create" and current is not None and self._matches_after(
            operation, current, resolved_ids
        ):
            if isinstance(current.get("id"), int):
                resolved_ids[operation.operation_id] = current["id"]
            return OperationResult(
                operation_id=operation.operation_id,
                state="verified",
                details={"already_applied": True, "actual": self._safe_actual(current)},
            )
        if not self._precondition_matches(operation, current):
            return OperationResult(
                operation_id=operation.operation_id,
                state="conflict",
                details={"actual": self._safe_actual(current)},
            )
        if operation.action in {"no_op", "skipped_new"}:
            return OperationResult(operation_id=operation.operation_id, state="verified")
        path = self._resolve_path(operation.path or "", resolved_ids)
        payload = self._resolve_refs(deepcopy(operation.payload or {}), resolved_ids)
        try:
            if operation.action == "create":
                response = self.client.post(path, payload)
            elif operation.action == "update":
                response = self.client.put(path, payload)
            elif operation.action == "delete":
                response = self.client.delete(path, force="permanent" in operation.risks)
            else:
                raise PlanStateError(f"unsupported executable action: {operation.action}")
        except WooClientError as error:
            state = "uncertain" if error.request_may_have_been_sent else "failed"
            return OperationResult(
                operation_id=operation.operation_id,
                state=state,
                details={"message": str(error)},
            )
        if isinstance(response, dict) and isinstance(response.get("id"), int):
            resolved_ids[operation.operation_id] = response["id"]
        try:
            actual = self._read(operation)
        except WooClientError as error:
            return OperationResult(
                operation_id=operation.operation_id,
                state="uncertain",
                details={"message": str(error), "phase": "read_back"},
            )
        if operation.action == "delete":
            verified = actual is None
        else:
            verified = self._matches_after(operation, actual, resolved_ids)
        return OperationResult(
            operation_id=operation.operation_id,
            state="verified" if verified else "failed_verification",
            details={"actual": self._safe_actual(actual)},
        )

    def _read(self, operation: PlannedOperation) -> dict[str, Any] | None:
        if operation.resource == "product":
            rows = self.client.list_all("products", params={"sku": operation.identity})
            return rows[0] if rows else None
        collection_paths = {
            "category": "products/categories",
            "tag": "products/tags",
            "attribute": "products/attributes",
        }
        if operation.resource in collection_paths:
            rows = self.client.list_all(collection_paths[operation.resource])
            return next((row for row in rows if row.get("slug") == operation.identity), None)
        if operation.resource == "attribute_term":
            attribute_slug, term_name = operation.identity.split(":", 1)
            attributes = self.client.list_all("products/attributes")
            attribute = next((row for row in attributes if row.get("slug") == attribute_slug), None)
            if attribute is None:
                return None
            terms = self.client.list_all(f"products/attributes/{attribute['id']}/terms")
            return next((row for row in terms if row.get("name") == term_name), None)
        if operation.resource == "setting":
            group_id, setting_id = operation.identity.split(":", 1)
            rows = self.client.list_all(f"settings/{group_id}")
            return next((row for row in rows if row.get("id") == setting_id), None)
        raise PlanStateError(f"unsupported resource reader: {operation.resource}")

    def _precondition_matches(
        self,
        operation: PlannedOperation,
        current: dict[str, Any] | None,
    ) -> bool:
        precondition = operation.precondition
        if precondition is None:
            return True
        if precondition.exists != (current is not None):
            return False
        if current is None:
            return precondition.relevant_sha256 == stable_hash({"exists": False})
        if precondition.resource_id is not None and current.get("id") != precondition.resource_id:
            return False
        relevant = self._relevant_current(operation, current, operation.before or {})
        return stable_hash(relevant) == precondition.relevant_sha256

    def _matches_after(
        self,
        operation: PlannedOperation,
        current: dict[str, Any] | None,
        resolved_ids: dict[str, int] | None = None,
    ) -> bool:
        if current is None or operation.after is None:
            return current is None and operation.after is None
        expected = self._resolve_refs(deepcopy(operation.after), resolved_ids or {})
        actual = self._relevant_current(operation, current, expected)
        return actual == expected

    def _relevant_current(
        self,
        operation: PlannedOperation,
        current: dict[str, Any],
        template: dict[str, Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, expected in template.items():
            value = current.get(key)
            if operation.resource == "product" and key in {"categories", "tags"}:
                value = [
                    {"name": item.get("name"), "slug": item.get("slug")}
                    for item in value or []
                ]
            elif operation.resource == "product" and key == "attributes":
                value = self._semantic_product_attributes(value or [])
            elif isinstance(expected, dict) and "$ref" in expected:
                value = current.get(key)
            result[key] = value
        return result

    def _semantic_product_attributes(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        attributes = (
            self.client.list_all("products/attributes")
            if any(item.get("id", 0) != 0 for item in items)
            else []
        )
        by_id = {row.get("id"): row for row in attributes}
        result = []
        for item in items:
            common = {
                "name": item.get("name"),
                "options": item.get("options", []),
                "visible": item.get("visible", False),
                "variation": item.get("variation", False),
                "position": item.get("position", 0),
            }
            if item.get("id", 0) == 0:
                result.append({"scope": "local", **common})
            else:
                result.append({"scope": "global", "slug": by_id.get(item.get("id"), {}).get("slug"), **common})
        return result

    @staticmethod
    def _resolve_path(path: str, resolved_ids: dict[str, int]) -> str:
        pattern = re.compile(r"\$ref\(([^)]+)\)")
        return pattern.sub(lambda match: str(resolved_ids[match.group(1)]), path)

    def _resolve_refs(self, value: Any, resolved_ids: dict[str, int]) -> Any:
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                return resolved_ids.get(value["$ref"], value)
            return {key: self._resolve_refs(item, resolved_ids) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_refs(item, resolved_ids) for item in value]
        return value

    @staticmethod
    def _safe_actual(current: dict[str, Any] | None) -> dict[str, Any] | None:
        if current is None:
            return None
        return {key: value for key, value in current.items() if key not in {"consumer_key", "consumer_secret"}}

    @staticmethod
    def _final_state(results: list[OperationResult]) -> str:
        states = {item.state for item in results}
        if "uncertain" in states:
            return "uncertain"
        unsuccessful = states & {"conflict", "blocked_dependency", "failed", "failed_verification", "retryable", "pending"}
        if not unsuccessful:
            return "applied"
        if states & {"verified"}:
            return "partial"
        if unsuccessful <= {"conflict", "blocked_dependency"}:
            return "conflict"
        return "failed"
