"""Durable local storage for confirmed Woo Catalog plans and execution events."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from .planning import ExecutionPlan, PlanState, PlanStateError


Clock = Callable[[], datetime]


class PlanStore:
    def __init__(self, path: str | Path = Path(".woo-catalog/state.sqlite3"), *, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or (lambda: datetime.now(UTC))
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not parent_existed or self.path.parent.name == ".woo-catalog":
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    document_sha256 TEXT NOT NULL,
                    store_identity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS operation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL REFERENCES plans(plan_id),
                    operation_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def save(self, plan: ExecutionPlan) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO plans (
                    plan_id, kind, state, document_sha256, store_identity,
                    created_at, expires_at, plan_json, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    plan.plan_id,
                    plan.kind,
                    plan.state,
                    plan.document_sha256,
                    plan.store_identity,
                    plan.created_at.isoformat(),
                    plan.expires_at.isoformat(),
                    plan.model_dump_json(),
                ),
            )

    def get(self, plan_id: str) -> ExecutionPlan:
        with self._connect() as connection:
            row = connection.execute("SELECT plan_json FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            raise PlanStateError("unknown plan")
        return ExecutionPlan.model_validate_json(row["plan_json"])

    def begin_apply(self, plan_id: str) -> ExecutionPlan:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state, plan_json FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if row is None:
                raise PlanStateError("unknown plan")
            plan = ExecutionPlan.model_validate_json(row["plan_json"])
            current_state = row["state"]
            if current_state == "planned" and plan.expires_at < self._clock():
                expired = plan.model_copy(update={"state": "expired"})
                self._set_plan(connection, expired)
                connection.commit()
                raise PlanStateError("plan expired")
            if current_state in {"failed", "partial"}:
                latest_rows = connection.execute(
                    """
                    SELECT operation_id, state FROM operation_events
                    WHERE plan_id = ? ORDER BY id DESC
                    """,
                    (plan_id,),
                ).fetchall()
                latest: dict[str, str] = {}
                for event in latest_rows:
                    latest.setdefault(event["operation_id"], event["state"])
                if "retryable" not in latest.values():
                    raise PlanStateError(self._state_message(current_state))
            elif current_state != "planned":
                raise PlanStateError(self._state_message(current_state))
            applying = plan.model_copy(update={"state": "applying"})
            changed = connection.execute(
                "UPDATE plans SET state = ?, plan_json = ? WHERE plan_id = ? AND state = ?",
                ("applying", applying.model_dump_json(), plan_id, current_state),
            ).rowcount
            if changed != 1:
                raise PlanStateError("plan state changed while applying")
            connection.commit()
            return applying
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def update_operation(self, plan_id: str, operation_id: str, state: str, details: dict[str, Any]) -> None:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM plans WHERE plan_id = ?", (plan_id,)).fetchone() is None:
                raise PlanStateError("unknown plan")
            connection.execute(
                """
                INSERT INTO operation_events (plan_id, operation_id, state, recorded_at, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    operation_id,
                    state,
                    self._clock().isoformat(),
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                ),
            )

    def finish(self, plan_id: str, state: PlanState, result: dict[str, Any]) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT plan_json FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if row is None:
                raise PlanStateError("unknown plan")
            plan = ExecutionPlan.model_validate_json(row["plan_json"]).model_copy(update={"state": state})
            connection.execute(
                "UPDATE plans SET state = ?, plan_json = ?, result_json = ? WHERE plan_id = ?",
                (state, plan.model_dump_json(), json.dumps(result, ensure_ascii=False, sort_keys=True), plan_id),
            )

    def status(self, plan_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state, plan_json, result_json FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if row is None:
                raise PlanStateError("unknown plan")
            plan = ExecutionPlan.model_validate_json(row["plan_json"])
            state = row["state"]
            if state == "planned" and plan.expires_at < self._clock():
                plan = plan.model_copy(update={"state": "expired"})
                self._set_plan(connection, plan)
                state = "expired"
            events = connection.execute(
                """
                SELECT operation_id, state, recorded_at, details_json
                FROM operation_events WHERE plan_id = ? ORDER BY id
                """,
                (plan_id,),
            ).fetchall()
        return {
            **plan.public_view(),
            "state": state,
            "events": [
                {
                    "operation_id": event["operation_id"],
                    "state": event["state"],
                    "recorded_at": event["recorded_at"],
                    "details": json.loads(event["details_json"]),
                }
                for event in events
            ],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    @staticmethod
    def _set_plan(connection: sqlite3.Connection, plan: ExecutionPlan) -> None:
        connection.execute(
            "UPDATE plans SET state = ?, plan_json = ? WHERE plan_id = ?",
            (plan.state, plan.model_dump_json(), plan.plan_id),
        )

    @staticmethod
    def _state_message(state: str) -> str:
        if state == "applying":
            return "plan is already applying"
        if state == "expired":
            return "plan expired"
        return f"plan cannot be applied from state {state}"
