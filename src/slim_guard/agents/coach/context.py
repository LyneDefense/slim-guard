"""Build distinct style-reference and factual-context regions without duplicating drafts."""

import json
from typing import Any

from slim_guard.agent_models.gateway import ModelMessage
from slim_guard.expression_style.contracts import StyleProfileSnapshot

CONTEXT_VERSION = "coach-context-v2-dialogue"


def style_reference(style: StyleProfileSnapshot) -> dict[str, Any]:
    profile = style.profile
    personality: dict[str, Any] = {
        "summary": profile.description,
        "rules": [{"text": text} for text in profile.tone_rules],
        "prohibited_phrases": profile.prohibited_phrases,
    }
    if style.compiled_prompt:
        try:
            compiled = json.loads(style.compiled_prompt)
        except ValueError:
            compiled = None
        if isinstance(compiled, dict):
            # Compiled v1 and v2 guides both carry rule boundaries. Only copy
            # expression fields: never the old long original/rewritten pairs.
            for key in personality:
                if key in compiled:
                    personality[key] = compiled[key]
    examples = []
    for example in style.examples:
        messages = [m.model_dump(mode="json") for m in example.dialogue]
        if not messages and example.user_input:
            messages = [
                {"role": "user", "content": example.user_input},
                {"role": "assistant", "content": example.text},
            ]
        examples.append(
            {"example_id": example.example_id, "messages": messages}
            if messages
            else {
                "example_id": example.example_id,
                "utterance": example.text,
                "context_missing": True,
            }
        )
    return {
        "type": "style_reference_only",
        "profile_id": profile.profile_id,
        "version": profile.version,
        "package_hash": style.package_hash,
        "personality": personality,
        "dialogue_examples": examples,
    }


def coach_messages(
    *, prompt: str, style: StyleProfileSnapshot, payload: dict[str, object]
) -> tuple[ModelMessage, ...]:
    return (
        ModelMessage(role="system", content=prompt),
        ModelMessage(role="user", content=json.dumps(style_reference(style), ensure_ascii=False)),
        ModelMessage(
            role="user",
            content=json.dumps({"type": "current_turn_evidence", **payload}, ensure_ascii=False),
        ),
    )
