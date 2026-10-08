"""Round-trip export and targeted update of WooCommerce settings."""

from __future__ import annotations

from typing import Any

from .client import WooClient
from .planning import ExecutionPlan, PlanLimitError, PlannedOperation, Precondition, stable_hash
from .schemas import SettingsChange


MAX_PLAN_OPERATIONS = 100


def export_settings(client: WooClient) -> dict[str, Any]:
    groups = client.list_all("settings")
    return {
        "schema_version": 1,
        "settings": [
            {
                "group_id": group["id"],
                "settings": [
                    {"id": setting["id"], "value": setting.get("value")}
                    for setting in client.list_all(f"settings/{group['id']}")
                    if setting.get("id")
                ],
            }
            for group in groups
            if group.get("id")
        ],
    }


class SettingsPlanner:
    def __init__(self, client: WooClient, *, store_identity: str = "unknown") -> None:
        self.client = client
        self.store_identity = store_identity

    def plan(self, change: SettingsChange, *, allow_large_plan: bool = False) -> ExecutionPlan:
        explicit_groups = len(change.settings)
        explicit_settings = sum(len(group.settings) for group in change.settings)
        oversized = explicit_groups > MAX_PLAN_OPERATIONS or explicit_settings > MAX_PLAN_OPERATIONS
        if oversized and not allow_large_plan:
            count = max(explicit_groups, explicit_settings)
            raise PlanLimitError(
                f"document contains {count} explicit settings entities; limit is {MAX_PLAN_OPERATIONS}"
            )
        operations: list[PlannedOperation] = []
        for group in change.settings:
            try:
                current = {item.get("id"): item for item in self.client.list_all(f"settings/{group.group_id}")}
            except Exception as error:
                operations.append(self._error("setting_group", group.group_id, str(error)))
                continue
            for desired in group.settings:
                identity = f"{group.group_id}:{desired.id}"
                existing = current.get(desired.id)
                if existing is None:
                    operations.append(self._error("setting", identity, "unknown setting"))
                    continue
                if existing.get("readonly") is True or existing.get("read_only") is True:
                    operations.append(self._error("setting", identity, "setting is read-only"))
                    continue
                before = {"value": existing.get("value")}
                after = {"value": desired.value}
                action = "no_op" if before == after else "update"
                operations.append(
                    PlannedOperation(
                        operation_id=f"setting:{identity}:{action}",
                        resource="setting",
                        identity=identity,
                        action=action,
                        path=f"settings/{group.group_id}/{desired.id}",
                        payload={"value": desired.value} if action == "update" else None,
                        before=before,
                        after=after,
                        changes={} if action == "no_op" else {"value": {"before": before["value"], "after": after["value"]}},
                        precondition=Precondition(
                            exists=True,
                            identity=identity,
                            relevant_sha256=stable_hash(before),
                        ),
                        risks={"settings"} if action == "update" else set(),
                    )
                )
        executable = sum(operation.action == "update" for operation in operations)
        required = {"confirmed", "confirm_settings"} if executable else {"confirmed"}
        warnings: list[str] = []
        if executable > MAX_PLAN_OPERATIONS or oversized:
            if executable > MAX_PLAN_OPERATIONS and not allow_large_plan:
                raise PlanLimitError(
                    f"plan contains {executable} executable operations; limit is {MAX_PLAN_OPERATIONS}"
                )
            required.add("confirm_large_plan")
            warnings.append(f"large plan contains {executable} executable operations")
        return ExecutionPlan.create(
            kind="settings",
            document=change,
            store_identity=self.store_identity,
            operations=operations,
            required_confirmations=required,
            warnings=warnings,
        )

    @staticmethod
    def _error(resource: str, identity: str, message: str) -> PlannedOperation:
        return PlannedOperation(
            operation_id=f"{resource}:{identity}:error",
            resource=resource,
            identity=identity,
            action="error",
            message=message,
        )


from .compat.settings import SettingsSynchronizer, validate_settings_document

__all__ = [
    "SettingsPlanner",
    "SettingsSynchronizer",
    "export_settings",
    "validate_settings_document",
]
