"""An unchanged style package must not reuse results from retired review rules."""

import copy
import json

import pytest

from slim_guard.agent.assembly.style_preview import build_coach_preview
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.expression_style.package import Guide, content_hash, make_package
from slim_guard.expression_style.review import policy
from slim_guard.expression_style.trainer.client import TrainingClient, TrainingGateway
from slim_guard.expression_style.trainer.contracts import BuildExecutionConfig
from slim_guard.expression_style.trainer.contracts import TestCase as RewriteCase
from slim_guard.expression_style.trainer.evaluation import freeze_execution, preview_case


class MemoryPort:
    def __init__(self):
        self.checkpoints = {}

    async def load(self, key):
        return copy.deepcopy(self.checkpoints.get(key))

    async def save(self, key, value, *, stage, message):
        self.checkpoints[key] = copy.deepcopy(value)

    async def event(self, stage, message, **details):
        pass

    async def record_call(self):
        return {}

    async def record_tokens(self, tokens):
        pass


@pytest.mark.parametrize("component", ["POLICY_VERSION", "INTEGRITY_VERSION"])
def test_review_signature_changes_when_semantic_or_contract_policy_changes(monkeypatch, component):
    current = policy.review_signature("test-model")
    monkeypatch.setattr(policy, component, "retired-number-counter-policy")
    assert policy.review_signature("test-model") != current


async def test_offline_rewrite_does_not_reuse_counter_failure_or_rebuild_style_package():
    package = make_package("doctor", "quantity-cache", "测试风格", Guide(summary="简洁自然"))
    original_package = package.model_dump(mode="json")
    case = RewriteCase(
        id="quantity-case",
        family="synthetic",
        user_input="今天喝水1500毫升",
        source_text="今天喝水1500毫升。",
    )
    rewritten = "今天喝水1500毫升，这1500毫升是今天的饮水量。"
    port = MemoryPort()
    gateway = ScriptedModelGateway(
        [
            ModelResponse(
                message=ModelMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "text": rewritten,
                        }
                    ),
                )
            ),
            ModelResponse(
                message=ModelMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "fidelity_passed": True,
                            "role_passed": True,
                            "expression_passed": True,
                            "issues": [],
                        }
                    ),
                )
            ),
        ]
    )
    training = TrainingGateway(gateway, port, BuildExecutionConfig())
    client = TrainingClient(
        training, "test-model", preview=build_coach_preview(training, "test-model", 120)
    )
    legacy_key = "rewrite:" + content_hash(
        {
            "package": package.package_hash,
            "case": case.model_dump(mode="json"),
            "model": client.model,
            "review": "retired-number-counter-policy",
            "rewrite": "response-style-v9-coach-scope",
            "temperature": 0,
        }
    )
    legacy_failure = {
        "passed": False,
        "fallback": True,
        "failure_code": "style_integrity_invalid",
        "checks": [{"issues": ["number_changed"]}],
    }
    port.checkpoints[legacy_key] = copy.deepcopy(legacy_failure)
    result = await preview_case(client, package, case, "preview")
    assert result["passed"] and not result["fallback"]
    assert result["text"] == rewritten
    assert result["execution"]["review"] == policy.review_signature(client.model)
    assert result["execution"]["coach"] == client.preview.signature
    assert result["checks"][0]["review"]["fidelity_passed"]
    assert [r.output_schema_name for r in gateway.requests] == ["CoachReply", "ReplyCheck"]
    assert port.checkpoints[legacy_key] == legacy_failure
    assert package.model_dump(mode="json") == original_package
    assert await preview_case(client, package, case, "preview") == result
    assert len(gateway.requests) == 2


async def test_resume_does_not_silently_switch_generation_or_review_policy():
    port = MemoryPort()
    gateway = ScriptedModelGateway([])
    training = TrainingGateway(gateway, port, BuildExecutionConfig())
    client = TrainingClient(
        training, "test-model", preview=build_coach_preview(training, "test-model", 120)
    )
    frozen = await freeze_execution(client)
    assert await freeze_execution(client) == frozen
    port.checkpoints["coach_execution_signature"] = "retired-direct-generation"
    with pytest.raises(ValueError, match="新建构建"):
        await freeze_execution(client)
    assert gateway.requests == []
