from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from slim_guard.agents.contracts import AgentArtifact, AgentInvocation
from slim_guard.orchestration.artifacts import (
    ArtifactAlreadyExists,
    ArtifactIntegrityError,
    ArtifactReferenceError,
    InMemoryArtifactStore,
    verify_artifact,
)


def artifact(
    artifact_id: str,
    *,
    turn_id: str = "turn-1",
    parents: tuple[str, ...] = (),
) -> AgentArtifact:
    return AgentArtifact.create(
        artifact_id=artifact_id,
        turn_id=turn_id,
        producer_role="orchestrator",  # type: ignore[arg-type]
        artifact_type="TurnDirective",
        schema_version="1",
        parent_artifact_ids=parents,
        payload={"artifact_id": artifact_id},
        created_at=datetime.now(UTC),
    )


def invocation(*artifact_ids: str, turn_id: str = "turn-1") -> AgentInvocation:
    return AgentInvocation(
        invocation_id="invocation-1",
        trace_id="trace-1",
        turn_id=turn_id,
        graph_version="typed-supervisor-v1",
        agent_role="response_style",
        agent_version="style-v1",
        input_artifact_ids=artifact_ids,
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        max_model_calls=1,
        max_tool_calls=0,
        max_total_tokens=2000,
    )


def test_ledger_is_append_only_and_preserves_a_snapshot() -> None:
    store = InMemoryArtifactStore()
    original = artifact("artifact-1")
    stored = store.append(original)

    with pytest.raises(ArtifactAlreadyExists):
        store.append(original)

    original.payload["artifact_id"] = "mutated"
    with pytest.raises(ArtifactIntegrityError):
        verify_artifact(original)
    assert store.require("artifact-1").payload == stored.payload


def test_parent_must_already_exist_in_the_same_turn() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(ArtifactReferenceError, match="unknown parent"):
        store.append(artifact("child", parents=("forged",)))

    store.append(artifact("parent", turn_id="turn-other"))
    with pytest.raises(ArtifactReferenceError, match="another turn"):
        store.append(artifact("child", parents=("parent",)))


def test_invocation_cannot_forge_or_cross_turn_artifact_references() -> None:
    store = InMemoryArtifactStore()
    store.append(artifact("artifact-1"))
    assert store.validate_invocation(invocation("artifact-1"))[0].artifact_id == "artifact-1"

    with pytest.raises(ArtifactReferenceError, match="unknown artifact"):
        store.validate_invocation(invocation("forged"))
    with pytest.raises(ArtifactReferenceError, match="another turn"):
        store.validate_invocation(invocation("artifact-1", turn_id="turn-2"))


def test_repair_artifact_retains_lineage() -> None:
    store = InMemoryArtifactStore()
    store.append(artifact("first"))
    store.append(artifact("repair", parents=("first",)))

    assert tuple(item.artifact_id for item in store.lineage("repair")) == (
        "repair",
        "first",
    )
