from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any


def _canonical_entries(values: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (key, json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
        for key, value in sorted(values.items())
    )


def _version_entries(values: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(values.items()))


@dataclass(frozen=True, slots=True)
class AgentGraphNodeManifest:
    """Frozen model, prompt, schema and grants for one workflow role."""

    role: str
    model: str
    prompt_version: str
    prompt_sha256: str
    output_schema: str
    output_schema_version: str
    allowed_tool_names: tuple[str, ...]
    privacy_scopes: tuple[str, ...]
    max_model_calls: int
    max_tool_calls: int
    max_total_tokens: int

    @classmethod
    def build(
        cls,
        *,
        role: str,
        model: str,
        prompt_version: str,
        prompt: str,
        output_schema: str,
        output_schema_version: str = "1",
        allowed_tool_names: Sequence[str] = (),
        privacy_scopes: Sequence[str] = (),
        max_model_calls: int = 2,
        max_tool_calls: int = 4,
        max_total_tokens: int = 4096,
    ) -> AgentGraphNodeManifest:
        text_fields = {
            "role": role,
            "model": model,
            "prompt_version": prompt_version,
            "output_schema": output_schema,
            "output_schema_version": output_schema_version,
        }
        if any(not value.strip() for value in text_fields.values()):
            raise ValueError("Graph node manifest text fields cannot be empty")
        if max_model_calls < 1 or max_total_tokens < 1 or max_tool_calls < 0:
            raise ValueError(
                "Graph node manifest requires positive model/token limits and a "
                "non-negative tool limit"
            )
        return cls(
            role=role,
            model=model,
            prompt_version=prompt_version,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            output_schema=output_schema,
            output_schema_version=output_schema_version,
            allowed_tool_names=tuple(sorted(set(allowed_tool_names))),
            privacy_scopes=tuple(sorted(set(privacy_scopes))),
            max_model_calls=max_model_calls,
            max_tool_calls=max_tool_calls,
            max_total_tokens=max_total_tokens,
        )


@dataclass(frozen=True, slots=True)
class AgentGraphManifest:
    """Immutable version of a complete typed multi-agent workflow."""

    schema_version: str
    graph_version: str
    nodes: tuple[tuple[str, AgentGraphNodeManifest], ...]
    style_profile_version: str
    routing_policy_version: str
    evidence_policy_version: str
    safety_policy_version: str
    business_tool_versions: tuple[tuple[str, str], ...]
    nutrition_tool_versions: tuple[tuple[str, str], ...]
    style_tool_versions: tuple[tuple[str, str], ...]
    code_revision: str

    @classmethod
    def build(
        cls,
        *,
        graph_version: str,
        nodes: Mapping[str, AgentGraphNodeManifest],
        style_profile_version: str,
        routing_policy_version: str,
        evidence_policy_version: str,
        safety_policy_version: str,
        business_tool_versions: Mapping[str, str] | None = None,
        nutrition_tool_versions: Mapping[str, str] | None = None,
        style_tool_versions: Mapping[str, str] | None = None,
        code_revision: str,
    ) -> AgentGraphManifest:
        required = {
            "orchestrator",
            "nutrition_expert",
            "response_style",
            "response_reviewer",
        }
        missing = required.difference(nodes)
        if missing:
            raise ValueError(f"Graph manifest is missing required nodes: {sorted(missing)}")
        mismatched = [key for key, node in nodes.items() if key != node.role]
        if mismatched:
            raise ValueError(f"Graph node keys must match node roles: {sorted(mismatched)}")
        text_fields = (
            graph_version,
            style_profile_version,
            routing_policy_version,
            evidence_policy_version,
            safety_policy_version,
            code_revision,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("Graph manifest version fields cannot be empty")
        return cls(
            schema_version="1",
            graph_version=graph_version,
            nodes=tuple(sorted(nodes.items())),
            style_profile_version=style_profile_version,
            routing_policy_version=routing_policy_version,
            evidence_policy_version=evidence_policy_version,
            safety_policy_version=safety_policy_version,
            business_tool_versions=_version_entries(business_tool_versions or {}),
            nutrition_tool_versions=_version_entries(nutrition_tool_versions or {}),
            style_tool_versions=_version_entries(style_tool_versions or {}),
            code_revision=code_revision,
        )

    @property
    def version_id(self) -> str:
        digest = hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()
        return f"graph-{digest[:24]}"

    def to_json(self) -> str:
        return json.dumps(
            asdict(self),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class AgentManifest:
    """Immutable snapshot of every component that can change agent behaviour."""

    schema_version: str
    model_provider: str
    text_model: str
    vision_model: str
    model_parameters: tuple[tuple[str, str], ...]
    system_prompt_version: str
    system_prompt_sha256: str
    skill_versions: tuple[tuple[str, str], ...]
    tool_versions: tuple[tuple[str, str], ...]
    context_policy_version: str
    memory_policy_version: str
    compaction_policy_version: str
    safety_policy_version: str
    code_revision: str

    @classmethod
    def build(
        cls,
        *,
        model_provider: str,
        text_model: str,
        vision_model: str,
        model_parameters: Mapping[str, Any],
        system_prompt_version: str,
        system_prompt: str,
        skill_versions: Mapping[str, str] | None = None,
        tool_versions: Mapping[str, str] | None = None,
        context_policy_version: str,
        memory_policy_version: str,
        compaction_policy_version: str,
        safety_policy_version: str,
        code_revision: str,
    ) -> AgentManifest:
        return cls(
            schema_version="1",
            model_provider=model_provider,
            text_model=text_model,
            vision_model=vision_model,
            model_parameters=_canonical_entries(model_parameters),
            system_prompt_version=system_prompt_version,
            system_prompt_sha256=hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
            skill_versions=_version_entries(skill_versions or {}),
            tool_versions=_version_entries(tool_versions or {}),
            context_policy_version=context_policy_version,
            memory_policy_version=memory_policy_version,
            compaction_policy_version=compaction_policy_version,
            safety_policy_version=safety_policy_version,
            code_revision=code_revision,
        )

    @property
    def version_id(self) -> str:
        digest = hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()
        return f"agent-{digest[:24]}"

    def to_json(self) -> str:
        """Return the canonical representation used for persistence and hashing."""

        return json.dumps(
            asdict(self),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def model_parameters_dict(self) -> dict[str, Any]:
        return {key: json.loads(value) for key, value in self.model_parameters}
