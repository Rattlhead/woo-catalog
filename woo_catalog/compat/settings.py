"""Original settings validation and immediate synchronization API."""

from __future__ import annotations

from typing import Any

from ..client import WooClient
from ..schemas import Operation, RunReport


def validate_settings_document(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "settings"} or raw.get("schema_version") != 1 or not isinstance(raw["settings"], list):
        raise ValueError("settings document must contain only schema_version: 1 and a settings array")
    groups: list[dict[str, Any]] = []
    for group in raw["settings"]:
        if not isinstance(group, dict) or set(group) != {"group_id", "settings"} or not isinstance(group.get("group_id"), str) or not isinstance(group.get("settings"), list):
            raise ValueError("each settings group must contain group_id and settings")
        values = []
        for setting in group["settings"]:
            if not isinstance(setting, dict) or set(setting) != {"id", "value"} or not isinstance(setting.get("id"), str):
                raise ValueError("each imported setting must contain only id and value")
            values.append(setting)
        groups.append({"group_id": group["group_id"], "settings": values})
    return groups


class SettingsSynchronizer:
    def __init__(self, client: WooClient):
        self.client = client

    def run(self, groups: list[dict[str, Any]], *, apply: bool) -> RunReport:
        operations: list[Operation] = []
        for group in groups:
            group_id = group["group_id"]
            try:
                current = {item.get("id"): item for item in self.client.list_all(f"settings/{group_id}")}
            except Exception as error:
                operations.append(Operation(resource="setting_group", identity=group_id, action="error", status="error", message=str(error)))
                continue
            for desired in group["settings"]:
                setting_id = desired["id"]
                identity = f"{group_id}:{setting_id}"
                existing = current.get(setting_id)
                if existing is None:
                    operations.append(Operation(resource="setting", identity=identity, action="error", status="error", message="unknown setting"))
                elif existing.get("value") == desired["value"]:
                    operations.append(Operation(resource="setting", identity=identity, action="no_op", status="skipped"))
                elif not apply:
                    operations.append(Operation(resource="setting", identity=identity, action="update", status="planned"))
                else:
                    try:
                        self.client.put(f"settings/{group_id}/{setting_id}", {"value": desired["value"]})
                        operations.append(Operation(resource="setting", identity=identity, action="update", status="applied"))
                    except Exception as error:
                        operations.append(Operation(resource="setting", identity=identity, action="error", status="error", message=str(error)))
        return RunReport(mode="apply" if apply else "dry_run", operations=operations, summary=self._summary(operations))

    @staticmethod
    def _summary(operations: list[Operation]) -> dict[str, int]:
        summary = {"update": 0, "no_op": 0, "error": 0}
        for operation in operations:
            summary[operation.action] = summary.get(operation.action, 0) + 1
        return summary
