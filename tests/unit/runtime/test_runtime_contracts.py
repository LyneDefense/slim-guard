from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from slim_guard.agents import contracts as legacy_contracts
from slim_guard.runtime.contracts import (
    AgentArtifact,
    AgentInvocation,
    AgentRole,
    ArtifactProducerRole,
)


def test_legacy_agent_module_reexports_runtime_envelopes() -> None:
    assert legacy_contracts.AgentInvocation is AgentInvocation
    assert legacy_contracts.AgentArtifact is AgentArtifact
    assert legacy_contracts.AgentRole is AgentRole


def test_invocation_contract_supports_core_role_and_rejects_extra_fields() -> None:
    payload = {
        "invocation_id": "inv-1",
        "trace_id": "trace-1",
        "turn_id": "turn-1",
        "graph_version": "core-v2",
        "agent_role": "core",
        "agent_version": "core-v1",
        "deadline_at": datetime(2026, 9, 17, tzinfo=UTC),
        "max_model_calls": 4,
        "max_tool_calls": 8,
        "max_total_tokens": 16_000,
    }

    invocation = AgentInvocation.model_validate(payload)

    assert invocation.agent_role is AgentRole.CORE
    with pytest.raises(ValidationError):
        AgentInvocation.model_validate({**payload, "unexpected": True})


def test_artifact_detects_payload_tampering_after_creation() -> None:
    artifact = AgentArtifact.create(
        artifact_id="artifact-1",
        turn_id="turn-1",
        producer_role=ArtifactProducerRole.CORE,
        artifact_type="response_plan",
        schema_version="1",
        payload={"text": "original"},
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
    )

    artifact.payload["text"] = "changed"

    assert artifact.verify_payload() is False
