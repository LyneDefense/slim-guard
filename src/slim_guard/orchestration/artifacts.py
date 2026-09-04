"""Append-only in-process artifact ledger and reference integrity checks."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

from slim_guard.agents.contracts import (
    AgentArtifact,
    AgentInvocation,
    ArtifactProducerRole,
)


class ArtifactStoreError(RuntimeError):
    """Base class for safe, classifiable artifact-store failures."""


class ArtifactNotFound(ArtifactStoreError):
    pass


class ArtifactAlreadyExists(ArtifactStoreError):
    pass


class ArtifactIntegrityError(ArtifactStoreError):
    pass


class ArtifactReferenceError(ArtifactStoreError):
    pass


class ArtifactStore(Protocol):
    def append(self, artifact: AgentArtifact) -> AgentArtifact: ...

    def get(self, artifact_id: str) -> AgentArtifact | None: ...

    def require(self, artifact_id: str) -> AgentArtifact: ...

    def validate_invocation(self, invocation: AgentInvocation) -> tuple[AgentArtifact, ...]: ...


def verify_artifact(artifact: AgentArtifact) -> None:
    """Recompute the digest so post-validation mutation of nested payloads is caught."""

    if not artifact.verify_payload():
        raise ArtifactIntegrityError(
            f"Artifact {artifact.artifact_id} no longer matches its payload hash"
        )


class InMemoryArtifactStore:
    """Small append-only ledger used before the PostgreSQL repository is introduced.

    Serialized snapshots are stored instead of model instances.  This prevents a caller
    from mutating the nested payload dictionary after append and changing ledger history.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, str] = {}
        self._turn_artifact_ids: dict[str, list[str]] = {}

    def append(self, artifact: AgentArtifact) -> AgentArtifact:
        verify_artifact(artifact)
        if artifact.artifact_id in self._snapshots:
            raise ArtifactAlreadyExists(
                f"Artifact {artifact.artifact_id} already exists and cannot be overwritten"
            )
        self._validate_parent_references(artifact)

        snapshot = artifact.model_dump_json()
        self._snapshots[artifact.artifact_id] = snapshot
        self._turn_artifact_ids.setdefault(artifact.turn_id, []).append(artifact.artifact_id)
        return AgentArtifact.model_validate_json(snapshot)

    def create_and_append(
        self,
        *,
        artifact_id: str,
        turn_id: str,
        producer_role: ArtifactProducerRole,
        artifact_type: str,
        schema_version: str,
        payload: dict[str, Any],
        created_at: datetime,
        parent_artifact_ids: tuple[str, ...] = (),
    ) -> AgentArtifact:
        artifact = AgentArtifact.create(
            artifact_id=artifact_id,
            turn_id=turn_id,
            producer_role=producer_role,
            artifact_type=artifact_type,
            schema_version=schema_version,
            parent_artifact_ids=parent_artifact_ids,
            payload=payload,
            created_at=created_at,
        )
        return self.append(artifact)

    def get(self, artifact_id: str) -> AgentArtifact | None:
        snapshot = self._snapshots.get(artifact_id)
        if snapshot is None:
            return None
        try:
            return AgentArtifact.model_validate_json(snapshot)
        except ValueError as error:
            raise ArtifactIntegrityError(
                f"Stored artifact {artifact_id} failed integrity validation"
            ) from error

    def require(self, artifact_id: str) -> AgentArtifact:
        artifact = self.get(artifact_id)
        if artifact is None:
            raise ArtifactNotFound(f"Artifact {artifact_id} does not exist")
        return artifact

    def contains(self, artifact_id: str) -> bool:
        return artifact_id in self._snapshots

    def list_turn(self, turn_id: str) -> tuple[AgentArtifact, ...]:
        return tuple(self.require(item_id) for item_id in self._turn_artifact_ids.get(turn_id, ()))

    def validate_invocation(self, invocation: AgentInvocation) -> tuple[AgentArtifact, ...]:
        artifacts: list[AgentArtifact] = []
        for artifact_id in invocation.input_artifact_ids:
            artifact = self.get(artifact_id)
            if artifact is None:
                raise ArtifactReferenceError(
                    f"Invocation references unknown artifact {artifact_id}"
                )
            if artifact.turn_id != invocation.turn_id:
                raise ArtifactReferenceError(
                    f"Invocation cannot reference artifact {artifact_id} from another turn"
                )
            verify_artifact(artifact)
            artifacts.append(artifact)
        return tuple(artifacts)

    def lineage(self, artifact_id: str) -> tuple[AgentArtifact, ...]:
        """Return the artifact and all ancestors once, newest first."""

        result: list[AgentArtifact] = []
        pending = [artifact_id]
        seen: set[str] = set()
        while pending:
            current_id = pending.pop()
            if current_id in seen:
                continue
            current = self.require(current_id)
            seen.add(current_id)
            result.append(current)
            pending.extend(reversed(current.parent_artifact_ids))
        return tuple(result)

    def _validate_parent_references(self, artifact: AgentArtifact) -> None:
        for parent_id in artifact.parent_artifact_ids:
            parent = self.get(parent_id)
            if parent is None:
                raise ArtifactReferenceError(
                    f"Artifact references unknown parent {parent_id}"
                )
            if parent.turn_id != artifact.turn_id:
                raise ArtifactReferenceError(
                    f"Artifact parent {parent_id} belongs to another turn"
                )
            verify_artifact(parent)

    def restore(self, artifacts: Iterable[AgentArtifact]) -> None:
        """Append a trusted ordered export while retaining all normal validations."""

        for artifact in artifacts:
            self.append(artifact)


# The ledger name communicates the append-only semantics to coordinator callers.
ArtifactLedger = InMemoryArtifactStore


def validate_invocation_artifacts(
    invocation: AgentInvocation,
    store: ArtifactStore,
) -> tuple[AgentArtifact, ...]:
    return store.validate_invocation(invocation)


__all__ = [
    "ArtifactAlreadyExists",
    "ArtifactIntegrityError",
    "ArtifactLedger",
    "ArtifactNotFound",
    "ArtifactReferenceError",
    "ArtifactStore",
    "ArtifactStoreError",
    "InMemoryArtifactStore",
    "validate_invocation_artifacts",
    "verify_artifact",
]
