"""Stable envelopes shared by the Turn and Invocation runtimes."""

from slim_guard.runtime.contracts.artifact import (
    AgentArtifact,
    Artifact,
    ArtifactProducerRole,
    canonical_payload_bytes,
    payload_sha256,
)
from slim_guard.runtime.contracts.base import ContractModel, validate_contract
from slim_guard.runtime.contracts.invocation import (
    AgentInvocation,
    AgentResult,
    AgentRole,
    Invocation,
    InvocationStatus,
)

__all__ = [
    "AgentArtifact",
    "AgentInvocation",
    "AgentResult",
    "AgentRole",
    "Artifact",
    "ArtifactProducerRole",
    "ContractModel",
    "Invocation",
    "InvocationStatus",
    "canonical_payload_bytes",
    "payload_sha256",
    "validate_contract",
]
