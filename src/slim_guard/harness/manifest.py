from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
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
        canonical = json.dumps(
            asdict(self),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"agent-{digest[:24]}"
