"""Durable structured operations shared by the MCP and CLI adapters."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Callable, Literal

from .planners import CatalogPlanner
from .client import WooClient
from .config import WooConfig
from .deletion import DeletionPlanner
from .execution import PlanExecutor
from .exporter import _to_product, export_attributes, export_catalog, export_categories, export_products, export_tags
from .plan_store import PlanStore
from .planning import Confirmations, ExecutionPlan, PlanError, PlanStateError, canonical_document
from .schemas import (
    CatalogChange,
    DeleteRequest,
    Operation,
    RunReport,
    SettingsChange,
)
from .settings import SettingsPlanner, export_settings


ClientFactory = Callable[[Path], WooClient]
ARTIFACT_THRESHOLD_BYTES = 1024 * 1024


class CatalogService:
    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        state_path: str | Path | None = None,
    ) -> None:
        self._client_factory = client_factory or self._default_client_factory
        configured_path = state_path or os.getenv("WOO_CATALOG_STATE_PATH") or ".woo-catalog/state.sqlite3"
        self.store = PlanStore(configured_path)

    @staticmethod
    def _default_client_factory(env_file: Path) -> WooClient:
        return WooClient(WooConfig.from_env(CatalogService._effective_env_file(env_file)))

    @staticmethod
    def _effective_env_file(env_file: Path) -> Path:
        configured = os.getenv("WOO_CATALOG_ENV_FILE")
        if configured and env_file == Path(".env"):
            return Path(configured)
        return env_file

    @staticmethod
    def validate_catalog(document: CatalogChange | dict[str, Any]) -> dict[str, Any]:
        change = document if isinstance(document, CatalogChange) else CatalogChange.model_validate(document)
        canonical = canonical_document(change)
        report = RunReport(
            mode="validate",
            summary={"create": 0, "update": 0, "no_op": 1, "error": 0},
            operations=[
                Operation(
                    resource="change",
                    identity="document",
                    action="no_op",
                    status="applied",
                    message="valid",
                )
            ],
        )
        return {"document": canonical, "report": report.model_dump(exclude_none=True)}

    def export_catalog(
        self,
        resource: Literal["products", "categories", "tags", "attributes", "all"] = "all",
        *,
        env_file: Path = Path(".env"),
    ) -> dict[str, Any]:
        client = self._client_factory(env_file)
        exporters = {
            "products": export_products,
            "categories": export_categories,
            "tags": export_tags,
            "attributes": export_attributes,
            "all": export_catalog,
        }
        document = exporters[resource](client).model_dump(mode="json", exclude_none=True)
        if resource != "all":
            document = {"schema_version": 1, resource: document[resource]}
        return self._artifact_or_document(document, resource)

    def export_settings(self, *, env_file: Path = Path(".env")) -> dict[str, Any]:
        return export_settings(self._client_factory(env_file))

    def diagnose(self, *, env_file: Path = Path(".env")) -> dict[str, Any]:
        return self._client_factory(env_file).diagnose()

    def plan_catalog(
        self,
        document: CatalogChange | dict[str, Any],
        *,
        create_products: bool = False,
        allow_large_plan: bool = False,
        batch_products: bool = False,
        env_file: Path = Path(".env"),
    ) -> dict[str, Any]:
        change = document if isinstance(document, CatalogChange) else CatalogChange.model_validate(document)
        client = self._client_factory(env_file)
        plan = CatalogPlanner(client, store_identity=self._store_identity(client, env_file)).plan(
            change,
            create_products=create_products,
            allow_large_plan=allow_large_plan,
        )
        return self._save_plan(
            plan,
            env_file,
            context={"execution_mode": "batch_products" if batch_products else "sequential"},
        )

    def apply_catalog(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        confirm_publish: bool = False,
        confirm_large_plan: bool = False,
    ) -> dict[str, Any]:
        return self._apply(
            plan_id,
            "catalog",
            Confirmations(
                confirmed=confirmed,
                confirm_publish=confirm_publish,
                confirm_large_plan=confirm_large_plan,
            ),
        )

    def plan_settings(
        self,
        document: SettingsChange | dict[str, Any],
        *,
        allow_large_plan: bool = False,
        env_file: Path = Path(".env"),
    ) -> dict[str, Any]:
        change = document if isinstance(document, SettingsChange) else SettingsChange.model_validate(document)
        client = self._client_factory(env_file)
        plan = SettingsPlanner(client, store_identity=self._store_identity(client, env_file)).plan(
            change,
            allow_large_plan=allow_large_plan,
        )
        return self._save_plan(plan, env_file)

    def apply_settings(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        confirm_settings: bool = False,
        confirm_large_plan: bool = False,
    ) -> dict[str, Any]:
        return self._apply(
            plan_id,
            "settings",
            Confirmations(
                confirmed=confirmed,
                confirm_settings=confirm_settings,
                confirm_large_plan=confirm_large_plan,
            ),
        )

    def plan_delete(
        self,
        document: DeleteRequest | dict[str, Any],
        *,
        allow_large_plan: bool = False,
        env_file: Path = Path(".env"),
    ) -> dict[str, Any]:
        request = document if isinstance(document, DeleteRequest) else DeleteRequest.model_validate(document)
        client = self._client_factory(env_file)
        plan = DeletionPlanner(client, store_identity=self._store_identity(client, env_file)).plan(
            request,
            allow_large_plan=allow_large_plan,
        )
        return self._save_plan(plan, env_file)

    def apply_delete(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        confirm_permanent: bool = False,
        confirm_large_plan: bool = False,
    ) -> dict[str, Any]:
        return self._apply(
            plan_id,
            "delete",
            Confirmations(
                confirmed=confirmed,
                confirm_permanent=confirm_permanent,
                confirm_large_plan=confirm_large_plan,
            ),
        )

    def operation_status(self, plan_id: str, *, reconcile: bool = False) -> dict[str, Any]:
        if not reconcile:
            return self.store.status(plan_id)
        status = self.store.status(plan_id)
        if status["state"] != "uncertain":
            raise PlanStateError("reconciliation requires a plan in uncertain state")
        plan = self.store.get(plan_id)
        client = self._client_for_plan(plan)
        return PlanExecutor(client, self.store).reconcile(plan_id).model_dump(mode="json")

    def get_product(
        self,
        sku: str,
        *,
        fields: list[str] | None = None,
        env_file: Path = Path(".env"),
    ) -> dict[str, Any] | None:
        client = self._client_factory(env_file)
        row = client.find_product(sku)
        if row is None:
            return None
        global_attributes = [item for item in row.get("attributes", []) if item.get("id", 0) != 0]
        attributes_by_id: dict[int, dict[str, Any]] = {}
        if global_attributes:
            attributes_by_id = {
                item["id"]: item for item in client.list_all("products/attributes") if item.get("id")
            }
        document = _to_product(row, attributes_by_id).model_dump(mode="json", exclude_none=True)
        return self._select_fields(document, fields)

    def list_products(
        self,
        *,
        page: int = 1,
        limit: int = 20,
        fields: list[str] | None = None,
        env_file: Path = Path(".env"),
    ) -> dict[str, Any]:
        result = self._client_factory(env_file).list_page("products", page=page, per_page=limit)
        return {
            "items": [self._select_fields(item, fields) for item in result.items],
            "page": result.page,
            "next_cursor": str(result.page + 1) if result.page < result.total_pages else None,
            "total_pages": result.total_pages,
            "total_items": result.total_items,
        }

    def search_taxonomy(
        self,
        resource: Literal["categories", "tags", "attributes"],
        query: str,
        *,
        page: int = 1,
        limit: int = 20,
        env_file: Path = Path(".env"),
    ) -> dict[str, Any]:
        paths = {
            "categories": "products/categories",
            "tags": "products/tags",
            "attributes": "products/attributes",
        }
        result = self._client_factory(env_file).list_page(
            paths[resource],
            page=page,
            per_page=limit,
            params={"search": query},
        )
        return {
            "items": result.items,
            "page": result.page,
            "next_cursor": str(result.page + 1) if result.page < result.total_pages else None,
            "total_pages": result.total_pages,
            "total_items": result.total_items,
        }

    @staticmethod
    def catalog_schema() -> dict[str, Any]:
        return {
            "catalog_change": CatalogChange.model_json_schema(),
            "delete_request": DeleteRequest.model_json_schema(),
            "settings_change": SettingsChange.model_json_schema(),
            "semantics": {
                "missing_field": "leave unchanged",
                "explicit_null": "distinct from omission and allowed only where the schema permits null",
                "empty_array_replace": "clear the field",
                "merge": "preserve unmentioned existing array entries",
                "untrusted_store_content": True,
            },
            "examples": {
                "update_price": {
                    "schema_version": 1,
                    "products": [{"sku": "EXAMPLE-001", "regular_price": "1990.00"}],
                },
                "merge_tag": {
                    "schema_version": 1,
                    "products": [
                        {
                            "sku": "EXAMPLE-001",
                            "tags_mode": "merge",
                            "tags": [{"name": "New", "slug": "new"}],
                        }
                    ],
                },
                "local_attribute": {
                    "schema_version": 1,
                    "products": [
                        {
                            "sku": "EXAMPLE-001",
                            "attributes_mode": "merge",
                            "attributes": [
                                {"scope": "local", "name": "Engraving", "options": ["Yes"]}
                            ],
                        }
                    ],
                },
            },
        }

    def _save_plan(
        self,
        plan: ExecutionPlan,
        env_file: Path,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective_env_file = self._effective_env_file(env_file).resolve()
        saved = plan.model_copy(
            update={"context": {**plan.context, "env_file": str(effective_env_file), **(context or {})}}
        )
        self.store.save(saved)
        view = saved.public_view()
        view["execution_mode"] = saved.context.get("execution_mode", "sequential")
        view["report"] = self._plan_report(saved).model_dump(mode="json", exclude_none=True)
        return view

    def _apply(self, plan_id: str, kind: str, confirmations: Confirmations) -> dict[str, Any]:
        plan = self.store.get(plan_id)
        if plan.kind != kind:
            raise PlanStateError(f"plan does not authorize {kind} changes")
        client = self._client_for_plan(plan)
        current_identity = self._store_identity(client, Path(plan.context.get("env_file", ".env")))
        if current_identity != plan.store_identity:
            raise PlanStateError("plan belongs to a different WooCommerce store")
        result = PlanExecutor(client, self.store).apply(plan_id, confirmations)
        return result.model_dump(mode="json")

    def _client_for_plan(self, plan: ExecutionPlan) -> WooClient:
        return self._client_factory(Path(plan.context.get("env_file", ".env")))

    @staticmethod
    def _store_identity(client: WooClient, env_file: Path) -> str:
        config = getattr(client, "config", None)
        identity = getattr(config, "store_identity", None)
        return identity or f"local:{env_file.resolve()}"

    @staticmethod
    def _plan_report(plan: ExecutionPlan) -> RunReport:
        operations: list[Operation] = []
        summary = {"create": 0, "update": 0, "delete": 0, "no_op": 0, "skipped_new": 0, "error": 0}
        for item in plan.operations:
            summary[item.action] = summary.get(item.action, 0) + 1
            status = "error" if item.action == "error" else (
                "planned" if item.action in {"create", "update", "delete"} else "skipped"
            )
            operations.append(
                Operation(
                    resource=item.resource,
                    identity=item.identity,
                    action=item.action,
                    status=status,
                    message=item.message,
                    details={
                        "before": item.before,
                        "after": item.after,
                        "changes": item.changes,
                        "risks": sorted(item.risks),
                        **item.details,
                    },
                )
            )
        return RunReport(mode="dry_run", summary=summary, operations=operations)

    def _artifact_or_document(self, document: dict[str, Any], resource: str) -> dict[str, Any]:
        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(encoded) <= ARTIFACT_THRESHOLD_BYTES:
            return document
        digest = sha256(encoded).hexdigest()
        directory = self.store.path.parent / "artifacts"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"{resource}-{digest[:16]}.json"
        path.write_bytes(encoded)
        return {
            "artifact": {
                "uri": path.resolve().as_uri(),
                "sha256": digest,
                "bytes": len(encoded),
                "summary": {
                    key: len(value) for key, value in document.items() if isinstance(value, list)
                },
            }
        }

    @staticmethod
    def _select_fields(document: dict[str, Any], fields: list[str] | None) -> dict[str, Any]:
        if fields is None:
            return document
        unknown = set(fields) - set(document)
        if unknown:
            raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
        return {field: document[field] for field in fields}


__all__ = ["CatalogService", "PlanError"]
