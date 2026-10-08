"""Shared operation-building helpers for catalog planning."""

from __future__ import annotations

from typing import Any

from ..planning import PlannedOperation, Precondition, stable_hash


class PlanningOperationsMixin:
    def _make_change_operation(
        self,
        resource: str,
        identity: str,
        collection_path: str,
        existing: dict[str, Any] | None,
        payload: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        depends_on: list[str] | None = None,
    ) -> PlannedOperation:
        changes = self._diff(before, after)
        action = "create" if existing is None else ("update" if changes else "no_op")
        return PlannedOperation(
            operation_id=self._operation_id(resource, identity, action),
            resource=resource,
            identity=identity,
            action=action,
            path=collection_path if existing is None else f"{collection_path}/{existing['id']}",
            payload=payload if action != "no_op" else None,
            before=before or None,
            after=after or None,
            changes=changes,
            precondition=Precondition(
                exists=existing is not None,
                identity=identity,
                resource_id=existing.get("id") if existing else None,
                relevant_sha256=stable_hash(before if existing else {"exists": False}),
            ),
            depends_on=depends_on or [],
        )

    def _no_op(self, resource: str, identity: str, existing: dict[str, Any]) -> PlannedOperation:
        return PlannedOperation(
            operation_id=self._operation_id(resource, identity, "no_op"),
            resource=resource,
            identity=identity,
            action="no_op",
            before={"id": existing.get("id")},
            after={"id": existing.get("id")},
            precondition=Precondition(
                exists=True,
                identity=identity,
                resource_id=existing.get("id"),
                relevant_sha256=stable_hash({"id": existing.get("id")}),
            ),
        )

    def _error(self, resource: str, identity: str, message: str) -> PlannedOperation:
        return PlannedOperation(
            operation_id=self._operation_id(resource, identity, "error"),
            resource=resource,
            identity=identity,
            action="error",
            message=message,
        )

    @staticmethod
    def _select_before(existing: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
        return {} if existing is None else {key: existing.get(key) for key in payload}

    @staticmethod
    def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            key: {"before": before.get(key), "after": after.get(key)}
            for key in after
            if before.get(key) != after.get(key)
        }

    @staticmethod
    def _operation_id(resource: str, identity: str, action: str) -> str:
        return f"{resource}:{identity}:{action}"

    @staticmethod
    def _path_ref(value: int | dict[str, str]) -> str:
        return str(value) if isinstance(value, int) else f"$ref({value['$ref']})"
