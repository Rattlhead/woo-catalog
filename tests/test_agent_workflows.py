"""End-to-end evidence for the eight LLM audit findings."""

from __future__ import annotations

import asyncio

import pytest

from woo_catalog.mcp_server import create_server
from woo_catalog.planning import ConfirmationError
from woo_catalog.service import CatalogService


class WorkflowClient:
    def __init__(self, products=None):
        self.products = {row["sku"]: dict(row) for row in (products or [])}
        self.categories: list[dict] = []
        self.tags: list[dict] = []
        self.attributes: list[dict] = []
        self.terms: dict[int, list[dict]] = {}
        self.reads: list[tuple[str, dict | None]] = []
        self.writes: list[tuple[str, str, dict]] = []

    def list_all(self, path, params=None):
        self.reads.append((path, params))
        if path == "products":
            if params and params.get("sku"):
                row = self.products.get(params["sku"])
                return [dict(row)] if row else []
            return [dict(row) for row in self.products.values()]
        if path == "products/categories":
            return [dict(row) for row in self.categories]
        if path == "products/tags":
            return [dict(row) for row in self.tags]
        if path == "products/attributes":
            return [dict(row) for row in self.attributes]
        if path.startswith("products/attributes/") and path.endswith("/terms"):
            return [dict(row) for row in self.terms.get(int(path.split("/")[2]), [])]
        raise AssertionError(f"unexpected read: {path}")

    def put(self, path, payload):
        sku = next(sku for sku, row in self.products.items() if path == f"products/{row['id']}")
        self.products[sku].update(payload)
        self.writes.append(("PUT", path, payload))
        return dict(self.products[sku])

    def find_product(self, sku):
        rows = self.list_all("products", params={"sku": sku})
        return rows[0] if rows else None

    def post(self, path, payload):
        if path == "products":
            row = {"id": len(self.products) + 100, "type": "simple", **payload}
            self.products[row["sku"]] = row
        elif path == "products/categories":
            row = {"id": len(self.categories) + 1, **payload}
            self.categories.append(row)
        else:
            raise AssertionError(f"unexpected write: {path}")
        self.writes.append(("POST", path, payload))
        return dict(row)


def plan_drift(tmp_path) -> bool:
    client = WorkflowClient([{"id": 1, "sku": "A", "type": "simple", "name": "Old"}])
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "drift.sqlite3")
    plan = service.plan_catalog({"schema_version": 1, "products": [{"sku": "A", "name": "New"}]})
    client.products["A"]["name"] = "Concurrent"
    result = service.apply_catalog(plan["plan_id"], confirmed=True)
    return result["state"] == "conflict" and client.writes == []


def semantic_json(tmp_path) -> bool:
    service = CatalogService(client_factory=lambda _: None, state_path=tmp_path / "semantic.sqlite3")
    raw = {"schema_version": 1, "categories": [{"name": "Root", "slug": "root", "parent_slug": None}]}
    return service.validate_catalog(raw)["document"] == raw


def local_attribute_round_trip(tmp_path) -> bool:
    client = WorkflowClient(
        [
            {
                "id": 1,
                "sku": "A",
                "type": "simple",
                "name": "Item",
                "attributes": [{"id": 0, "name": "Engraving", "options": ["Yes"]}],
            }
        ]
    )
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "export.sqlite3")
    document = service.export_catalog("products")
    export_reads = list(client.reads)
    plan = service.plan_catalog(document)
    result = service.apply_catalog(plan["plan_id"], confirmed=True)
    return document["products"][0]["attributes"][0] == {
        "scope": "local",
        "name": "Engraving",
        "options": ["Yes"],
        "visible": False,
        "variation": False,
        "position": 0,
    } and export_reads == [("products", None)] and result["state"] == "applied" and client.writes == []


def invalid_product_dependencies(tmp_path) -> bool:
    client = WorkflowClient()
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "dependencies.sqlite3")
    plan = service.plan_catalog(
        {
            "schema_version": 1,
            "products": [
                {
                    "sku": "NEW",
                    "name": "Skipped",
                    "categories": [{"name": "Must not exist", "slug": "unused"}],
                }
            ],
        }
    )
    return [item["resource"] for item in plan["operations"]] == ["product"] and plan["operations"][0]["action"] == "skipped_new"


def preflight_diff(tmp_path) -> bool:
    client = WorkflowClient(
        [{"id": 1, "sku": "A", "type": "simple", "name": "Old", "description": "<b>data</b>"}]
    )
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "diff.sqlite3")
    plan = service.plan_catalog({"schema_version": 1, "products": [{"sku": "A", "name": "New"}]})
    operation = plan["operations"][0]
    return operation["before"] == {"name": "Old"} and operation["after"] == {"name": "New"} and operation["changes"]["name"] == {"before": "Old", "after": "New"}


def typed_mcp(tmp_path) -> bool:
    server = create_server(CatalogService(client_factory=lambda _: None, state_path=tmp_path / "mcp.sqlite3"))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    schema = tools["catalog_plan_sync"].inputSchema["properties"]["document"]
    return "products" in schema.get("properties", {}) and "untrusted data" in server.instructions.lower()


def targeted_reads(tmp_path) -> bool:
    client = WorkflowClient([{"id": 1, "sku": "A", "type": "simple", "name": "Item", "attributes": []}])
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "reads.sqlite3")
    product = service.get_product("A", fields=["sku", "name"])
    return product == {"sku": "A", "name": "Item"} and client.reads == [("products", {"sku": "A"})]


def durable_recovery(tmp_path) -> bool:
    client = WorkflowClient([{"id": 1, "sku": "A", "type": "simple", "name": "Old"}])
    state = tmp_path / "durable.sqlite3"
    first = CatalogService(client_factory=lambda _: client, state_path=state)
    plan = first.plan_catalog({"schema_version": 1, "products": [{"sku": "A", "name": "New"}]})
    second = CatalogService(client_factory=lambda _: client, state_path=state)
    result = second.apply_catalog(plan["plan_id"], confirmed=True)
    status = second.operation_status(plan["plan_id"])
    return result["state"] == "applied" and status["state"] == "applied"


AUDIT_SCENARIOS = {
    "plan_drift": plan_drift,
    "semantic_json": semantic_json,
    "local_attribute_round_trip": local_attribute_round_trip,
    "invalid_product_dependencies": invalid_product_dependencies,
    "preflight_diff": preflight_diff,
    "typed_mcp": typed_mcp,
    "targeted_reads": targeted_reads,
    "durable_recovery": durable_recovery,
}


@pytest.mark.parametrize("scenario", AUDIT_SCENARIOS.values(), ids=AUDIT_SCENARIOS.keys())
def test_audit_regression(scenario, tmp_path):
    assert scenario(tmp_path)


def test_publish_transition_requires_specific_confirmation(tmp_path):
    client = WorkflowClient(
        [{"id": 1, "sku": "A", "type": "simple", "name": "Item", "status": "draft"}]
    )
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "publish.sqlite3")
    plan = service.plan_catalog({"schema_version": 1, "products": [{"sku": "A", "status": "publish"}]})

    assert "confirm_publish" in plan["required_confirmations"]
    with pytest.raises(ConfirmationError, match="confirm_publish"):
        service.apply_catalog(plan["plan_id"], confirmed=True)
    assert client.writes == []
