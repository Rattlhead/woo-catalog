from pathlib import Path

import pytest

from woo_catalog.planning import ConfirmationError, PlanLimitError, PlanStateError
from woo_catalog.service import CatalogService, PlanError


class FakeWooClient:
    def __init__(self, products=None):
        self.products = {row["sku"]: dict(row) for row in (products or [])}
        self.puts = []
        self.posts = []
        self.deleted = []

    def list_all(self, path, params=None):
        if path == "products":
            if params and "sku" in params:
                row = self.products.get(params["sku"])
                return [dict(row)] if row else []
            return [dict(row) for row in self.products.values()]
        if path in {"products/categories", "products/tags", "products/attributes"}:
            return []
        raise AssertionError(f"Unexpected list path: {path}")

    def put(self, path, payload):
        row = next(row for row in self.products.values() if path == f"products/{row['id']}")
        row.update(payload)
        self.puts.append((path, payload))
        return dict(row)

    def post(self, path, payload):
        row = {"id": len(self.products) + 100, **payload}
        self.products[row["sku"]] = row
        self.posts.append((path, payload))
        return dict(row)

    def delete(self, path, *, force=False):
        sku = next(sku for sku, row in self.products.items() if path == f"products/{row['id']}")
        row = self.products.pop(sku)
        self.deleted.append((path, force))
        return row


def test_catalog_plan_is_durable_and_apply_requires_confirmation(tmp_path):
    client = FakeWooClient([{"id": 1, "sku": "SKU-1", "type": "simple", "name": "Old"}])
    state_path = tmp_path / "state.sqlite3"
    first = CatalogService(client_factory=lambda _: client, state_path=state_path)
    document = {"schema_version": 1, "products": [{"sku": "SKU-1", "name": "New"}]}

    plan = first.plan_catalog(document)

    assert plan["document_sha256"]
    assert plan["report"]["mode"] == "dry_run"
    assert first.store.get(plan["plan_id"]).context["env_file"] == str(Path(".env").resolve())
    second = CatalogService(client_factory=lambda _: client, state_path=state_path)
    with pytest.raises(PlanError, match="confirmation"):
        second.apply_catalog(plan["plan_id"], confirmed=False)
    assert client.puts == []

    result = second.apply_catalog(plan["plan_id"], confirmed=True)

    assert result["state"] == "applied"
    assert client.puts == [("products/1", {"name": "New"})]
    with pytest.raises(PlanStateError, match="state applied"):
        second.apply_catalog(plan["plan_id"], confirmed=True)


def test_catalog_plan_persists_opt_in_batch_execution_mode(tmp_path):
    client = FakeWooClient([{"id": 1, "sku": "SKU-1", "type": "simple", "name": "Old"}])
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "state.sqlite3")

    plan = service.plan_catalog(
        {"schema_version": 1, "products": [{"sku": "SKU-1", "name": "New"}]},
        batch_products=True,
    )

    assert plan["execution_mode"] == "batch_products"
    assert service.store.get(plan["plan_id"]).context["execution_mode"] == "batch_products"


def test_catalog_apply_rejects_a_plan_for_a_different_operation(tmp_path):
    service = CatalogService(client_factory=lambda _: FakeWooClient(), state_path=tmp_path / "state.sqlite3")
    plan = service.plan_catalog({"schema_version": 1})

    with pytest.raises(PlanStateError, match="does not authorize settings"):
        service.apply_settings(plan["plan_id"], confirmed=True, confirm_settings=True)


def test_preflight_error_blocks_only_its_operation(tmp_path):
    client = FakeWooClient(
        [
            {"id": 1, "sku": "BAD", "type": "variable", "name": "Variable"},
            {"id": 2, "sku": "GOOD", "type": "simple", "name": "Old"},
        ]
    )
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "state.sqlite3")
    plan = service.plan_catalog(
        {
            "schema_version": 1,
            "products": [
                {"sku": "BAD", "name": "Unsupported"},
                {"sku": "GOOD", "name": "New"},
            ],
        }
    )

    result = service.apply_catalog(plan["plan_id"], confirmed=True)

    assert result["state"] == "partial"
    assert [item["state"] for item in result["operations"]] == ["failed", "verified"]
    assert client.puts == [("products/2", {"name": "New"})]


def test_delete_plan_requires_confirmation_before_moving_product_to_trash(tmp_path):
    client = FakeWooClient([{"id": 4, "sku": "SKU-4", "type": "simple"}])
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "state.sqlite3")
    plan = service.plan_delete({"schema_version": 1, "products": ["SKU-4"]})

    with pytest.raises(ConfirmationError, match="confirmed"):
        service.apply_delete(plan["plan_id"], confirmed=False)
    assert client.deleted == []

    result = service.apply_delete(plan["plan_id"], confirmed=True)

    assert result["state"] == "applied"
    assert client.deleted == [("products/4", False)]


def test_settings_apply_requires_specific_confirmation_and_verifies_value(tmp_path):
    class SettingsClient:
        def __init__(self):
            self.value = "RUB"
            self.puts = []

        def list_all(self, path, params=None):
            if path == "settings/general":
                return [{"id": "woocommerce_currency", "value": self.value}]
            raise AssertionError(path)

        def put(self, path, payload):
            self.value = payload["value"]
            self.puts.append((path, payload))
            return {"id": "woocommerce_currency", "value": self.value}

    client = SettingsClient()
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "state.sqlite3")
    plan = service.plan_settings(
        {
            "schema_version": 1,
            "settings": [
                {
                    "group_id": "general",
                    "settings": [{"id": "woocommerce_currency", "value": "USD"}],
                }
            ],
        }
    )

    with pytest.raises(ConfirmationError, match="confirm_settings"):
        service.apply_settings(plan["plan_id"], confirmed=True)
    result = service.apply_settings(plan["plan_id"], confirmed=True, confirm_settings=True)

    assert result["state"] == "applied"
    assert client.puts == [("settings/general/woocommerce_currency", {"value": "USD"})]


def test_permanent_delete_requires_specific_confirmation_and_read_back(tmp_path):
    class DeleteClient:
        def __init__(self):
            self.categories = [{"id": 10, "slug": "old", "name": "Old"}]
            self.deleted = []

        def list_all(self, path, params=None):
            if path == "products/categories":
                return [dict(item) for item in self.categories]
            if path == "products" and params == {"category": 10}:
                return []
            raise AssertionError((path, params))

        def delete(self, path, *, force=False):
            row = self.categories.pop()
            self.deleted.append((path, force))
            return row

    client = DeleteClient()
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "state.sqlite3")
    plan = service.plan_delete({"schema_version": 1, "categories": ["old"]})

    with pytest.raises(ConfirmationError, match="confirm_permanent"):
        service.apply_delete(plan["plan_id"], confirmed=True)
    result = service.apply_delete(plan["plan_id"], confirmed=True, confirm_permanent=True)

    assert result["state"] == "applied"
    assert client.deleted == [("products/categories/10", True)]


def test_large_plan_requires_plan_and_apply_flags(tmp_path):
    products = [
        {"id": index, "sku": f"SKU-{index}", "type": "simple", "name": "Old"}
        for index in range(1, 102)
    ]
    document = {
        "schema_version": 1,
        "products": [{"sku": row["sku"], "name": "New"} for row in products],
    }
    client = FakeWooClient(products)
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "state.sqlite3")

    with pytest.raises(PlanLimitError, match="limit is 100"):
        service.plan_catalog(document)

    plan = service.plan_catalog(document, allow_large_plan=True)
    with pytest.raises(ConfirmationError, match="confirm_large_plan"):
        service.apply_catalog(plan["plan_id"], confirmed=True)


def test_operation_status_reads_saved_journal(tmp_path):
    client = FakeWooClient([{"id": 1, "sku": "A", "type": "simple", "name": "Old"}])
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "state.sqlite3")
    plan = service.plan_catalog({"schema_version": 1, "products": [{"sku": "A", "name": "New"}]})
    service.apply_catalog(plan["plan_id"], confirmed=True)

    status = service.operation_status(plan["plan_id"])

    assert status["state"] == "applied"
    assert status["events"][0]["state"] == "verified"


def test_validate_preserves_explicit_null_in_returned_document(tmp_path):
    service = CatalogService(client_factory=lambda _: None, state_path=tmp_path / "state.sqlite3")
    raw = {"schema_version": 1, "categories": [{"name": "Child", "slug": "child", "parent_slug": None}]}

    result = service.validate_catalog(raw)

    assert result["document"] == raw


def test_large_export_returns_local_artifact_metadata(tmp_path, monkeypatch):
    client = FakeWooClient([{"id": 1, "sku": "A", "type": "simple", "name": "Item"}])
    service = CatalogService(client_factory=lambda _: client, state_path=tmp_path / "state.sqlite3")
    monkeypatch.setattr("woo_catalog.service.ARTIFACT_THRESHOLD_BYTES", 1)

    result = service.export_catalog("products")

    artifact = result["artifact"]
    assert artifact["uri"].startswith("file://")
    assert artifact["sha256"]
    assert artifact["summary"] == {"products": 1}


def test_default_client_uses_env_file_registered_by_mcp_setup(tmp_path, monkeypatch):
    env_file = tmp_path / "store.env"
    env_file.write_text(
        "WOO_BASE_URL=https://mcp.example\nWOO_CONSUMER_KEY=ck_test\nWOO_CONSUMER_SECRET=cs_test\n",
        encoding="utf-8",
    )
    for name in ("WOO_BASE_URL", "WOO_CONSUMER_KEY", "WOO_CONSUMER_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WOO_CATALOG_ENV_FILE", str(env_file))

    client = CatalogService._default_client_factory(Path(".env"))

    assert client.config.store_identity == "https://mcp.example"
