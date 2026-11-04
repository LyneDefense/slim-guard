"""Coach context assembly and published dialogue-package compatibility."""

import json

import pytest
from pydantic import ValidationError

from slim_guard.agents.coach.context import coach_messages, style_reference
from slim_guard.expression_style.contracts import (
    SLIMGUARD_DEFAULT_V1,
    StyleExample,
    StyleProfileSnapshot,
)
from slim_guard.expression_style.dialogue import DialogueMessage
from slim_guard.expression_style.package import (
    Guide,
    StylePackage,
    canonical,
    content_hash,
)
from slim_guard.expression_style.trainer.contracts import Material


def legacy_package():
    guide = Guide(summary="简短克制")
    values = {
        "schema_version": "1",
        "compiler_version": "fixed-style-v1",
        "style_id": "doctor",
        "version_id": "legacy",
        "display_name": "医生",
        "guide": guide.model_dump(mode="json"),
        "examples": [],
        "compiled_prompt": canonical(
            {"summary": guide.summary, "rules": [], "prohibited_phrases": [], "fixed_examples": []}
        ),
    }
    return {**values, "package_hash": content_hash(values)}


def test_published_legacy_package_keeps_original_compilation_and_hash():
    legacy = legacy_package()
    legacy["examples"] = [
        {
            "id": "e",
            "revision": 1,
            "original_response": "旧长回答",
            "desired_response": "短回答",
            "rule_ids": ["r"],
        }
    ]
    legacy["guide"]["rules"] = [
        {
            "rule_id": "r",
            "text": "简短",
            "boundary": "不省必要风险",
            "evidence": [{"example_id": "e", "revision": 1, "quote": "短回答"}],
            "counterexample_ids": [],
        }
    ]
    legacy["compiled_prompt"] = canonical(
        {
            "summary": "简短克制",
            "rules": [{"text": "简短", "boundary": "不省必要风险"}],
            "prohibited_phrases": [],
            "fixed_examples": [{"original": "旧长回答", "rewritten": "短回答"}],
        }
    )
    legacy["package_hash"] = content_hash({k: v for k, v in legacy.items() if k != "package_hash"})
    parsed = StylePackage.model_validate(legacy)
    assert parsed.compiled_prompt == legacy["compiled_prompt"]
    assert parsed.package_hash == legacy["package_hash"]
    assert StylePackage.model_validate(parsed.model_dump(mode="json")) == parsed
    card = style_reference(parsed.runtime_snapshot())
    assert "旧长回答" not in canonical(card)
    assert card["dialogue_examples"][0]["context_missing"] is True
    tampered = parsed.model_dump(mode="json")
    tampered["examples"][0]["user_input"] = "伪造旧版没有的上下文"
    with pytest.raises(ValidationError):
        StylePackage.model_validate(tampered)


def test_dialogue_examples_and_current_evidence_are_distinct_and_not_duplicated():
    example = StyleExample(
        example_id="e",
        style_profile_version=SLIMGUARD_DEFAULT_V1.version,
        original_response="不要带入模型的长反例",
        text="那就休息一天。",
        user_input="我不想运动",
        dialogue=(
            DialogueMessage(role="user", content="我不想运动"),
            DialogueMessage(role="assistant", content="那就休息一天。"),
        ),
    )
    style = StyleProfileSnapshot(profile=SLIMGUARD_DEFAULT_V1, examples=(example,))
    messages = coach_messages(prompt="系统职责", style=style, payload={"evidence": "今天想游泳"})
    assert len(messages) == 3
    card, factual = (json.loads(m.content) for m in messages[1:])
    assert card["type"] == "style_reference_only" and factual["type"] == "current_turn_evidence"
    assert "今天想游泳" not in canonical(card)
    assert "我不想运动" not in canonical(factual)
    assert "不要带入模型的长反例" not in canonical([m.content for m in messages])


@pytest.mark.parametrize(
    "history",
    [
        [{"role": "assistant", "content": "乱序"}],
        [{"role": "user", "content": "未完成"}],
        [{"role": "system", "content": "越权"}, {"role": "assistant", "content": "回答"}],
        [{"role": "user", "content": " "}, {"role": "assistant", "content": "回答"}],
    ],
)
def test_sample_history_rejects_incomplete_or_instruction_roles(history):
    with pytest.raises(ValidationError):
        Material(
            id="e", revision=1, user_input="当前消息", original_response="旧回答", history=history
        )
