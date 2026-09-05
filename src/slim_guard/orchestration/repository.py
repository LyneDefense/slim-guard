"""Durable, auditable storage for multi-agent invocations and artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.agents.contracts import AgentArtifact, AgentInvocation, AgentResult
from slim_guard.db.models import (
    AgentArtifactParentRecord,
    AgentArtifactRecord,
    AgentInvocationRecord,
    AgentTurnRecord,
    utc_now,
)
from slim_guard.db.session import Database
from slim_guard.orchestration.artifacts import (
    ArtifactAlreadyExists,
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactReferenceError,
    verify_artifact,
)


class InvocationStoreError(RuntimeError):
    """Base class for classifiable invocation persistence failures."""


class InvocationAlreadyExists(InvocationStoreError):
    pass


class InvocationNotFound(InvocationStoreError):
    pass


class InvocationReferenceError(InvocationStoreError):
    pass


class InvocationStateConflict(InvocationStoreError):
    pass


class InvocationBudgetExceeded(InvocationStoreError):
    pass


@dataclass(frozen=True, slots=True)
class StoredInvocation:
    invocation_id: str
    trace_id: str
    turn_id: str
    graph_version: str
    agent_role: str
    agent_version: str
    attempt: int
    caller: str
    parent_invocation_id: str | None
    input_artifact_ids: tuple[str, ...]
    input_schema: str | None
    input_schema_version: str
    allowed_tools: tuple[str, ...]
    privacy_scopes: tuple[str, ...]
    deadline_at: datetime
    max_model_calls: int
    max_tool_calls: int
    max_total_tokens: int
    reason_summary: str | None
    status: str
    output_schema: str | None
    output_schema_version: str | None
    output_artifact_id: str | None
    tool_receipt_ids: tuple[str, ...]
    model_call_count: int
    tool_call_count: int
    total_token_count: int
    failure_code: str | None
    started_at: datetime
    completed_at: datetime | None


class OrchestrationRepository:
    """Persists coordinator state and verifies every artifact reference and digest."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def start_invocation(
        self,
        invocation: AgentInvocation,
        *,
        reason_summary: str | None = None,
        started_at: datetime | None = None,
    ) -> StoredInvocation:
        started_at = started_at or utc_now()
        self._require_aware(started_at, field="started_at")
        row = AgentInvocationRecord(
            id=invocation.invocation_id,
            trace_id=invocation.trace_id,
            turn_id=invocation.turn_id,
            graph_version=invocation.graph_version,
            agent_role=invocation.agent_role.value,
            agent_version=invocation.agent_version,
            attempt=invocation.attempt,
            caller=invocation.caller,
            parent_invocation_id=invocation.parent_invocation_id,
            input_artifact_ids_json=self._json_array(invocation.input_artifact_ids),
            input_schema=invocation.input_schema,
            input_schema_version=invocation.input_schema_version,
            allowed_tools_json=self._json_array(invocation.allowed_tools),
            privacy_scopes_json=self._json_array(invocation.privacy_scopes),
            deadline_at=invocation.deadline_at,
            max_model_calls=invocation.max_model_calls,
            max_tool_calls=invocation.max_tool_calls,
            max_total_tokens=invocation.max_total_tokens,
            input_payload_json=self._json_object(invocation.payload),
            reason_summary=self._reason_summary(reason_summary),
            status="started",
            started_at=started_at,
        )
        try:
            async with self.database.session() as session, session.begin():
                if await session.get(AgentInvocationRecord, invocation.invocation_id):
                    raise InvocationAlreadyExists(
                        f"Invocation {invocation.invocation_id} already exists"
                    )
                if await session.get(AgentTurnRecord, invocation.turn_id) is None:
                    raise InvocationReferenceError(
                        f"Invocation references unknown turn {invocation.turn_id}"
                    )
                await self._validate_parent_invocation(session, invocation)
                await self._validate_input_artifacts(session, invocation)
                session.add(row)
                await session.flush()
        except IntegrityError as error:
            raise InvocationStateConflict(
                "Invocation identity, role attempt, or parent reference conflicts "
                "with persisted history"
            ) from error
        return self._invocation_ref(row)

    async def append_invocation(
        self,
        invocation: AgentInvocation,
        *,
        reason_summary: str | None = None,
        started_at: datetime | None = None,
    ) -> StoredInvocation:
        return await self.start_invocation(
            invocation,
            reason_summary=reason_summary,
            started_at=started_at,
        )

    async def complete_invocation(
        self,
        result: AgentResult,
        *,
        completed_at: datetime | None = None,
    ) -> StoredInvocation:
        completed_at = completed_at or utc_now()
        self._require_aware(completed_at, field="completed_at")
        async with self.database.session() as session, session.begin():
            row = await session.get(AgentInvocationRecord, result.invocation_id)
            if row is None:
                raise InvocationNotFound(f"Invocation {result.invocation_id} does not exist")
            self._validate_result_budgets(row, result)
            await self._validate_output_artifact(session, row, result)
            if row.status != "started":
                if self._result_matches(row, result):
                    return self._invocation_ref(row)
                raise InvocationStateConflict(
                    f"Invocation {result.invocation_id} is already {row.status}"
                )
            if self._aware(completed_at) < self._aware(row.started_at):
                raise ValueError("completed_at cannot be earlier than started_at")

            row.status = result.status.value
            row.output_schema = result.output_schema
            row.output_schema_version = result.output_schema_version
            row.output_artifact_id = result.artifact_id
            row.tool_receipt_ids_json = self._json_array(result.tool_receipt_ids)
            row.model_call_count = result.model_call_count
            row.tool_call_count = result.tool_call_count
            row.total_token_count = result.token_usage
            row.failure_code = result.failure_code
            row.completed_at = completed_at
            await session.flush()
            return self._invocation_ref(row)

    async def get_invocation(self, invocation_id: str) -> StoredInvocation | None:
        async with self.database.session() as session:
            row = await session.get(AgentInvocationRecord, invocation_id)
            return self._invocation_ref(row) if row is not None else None

    async def list_invocations(
        self,
        *,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[StoredInvocation, ...]:
        if turn_id is None and trace_id is None:
            raise ValueError("turn_id or trace_id is required")
        statement = select(AgentInvocationRecord)
        if turn_id is not None:
            statement = statement.where(AgentInvocationRecord.turn_id == turn_id)
        if trace_id is not None:
            statement = statement.where(AgentInvocationRecord.trace_id == trace_id)
        statement = statement.order_by(
            AgentInvocationRecord.started_at,
            AgentInvocationRecord.id,
        )
        async with self.database.session() as session:
            rows = tuple(await session.scalars(statement))
            return tuple(self._invocation_ref(row) for row in rows)

    async def list_turn_invocations(self, turn_id: str) -> tuple[StoredInvocation, ...]:
        return await self.list_invocations(turn_id=turn_id)

    async def append_artifact(
        self,
        artifact: AgentArtifact,
        *,
        invocation_id: str | None = None,
    ) -> AgentArtifact:
        verify_artifact(artifact)
        row = AgentArtifactRecord(
            id=artifact.artifact_id,
            turn_id=artifact.turn_id,
            invocation_id=invocation_id,
            producer_role=artifact.producer_role.value,
            artifact_type=artifact.artifact_type,
            schema_version=artifact.schema_version,
            parent_artifact_ids_json=self._json_array(artifact.parent_artifact_ids),
            payload_sha256=artifact.payload_sha256,
            payload_json=self._json_object(artifact.payload),
            created_at=artifact.created_at,
        )
        try:
            async with self.database.session() as session, session.begin():
                if await session.get(AgentArtifactRecord, artifact.artifact_id):
                    raise ArtifactAlreadyExists(
                        f"Artifact {artifact.artifact_id} already exists and cannot be overwritten"
                    )
                if await session.get(AgentTurnRecord, artifact.turn_id) is None:
                    raise ArtifactReferenceError(
                        f"Artifact references unknown turn {artifact.turn_id}"
                    )
                if invocation_id is not None:
                    invocation = await session.get(AgentInvocationRecord, invocation_id)
                    if invocation is None:
                        raise ArtifactReferenceError(
                            f"Artifact references unknown invocation {invocation_id}"
                        )
                    if invocation.turn_id != artifact.turn_id:
                        raise ArtifactReferenceError(
                            f"Artifact invocation {invocation_id} belongs to another turn"
                        )
                await self._validate_artifact_parents(session, artifact)
                session.add(row)
                await session.flush()
                session.add_all(
                    AgentArtifactParentRecord(
                        artifact_id=artifact.artifact_id,
                        parent_artifact_id=parent_id,
                        turn_id=artifact.turn_id,
                    )
                    for parent_id in artifact.parent_artifact_ids
                )
                await session.flush()
        except IntegrityError as error:
            raise ArtifactReferenceError(
                "Artifact identity or lineage conflicts with persisted history"
            ) from error
        return self._snapshot(artifact)

    async def append(
        self,
        artifact: AgentArtifact,
        *,
        invocation_id: str | None = None,
    ) -> AgentArtifact:
        return await self.append_artifact(
            artifact,
            invocation_id=invocation_id,
        )

    async def get_artifact(self, artifact_id: str) -> AgentArtifact | None:
        async with self.database.session() as session:
            row = await session.get(AgentArtifactRecord, artifact_id)
            if row is None:
                return None
            return await self._artifact_from_row(session, row)

    async def get(self, artifact_id: str) -> AgentArtifact | None:
        return await self.get_artifact(artifact_id)

    async def require_artifact(self, artifact_id: str) -> AgentArtifact:
        artifact = await self.get_artifact(artifact_id)
        if artifact is None:
            raise ArtifactNotFound(f"Artifact {artifact_id} does not exist")
        return artifact

    async def require(self, artifact_id: str) -> AgentArtifact:
        return await self.require_artifact(artifact_id)

    async def list_artifacts(self, turn_id: str) -> tuple[AgentArtifact, ...]:
        async with self.database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(AgentArtifactRecord)
                    .where(AgentArtifactRecord.turn_id == turn_id)
                    .order_by(
                        AgentArtifactRecord.created_at,
                        AgentArtifactRecord.id,
                    )
                )
            )
            artifacts = tuple([await self._artifact_from_row(session, row) for row in rows])
            return self._topological_artifacts(artifacts)

    async def list_turn(self, turn_id: str) -> tuple[AgentArtifact, ...]:
        return await self.list_artifacts(turn_id)

    async def validate_invocation(self, invocation: AgentInvocation) -> tuple[AgentArtifact, ...]:
        async with self.database.session() as session:
            return await self._validate_input_artifacts(session, invocation)

    async def lineage(self, artifact_id: str) -> tuple[AgentArtifact, ...]:
        async with self.database.session() as session:
            result: list[AgentArtifact] = []
            pending = [artifact_id]
            seen: set[str] = set()
            while pending:
                current_id = pending.pop()
                if current_id in seen:
                    continue
                row = await session.get(AgentArtifactRecord, current_id)
                if row is None:
                    raise ArtifactNotFound(f"Artifact {current_id} does not exist")
                artifact = await self._artifact_from_row(session, row)
                seen.add(current_id)
                result.append(artifact)
                pending.extend(reversed(artifact.parent_artifact_ids))
            return tuple(result)

    @staticmethod
    async def _validate_parent_invocation(
        session: AsyncSession,
        invocation: AgentInvocation,
    ) -> None:
        if invocation.parent_invocation_id is None:
            return
        parent = await session.get(AgentInvocationRecord, invocation.parent_invocation_id)
        if parent is None:
            raise InvocationReferenceError(
                f"Invocation references unknown parent {invocation.parent_invocation_id}"
            )
        if parent.turn_id != invocation.turn_id:
            raise InvocationReferenceError(f"Invocation parent {parent.id} belongs to another turn")
        if parent.trace_id != invocation.trace_id:
            raise InvocationReferenceError(
                f"Invocation parent {parent.id} belongs to another trace"
            )

    async def _validate_input_artifacts(
        self,
        session: AsyncSession,
        invocation: AgentInvocation,
    ) -> tuple[AgentArtifact, ...]:
        if not invocation.input_artifact_ids:
            return ()
        rows = tuple(
            await session.scalars(
                select(AgentArtifactRecord).where(
                    AgentArtifactRecord.id.in_(invocation.input_artifact_ids)
                )
            )
        )
        by_id = {row.id: row for row in rows}
        artifacts: list[AgentArtifact] = []
        for artifact_id in invocation.input_artifact_ids:
            row = by_id.get(artifact_id)
            if row is None:
                raise ArtifactReferenceError(
                    f"Invocation references unknown artifact {artifact_id}"
                )
            if row.turn_id != invocation.turn_id:
                raise ArtifactReferenceError(
                    f"Invocation cannot reference artifact {artifact_id} from another turn"
                )
            artifacts.append(await self._artifact_from_row(session, row))
        return tuple(artifacts)

    @staticmethod
    async def _validate_artifact_parents(
        session: AsyncSession,
        artifact: AgentArtifact,
    ) -> None:
        if not artifact.parent_artifact_ids:
            return
        rows = tuple(
            await session.scalars(
                select(AgentArtifactRecord).where(
                    AgentArtifactRecord.id.in_(artifact.parent_artifact_ids)
                )
            )
        )
        by_id = {row.id: row for row in rows}
        for parent_id in artifact.parent_artifact_ids:
            parent = by_id.get(parent_id)
            if parent is None:
                raise ArtifactReferenceError(f"Artifact references unknown parent {parent_id}")
            if parent.turn_id != artifact.turn_id:
                raise ArtifactReferenceError(f"Artifact parent {parent_id} belongs to another turn")

    async def _validate_output_artifact(
        self,
        session: AsyncSession,
        invocation: AgentInvocationRecord,
        result: AgentResult,
    ) -> None:
        if result.artifact_id is None:
            return
        artifact = await session.get(AgentArtifactRecord, result.artifact_id)
        if artifact is None:
            raise InvocationReferenceError(
                f"Invocation result references unknown artifact {result.artifact_id}"
            )
        if artifact.turn_id != invocation.turn_id:
            raise InvocationReferenceError(
                f"Invocation result artifact {result.artifact_id} belongs to another turn"
            )
        await self._artifact_from_row(session, artifact)

    @staticmethod
    def _validate_result_budgets(
        invocation: AgentInvocationRecord,
        result: AgentResult,
    ) -> None:
        exceeded: list[str] = []
        if result.model_call_count > invocation.max_model_calls:
            exceeded.append("model calls")
        if result.tool_call_count > invocation.max_tool_calls:
            exceeded.append("tool calls")
        if result.token_usage > invocation.max_total_tokens:
            exceeded.append("tokens")
        if exceeded:
            raise InvocationBudgetExceeded(
                "Invocation result exceeded its " + ", ".join(exceeded) + " budget"
            )

    @classmethod
    async def _artifact_from_row(
        cls,
        session: AsyncSession,
        row: AgentArtifactRecord,
    ) -> AgentArtifact:
        edge_ids = tuple(
            await session.scalars(
                select(AgentArtifactParentRecord.parent_artifact_id)
                .where(AgentArtifactParentRecord.artifact_id == row.id)
                .order_by(AgentArtifactParentRecord.parent_artifact_id)
            )
        )
        encoded_ids = cls._tuple(cls._json_load(row.parent_artifact_ids_json))
        if set(edge_ids) != set(encoded_ids):
            raise ArtifactIntegrityError(
                f"Stored artifact {row.id} has inconsistent parent lineage"
            )
        try:
            artifact = AgentArtifact(
                artifact_id=row.id,
                turn_id=row.turn_id,
                producer_role=row.producer_role,
                artifact_type=row.artifact_type,
                schema_version=row.schema_version,
                parent_artifact_ids=encoded_ids,
                payload_sha256=row.payload_sha256,
                payload=cls._object(cls._json_load(row.payload_json)),
                created_at=cls._aware(row.created_at),
            )
        except (TypeError, ValueError) as error:
            raise ArtifactIntegrityError(
                f"Stored artifact {row.id} failed integrity validation"
            ) from error
        verify_artifact(artifact)
        return artifact

    @classmethod
    def _invocation_ref(cls, row: AgentInvocationRecord) -> StoredInvocation:
        return StoredInvocation(
            invocation_id=row.id,
            trace_id=row.trace_id,
            turn_id=row.turn_id,
            graph_version=row.graph_version,
            agent_role=row.agent_role,
            agent_version=row.agent_version,
            attempt=row.attempt,
            caller=row.caller,
            parent_invocation_id=row.parent_invocation_id,
            input_artifact_ids=cls._tuple(cls._json_load(row.input_artifact_ids_json)),
            input_schema=row.input_schema,
            input_schema_version=row.input_schema_version,
            allowed_tools=cls._tuple(cls._json_load(row.allowed_tools_json)),
            privacy_scopes=cls._tuple(cls._json_load(row.privacy_scopes_json)),
            deadline_at=cls._aware(row.deadline_at),
            max_model_calls=row.max_model_calls,
            max_tool_calls=row.max_tool_calls,
            max_total_tokens=row.max_total_tokens,
            reason_summary=row.reason_summary,
            status=row.status,
            output_schema=row.output_schema,
            output_schema_version=row.output_schema_version,
            output_artifact_id=row.output_artifact_id,
            tool_receipt_ids=cls._tuple(cls._json_load(row.tool_receipt_ids_json)),
            model_call_count=row.model_call_count,
            tool_call_count=row.tool_call_count,
            total_token_count=row.total_token_count,
            failure_code=row.failure_code,
            started_at=cls._aware(row.started_at),
            completed_at=(cls._aware(row.completed_at) if row.completed_at is not None else None),
        )

    @classmethod
    def _result_matches(
        cls,
        row: AgentInvocationRecord,
        result: AgentResult,
    ) -> bool:
        return (
            row.status == result.status.value
            and row.output_schema == result.output_schema
            and row.output_schema_version == result.output_schema_version
            and row.output_artifact_id == result.artifact_id
            and cls._tuple(cls._json_load(row.tool_receipt_ids_json)) == result.tool_receipt_ids
            and row.model_call_count == result.model_call_count
            and row.tool_call_count == result.tool_call_count
            and row.total_token_count == result.token_usage
            and row.failure_code == result.failure_code
        )

    @staticmethod
    def _reason_summary(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            return None
        return normalized[:280]

    @staticmethod
    def _json_array(values: tuple[str, ...]) -> str:
        return json.dumps(
            list(values),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _json_object(value: dict[str, object]) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Invocation and artifact payloads must be JSON-safe") from error

    @staticmethod
    def _json_load(value: str) -> object:
        try:
            return json.loads(value)
        except (TypeError, ValueError) as error:
            raise ArtifactIntegrityError("Persisted orchestration JSON is invalid") from error

    @staticmethod
    def _tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ArtifactIntegrityError("Persisted orchestration reference list is invalid")
        return tuple(value)

    @staticmethod
    def _object(value: object) -> dict[str, object]:
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise ArtifactIntegrityError("Persisted artifact payload is invalid")
        return value

    @staticmethod
    def _require_aware(value: datetime, *, field: str) -> None:
        if value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _snapshot(artifact: AgentArtifact) -> AgentArtifact:
        return AgentArtifact.model_validate_json(artifact.model_dump_json())

    @staticmethod
    def _topological_artifacts(
        artifacts: tuple[AgentArtifact, ...],
    ) -> tuple[AgentArtifact, ...]:
        by_id = {artifact.artifact_id: artifact for artifact in artifacts}
        ordered: list[AgentArtifact] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(artifact: AgentArtifact) -> None:
            if artifact.artifact_id in visited:
                return
            if artifact.artifact_id in visiting:
                raise ArtifactIntegrityError("Persisted artifact lineage contains a cycle")
            visiting.add(artifact.artifact_id)
            for parent_id in artifact.parent_artifact_ids:
                parent = by_id.get(parent_id)
                if parent is not None:
                    visit(parent)
            visiting.remove(artifact.artifact_id)
            visited.add(artifact.artifact_id)
            ordered.append(artifact)

        for artifact in artifacts:
            visit(artifact)
        return tuple(ordered)


__all__ = [
    "InvocationAlreadyExists",
    "InvocationBudgetExceeded",
    "InvocationNotFound",
    "InvocationReferenceError",
    "InvocationStateConflict",
    "InvocationStoreError",
    "OrchestrationRepository",
    "StoredInvocation",
]
