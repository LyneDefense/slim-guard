from __future__ import annotations

import pytest

from slim_guard.harness.manifest import AgentGraphManifest, AgentGraphNodeManifest


def node(role: str) -> AgentGraphNodeManifest:
    return AgentGraphNodeManifest.build(
        role=role,
        model="glm-5.2",
        prompt_version=f"{role}-v1",
        prompt=f"Instructions for {role}",
        output_schema="TurnDirective" if role == "orchestrator" else "AgentOutput",
        allowed_tool_names=("tool_b", "tool_a", "tool_a"),
        privacy_scopes=("scope_b", "scope_a"),
    )


def manifest() -> AgentGraphManifest:
    roles = (
        "orchestrator",
        "dish_recognition",
        "nutrition_retrieval",
        "nutrition_expert",
        "response_style",
        "response_reviewer",
    )
    return AgentGraphManifest.build(
        graph_version="typed-supervisor-v1",
        nodes={role: node(role) for role in reversed(roles)},
        style_profile_version="slimguard_default_v1",
        routing_policy_version="model-directed-code-validated-v1",
        evidence_policy_version="typed-provenance-v1",
        safety_policy_version="health-output-guard-v2",
        business_tool_versions={"record_weight": "v1"},
        code_revision="test",
    )


def test_graph_manifest_is_canonical_and_content_addressed() -> None:
    first = manifest()
    second = manifest()

    assert first.version_id == second.version_id
    assert first.version_id.startswith("graph-")
    assert [role for role, _ in first.nodes] == sorted(role for role, _ in first.nodes)
    assert first.nodes[0][1].allowed_tool_names == ("tool_a", "tool_b")
    assert first.to_json() == second.to_json()


def test_graph_manifest_requires_all_controlled_roles() -> None:
    with pytest.raises(ValueError, match="missing required nodes"):
        AgentGraphManifest.build(
            graph_version="typed-supervisor-v1",
            nodes={"orchestrator": node("orchestrator")},
            style_profile_version="slimguard_default_v1",
            routing_policy_version="routing-v1",
            evidence_policy_version="evidence-v1",
            safety_policy_version="safety-v1",
            code_revision="test",
        )


def test_graph_manifest_rejects_role_key_mismatch() -> None:
    roles = (
        "orchestrator",
        "dish_recognition",
        "nutrition_retrieval",
        "nutrition_expert",
        "response_style",
        "response_reviewer",
    )
    nodes = {role: node(role) for role in roles}
    nodes["response_style"] = node("orchestrator")

    with pytest.raises(ValueError, match="keys must match"):
        AgentGraphManifest.build(
            graph_version="typed-supervisor-v1",
            nodes=nodes,
            style_profile_version="slimguard_default_v1",
            routing_policy_version="routing-v1",
            evidence_policy_version="evidence-v1",
            safety_policy_version="safety-v1",
            code_revision="test",
        )
