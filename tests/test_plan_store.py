from datetime import UTC, datetime, timedelta

import pytest

from woo_catalog.plan_store import PlanStore
from woo_catalog.planning import ExecutionPlan, PlanStateError, PlannedOperation


class FrozenClock:
    def __init__(self, now: datetime):
        self.value = now

    def __call__(self) -> datetime:
        return self.value


def sample_plan(now: datetime, *, expires_at: datetime | None = None) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan-1",
        kind="catalog",
        state="planned",
        document_sha256="abc",
        store_identity="https://shop.example",
        created_at=now,
        expires_at=expires_at or now + timedelta(minutes=15),
        operations=[
            PlannedOperation(
                operation_id="product:A:update",
                resource="product",
                identity="A",
                action="update",
                path="products/1",
                payload={"name": "New"},
                before={"name": "Old"},
                after={"name": "New"},
                changes={"name": {"before": "Old", "after": "New"}},
            )
        ],
    )


def test_plan_survives_new_store_instance(tmp_path):
    now = datetime(2026, 9, 19, tzinfo=UTC)
    path = tmp_path / "state.sqlite3"
    plan = sample_plan(now)

    PlanStore(path, clock=FrozenClock(now)).save(plan)

    assert PlanStore(path, clock=FrozenClock(now)).get(plan.plan_id) == plan


def test_only_one_begin_apply_transition_succeeds(tmp_path):
    now = datetime(2026, 9, 19, tzinfo=UTC)
    store = PlanStore(tmp_path / "state.sqlite3", clock=FrozenClock(now))
    store.save(sample_plan(now))

    started = store.begin_apply("plan-1")

    assert started.state == "applying"
    with pytest.raises(PlanStateError, match="already applying"):
        PlanStore(store.path, clock=FrozenClock(now)).begin_apply("plan-1")


def test_expired_unstarted_plan_cannot_begin(tmp_path):
    now = datetime(2026, 9, 19, tzinfo=UTC)
    store = PlanStore(tmp_path / "state.sqlite3", clock=FrozenClock(now))
    store.save(sample_plan(now, expires_at=now - timedelta(seconds=1)))

    with pytest.raises(PlanStateError, match="expired"):
        store.begin_apply("plan-1")

    assert store.get("plan-1").state == "expired"


def test_status_marks_an_unstarted_expired_plan(tmp_path):
    now = datetime(2026, 9, 19, tzinfo=UTC)
    store = PlanStore(tmp_path / "state.sqlite3", clock=FrozenClock(now))
    store.save(sample_plan(now, expires_at=now - timedelta(seconds=1)))

    assert store.status("plan-1")["state"] == "expired"
    assert store.get("plan-1").state == "expired"


def test_operation_events_and_final_result_are_durable(tmp_path):
    now = datetime(2026, 9, 19, tzinfo=UTC)
    path = tmp_path / "state.sqlite3"
    store = PlanStore(path, clock=FrozenClock(now))
    store.save(sample_plan(now))
    store.begin_apply("plan-1")
    store.update_operation("plan-1", "product:A:update", "verified", {"actual": {"name": "New"}})
    store.finish("plan-1", "applied", {"mode": "apply", "summary": {"update": 1}})

    status = PlanStore(path, clock=FrozenClock(now)).status("plan-1")

    assert status["state"] == "applied"
    assert status["events"][0]["state"] == "verified"
    assert status["result"]["summary"] == {"update": 1}
