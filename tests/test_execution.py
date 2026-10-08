from datetime import UTC, datetime, timedelta

from woo_catalog.client import WooClientError
from woo_catalog.execution import PlanExecutor
from woo_catalog.plan_store import PlanStore
from woo_catalog.planning import Confirmations, ExecutionPlan, PlannedOperation, Precondition, stable_hash


class MutableProductClient:
    def __init__(self, products):
        self.products = {row["sku"]: dict(row) for row in products}
        self.writes = []
        self.timeout_after_persist = set()
        self.timeout_before_persist = set()
        self.fail_read_after_write = set()
        self.batches = []

    def list_all(self, path, params=None):
        if path == "products":
            sku = (params or {}).get("sku")
            if sku in self.fail_read_after_write and self.writes:
                raise WooClientError("WooCommerce request failed")
            product = self.products.get(sku)
            return [dict(product)] if product else []
        if path in {"products/categories", "products/tags", "products/attributes"}:
            return []
        raise AssertionError(f"Unexpected list path: {path}")

    def put(self, path, payload):
        sku = next(sku for sku, row in self.products.items() if path == f"products/{row['id']}")
        if sku in self.timeout_before_persist:
            raise WooClientError("WooCommerce request failed", request_may_have_been_sent=True)
        self.products[sku].update(payload)
        self.writes.append(("PUT", path, payload))
        if sku in self.timeout_after_persist:
            raise WooClientError("WooCommerce request failed", request_may_have_been_sent=True)
        return dict(self.products[sku])

    def post(self, path, payload):
        row = {"id": max([item["id"] for item in self.products.values()] + [0]) + 1, **payload}
        self.products[row["sku"]] = row
        self.writes.append(("POST", path, payload))
        return dict(row)

    def delete(self, path, *, force=False):
        raise AssertionError("delete is not expected")

    def post_batch(self, path, payload):
        assert path == "products/batch"
        self.batches.append(payload)
        for item in payload.get("update", []):
            row = next(row for row in self.products.values() if row["id"] == item["id"])
            row.update({key: value for key, value in item.items() if key != "id"})
        for item in payload.get("create", []):
            row = {"id": max([row["id"] for row in self.products.values()] + [0]) + 1, **item}
            self.products[row["sku"]] = row
        return payload


def product_operation(sku, before, after, *, action="update", resource_id=1):
    return PlannedOperation(
        operation_id=f"product:{sku}:{action}",
        resource="product",
        identity=sku,
        action=action,
        path=f"products/{resource_id}" if action != "create" else "products",
        payload=after if action != "no_op" else None,
        before=before,
        after=after,
        changes={key: {"before": before.get(key), "after": value} for key, value in after.items() if before.get(key) != value},
        precondition=Precondition(
            exists=action != "create",
            identity=sku,
            resource_id=resource_id if action != "create" else None,
            relevant_sha256=stable_hash(before if action != "create" else {"exists": False}),
        ),
    )


def save_plan(path, operations, *, context=None):
    now = datetime.now(UTC)
    plan = ExecutionPlan(
        plan_id="plan-1",
        kind="catalog",
        state="planned",
        document_sha256="document",
        store_identity="https://shop.example",
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        operations=operations,
        context=context or {},
    )
    PlanStore(path).save(plan)
    return plan.plan_id


def test_no_op_plan_never_turns_into_update(tmp_path):
    client = MutableProductClient([{"id": 1, "sku": "A", "name": "Wanted", "type": "simple"}])
    path = tmp_path / "state.sqlite3"
    plan_id = save_plan(path, [product_operation("A", {"name": "Wanted"}, {"name": "Wanted"}, action="no_op")])
    client.products["A"]["name"] = "Concurrent edit"

    result = PlanExecutor(client, PlanStore(path)).apply(plan_id, Confirmations(confirmed=True))

    assert client.writes == []
    assert result.state == "conflict"
    assert result.operations[0].state == "conflict"


def test_matching_object_created_after_plan_is_verified_without_duplicate_write(tmp_path):
    client = MutableProductClient([{"id": 7, "sku": "A", "name": "New", "type": "simple"}])
    path = tmp_path / "state.sqlite3"
    plan_id = save_plan(path, [product_operation("A", {}, {"name": "New"}, action="create")])

    result = PlanExecutor(client, PlanStore(path)).apply(plan_id, Confirmations(confirmed=True))

    assert result.state == "applied"
    assert result.operations[0].details["already_applied"] is True
    assert client.writes == []


def test_timeout_after_write_is_uncertain_until_reconcile(tmp_path):
    client = MutableProductClient([{"id": 1, "sku": "A", "name": "Old", "type": "simple"}])
    client.timeout_after_persist.add("A")
    path = tmp_path / "state.sqlite3"
    plan_id = save_plan(path, [product_operation("A", {"name": "Old"}, {"name": "New"})])

    result = PlanExecutor(client, PlanStore(path)).apply(plan_id, Confirmations(confirmed=True))
    reconciled = PlanExecutor(client, PlanStore(path)).reconcile(plan_id)

    assert result.state == "uncertain"
    assert reconciled.state == "applied"
    assert len(client.writes) == 1


def test_retry_is_allowed_only_after_reconcile_proves_original_state(tmp_path):
    client = MutableProductClient([{"id": 1, "sku": "A", "name": "Old", "type": "simple"}])
    client.timeout_before_persist.add("A")
    path = tmp_path / "state.sqlite3"
    plan_id = save_plan(path, [product_operation("A", {"name": "Old"}, {"name": "New"})])

    uncertain = PlanExecutor(client, PlanStore(path)).apply(plan_id, Confirmations(confirmed=True))
    retryable = PlanExecutor(client, PlanStore(path)).reconcile(plan_id)
    client.timeout_before_persist.clear()
    retried = PlanExecutor(client, PlanStore(path)).apply(plan_id, Confirmations(confirmed=True))

    assert uncertain.state == "uncertain"
    assert retryable.operations[0].state == "retryable"
    assert retried.state == "applied"
    assert client.products["A"]["name"] == "New"


def test_read_back_failure_is_journaled_as_uncertain_and_reconciled(tmp_path):
    client = MutableProductClient([{"id": 1, "sku": "A", "name": "Old", "type": "simple"}])
    client.fail_read_after_write.add("A")
    path = tmp_path / "state.sqlite3"
    plan_id = save_plan(path, [product_operation("A", {"name": "Old"}, {"name": "New"})])

    result = PlanExecutor(client, PlanStore(path)).apply(plan_id, Confirmations(confirmed=True))
    still_uncertain = PlanExecutor(client, PlanStore(path)).reconcile(plan_id)
    client.fail_read_after_write.clear()
    verified = PlanExecutor(client, PlanStore(path)).reconcile(plan_id)

    assert result.operations[0].details["phase"] == "read_back"
    assert result.state == "uncertain"
    assert still_uncertain.state == "uncertain"
    assert verified.state == "applied"


def test_independent_operation_continues_after_conflict(tmp_path):
    client = MutableProductClient(
        [
            {"id": 1, "sku": "A", "name": "Changed", "type": "simple"},
            {"id": 2, "sku": "B", "name": "Old B", "type": "simple"},
        ]
    )
    path = tmp_path / "state.sqlite3"
    plan_id = save_plan(
        path,
        [
            product_operation("A", {"name": "Old A"}, {"name": "New A"}, resource_id=1),
            product_operation("B", {"name": "Old B"}, {"name": "New B"}, resource_id=2),
        ],
    )

    result = PlanExecutor(client, PlanStore(path)).apply(plan_id, Confirmations(confirmed=True))

    assert [item.state for item in result.operations] == ["conflict", "verified"]
    assert result.state == "partial"
    assert client.products["B"]["name"] == "New B"


def test_batch_products_is_opt_in_and_read_back_verifies_each_operation(tmp_path):
    client = MutableProductClient([
        {"id": 1, "sku": "A", "name": "Old A", "type": "simple"},
        {"id": 2, "sku": "B", "name": "Old B", "type": "simple"},
    ])
    path = tmp_path / "state.sqlite3"
    plan_id = save_plan(
        path,
        [
            product_operation("A", {"name": "Old A"}, {"name": "New A"}, resource_id=1),
            product_operation("B", {"name": "Old B"}, {"name": "New B"}, resource_id=2),
        ],
        context={"execution_mode": "batch_products"},
    )

    result = PlanExecutor(client, PlanStore(path)).apply(plan_id, Confirmations(confirmed=True))

    assert result.state == "applied"
    assert len(client.batches) == 1
    assert [item["id"] for item in client.batches[0]["update"]] == [1, 2]
    assert all(item.details["execution"] == "batch" for item in result.operations)


def test_batch_products_splits_at_the_documented_woocommerce_limit(tmp_path):
    rows = [
        {"id": index, "sku": f"SKU-{index}", "name": "Old", "type": "simple"}
        for index in range(1, 102)
    ]
    client = MutableProductClient(rows)
    path = tmp_path / "state.sqlite3"
    plan_id = save_plan(
        path,
        [
            product_operation(row["sku"], {"name": "Old"}, {"name": "New"}, resource_id=row["id"])
            for row in rows
        ],
        context={"execution_mode": "batch_products"},
    )

    result = PlanExecutor(client, PlanStore(path)).apply(plan_id, Confirmations(confirmed=True))

    assert result.state == "applied"
    assert [len(batch["update"]) for batch in client.batches] == [100, 1]
