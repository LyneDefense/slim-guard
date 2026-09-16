from __future__ import annotations

import json

import pytest

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    ResponseFormat,
    ToolChoice,
)
from slim_guard.nutrition_rag.answerability import (
    ANSWERABILITY_PROMPT_VERSION,
    ANSWERABILITY_PROMPT_VERSION_V1,
    AnswerabilityDocument,
    ModelAnswerabilityGateway,
)
from slim_guard.nutrition_rag.gateways import NutritionModelGatewayError
from slim_guard.nutrition_rag.profiles import ANSWERABILITY_MODE_V1


def _response(payload: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            content=json.dumps(payload, ensure_ascii=False),
        ),
        usage=ModelUsage(input_tokens=31, output_tokens=9, total_tokens=40),
        provider_request_id="answerability-provider-1",
    )


def _documents() -> tuple[AnswerabilityDocument, ...]:
    return (
        AnswerabilityDocument(
            source_key="adult-weight-guide",
            title="成年人减重指南",
            section="饮食搭配",
            content="减重期间应保持食物多样，合理搭配蔬菜和优质蛋白质。",
        ),
        AnswerabilityDocument(
            source_key="food-label-guide",
            title="预包装食品标签指南",
            section="营养成分表",
            content="购买预包装食品时可以查看能量和钠的标示值。",
        ),
    )


async def test_answerability_gateway_returns_only_directly_supported_documents() -> None:
    scripted = ScriptedModelGateway(
        (
            _response(
                {
                    "outcome": "supported",
                    "supported_document_indices": [0],
                    "reason_code": "directly_supported",
                }
            ),
        )
    )
    gateway = ModelAnswerabilityGateway(gateway=scripted, model="test-model")

    result = await gateway.assess(query="减重期间如何搭配食物？", documents=_documents())

    assert result.outcome == "supported"
    assert result.supported_document_indices == (0,)
    assert result.request_id == "answerability-provider-1"
    request = scripted.requests[0]
    assert request.response_format is ResponseFormat.JSON_OBJECT
    assert request.tool_choice is ToolChoice.NONE
    assert request.metadata["prompt_version"] == ANSWERABILITY_PROMPT_VERSION
    assert "主题相关不等于能够回答" in (request.messages[0].content or "")
    assert "控制性措辞已经由系统过滤" in (request.messages[0].content or "")
    submitted = json.loads(request.messages[1].content or "{}")
    assert submitted["question"] == "减重期间如何搭配食物？"
    assert submitted["documents"][0]["source_key"] == "adult-weight-guide"


async def test_answerability_gateway_preserves_the_v1_prompt_for_old_releases() -> None:
    scripted = ScriptedModelGateway(
        (
            _response(
                {
                    "outcome": "insufficient",
                    "supported_document_indices": [],
                    "reason_code": "missing_requested_fact",
                }
            ),
        )
    )
    gateway = ModelAnswerabilityGateway(gateway=scripted, model="test-model")

    await gateway.assess(
        query="某品牌每份有多少千卡？",
        documents=_documents(),
        mode=ANSWERABILITY_MODE_V1,
    )

    request = scripted.requests[0]
    assert request.metadata["prompt_version"] == ANSWERABILITY_PROMPT_VERSION_V1
    assert "控制性措辞已经由系统过滤" not in (request.messages[0].content or "")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "outcome": "insufficient",
            "supported_document_indices": [0],
            "reason_code": "missing_requested_fact",
        },
        {
            "outcome": "supported",
            "supported_document_indices": [99],
            "reason_code": "directly_supported",
        },
        {
            "outcome": "supported",
            "supported_document_indices": [],
            "reason_code": "directly_supported",
        },
    ],
)
async def test_answerability_gateway_fails_closed_on_invalid_model_decision(
    payload: dict[str, object],
) -> None:
    gateway = ModelAnswerabilityGateway(
        gateway=ScriptedModelGateway((_response(payload),)),
        model="test-model",
    )

    with pytest.raises(NutritionModelGatewayError, match="answerability_provider_error"):
        await gateway.assess(query="某品牌每份有多少千卡？", documents=_documents())
