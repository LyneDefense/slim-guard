from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from slim_guard.harness.manifest import AgentManifest


def build_manifest(**overrides: object) -> AgentManifest:
    values: dict[str, object] = {
        "model_provider": "zhipu",
        "text_model": "glm-5.2",
        "vision_model": "glm-5v-turbo",
        "model_parameters": {
            "thinking": {"type": "disabled"},
            "max_output_tokens": 1024,
        },
        "system_prompt_version": "legacy-v1",
        "system_prompt": "You are SlimGuard.",
        "skill_versions": {},
        "tool_versions": {},
        "context_policy_version": "single-turn-v1",
        "memory_policy_version": "none-v1",
        "compaction_policy_version": "none-v1",
        "safety_policy_version": "legacy-v1",
        "code_revision": "test-revision",
    }
    values.update(overrides)
    return AgentManifest.build(**values)  # type: ignore[arg-type]


def test_manifest_version_is_stable_across_mapping_order() -> None:
    first = build_manifest(
        model_parameters={
            "thinking": {"type": "disabled"},
            "max_output_tokens": 1024,
        }
    )
    second = build_manifest(
        model_parameters={
            "max_output_tokens": 1024,
            "thinking": {"type": "disabled"},
        }
    )

    assert first.version_id == second.version_id
    assert first.version_id.startswith("agent-")


def test_manifest_version_changes_when_prompt_content_changes() -> None:
    baseline = build_manifest(system_prompt="You are SlimGuard.")
    candidate = build_manifest(system_prompt="You are a more concise SlimGuard.")

    assert baseline.system_prompt_sha256 != candidate.system_prompt_sha256
    assert baseline.version_id != candidate.version_id


def test_manifest_keeps_component_versions_human_readable() -> None:
    manifest = build_manifest(
        skill_versions={"weight_checkin": "v2"},
        tool_versions={"record_weight": "v3"},
    )

    assert manifest.skill_versions == (("weight_checkin", "v2"),)
    assert manifest.tool_versions == (("record_weight", "v3"),)


def test_manifest_is_immutable() -> None:
    manifest = build_manifest()

    with pytest.raises(FrozenInstanceError):
        manifest.text_model = "another-model"  # type: ignore[misc]
