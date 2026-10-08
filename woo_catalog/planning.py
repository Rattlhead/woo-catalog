"""Semantic serialization and durable plan contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from secrets import token_urlsafe
from typing import Any, Literal

from pydantic import BaseModel, Field


def canonical_document(model: BaseModel) -> dict[str, Any]:
    """Serialize only explicitly supplied fields while retaining explicit nulls."""

    return model.model_dump(mode="json", exclude_unset=True, exclude_none=False)


def stable_hash(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    """Hash the semantic JSON form used for user confirmation and preconditions."""

    raw = canonical_document(value) if isinstance(value, BaseModel) else value
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


PlanKind = Literal["catalog", "settings", "delete"]
PlanState = Literal["planned", "applying", "applied", "partial", "conflict", "uncertain", "failed", "expired"]
OperationState = Literal[
    "pending",
    "verified",
    "conflict",
    "blocked_dependency",
    "failed",
    "failed_verification",
    "uncertain",
    "retryable",
]


class PlanError(ValueError):
    """Base exception for invalid plan operations."""


class PlanStateError(PlanError):
    """A plan cannot transition from its current state."""


class PlanLimitError(PlanError):
    """The requested plan exceeds the safe default operation limit."""


class ConfirmationError(PlanError):
    """One or more risk confirmations are missing."""


class Confirmations(BaseModel):
    confirmed: bool = False
    confirm_publish: bool = False
    confirm_settings: bool = False
    confirm_permanent: bool = False
    confirm_large_plan: bool = False

    def enabled_names(self) -> set[str]:
        return {name for name in self.__class__.model_fields if getattr(self, name)}


class Precondition(BaseModel):
    exists: bool
    identity: str
    relevant_sha256: str
    resource_id: int | None = None


class PlannedOperation(BaseModel):
    operation_id: str
    resource: str
    identity: str
    action: Literal["create", "update", "delete", "no_op", "skipped_new", "error"]
    path: str | None = None
    payload: dict[str, Any] | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    changes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    precondition: Precondition | None = None
    depends_on: list[str] = Field(default_factory=list)
    risks: set[str] = Field(default_factory=set)
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class OperationResult(BaseModel):
    operation_id: str
    state: OperationState = "pending"
    details: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    plan_id: str
    state: Literal["applied", "partial", "conflict", "uncertain", "failed"]
    operations: list[OperationResult] = Field(default_factory=list)


class ExecutionPlan(BaseModel):
    plan_id: str
    kind: PlanKind
    state: PlanState = "planned"
    document_sha256: str
    store_identity: str
    created_at: datetime
    expires_at: datetime
    operations: list[PlannedOperation]
    required_confirmations: set[str] = Field(default_factory=lambda: {"confirmed"})
    warnings: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        kind: PlanKind,
        document: BaseModel | dict[str, Any],
        store_identity: str,
        operations: list[PlannedOperation],
        required_confirmations: set[str] | None = None,
        warnings: list[str] | None = None,
        created_at: datetime | None = None,
        ttl: timedelta = timedelta(minutes=15),
    ) -> "ExecutionPlan":
        created = created_at or datetime.now(UTC)
        return cls(
            plan_id=token_urlsafe(24),
            kind=kind,
            document_sha256=stable_hash(document),
            store_identity=store_identity,
            created_at=created,
            expires_at=created + ttl,
            operations=operations,
            required_confirmations=required_confirmations or {"confirmed"},
            warnings=warnings or [],
        )

    def public_view(self) -> dict[str, Any]:
        executable = [item for item in self.operations if item.action in {"create", "update", "delete"}]
        return {
            "plan_id": self.plan_id,
            "kind": self.kind,
            "state": self.state,
            "document_sha256": self.document_sha256,
            "expires_at": self.expires_at.isoformat(),
            "required_confirmations": sorted(self.required_confirmations),
            "warnings": self.warnings,
            "summary": {
                action: sum(operation.action == action for operation in self.operations)
                for action in ("create", "update", "delete", "no_op", "skipped_new", "error")
            },
            "executable_operations": len(executable),
            "operations": [item.model_dump(mode="json", exclude_none=True) for item in self.operations],
        }
