from __future__ import annotations

from slim_guard.agent_models.vision import (
    DishVisionResponse,
    VisionDishCandidate,
    VisionDishItem,
    VisionInspectionRequest,
)
from slim_guard.agents.contracts import InvocationStatus
from slim_guard.agents.dish_recognition import DishRecognitionAgent


class FakeDishVision:
    def __init__(self, response: DishVisionResponse) -> None:
        self.response = response

    async def recognize_dishes(
        self, request: VisionInspectionRequest
    ) -> DishVisionResponse:
        return self.response

    async def inspect(self, request: VisionInspectionRequest):  # type: ignore[no-untyped-def]
        raise AssertionError("generic inspection must not be used")

    async def close(self) -> None:
        return None


def _request() -> VisionInspectionRequest:
    return VisionInspectionRequest(
        model="vision-model",
        prompt="dish schema",
        image_bytes=b"image",
        image_mime_type="image/jpeg",
    )


async def test_high_confidence_single_candidate_does_not_need_confirmation() -> None:
    response = DishVisionResponse(
        image_kind="meal",
        dishes=(
            VisionDishItem(
                candidates=(VisionDishCandidate(label="清蒸鲈鱼", confidence=0.93),),
                visible_ingredients=("鱼", "葱",),
                preparation_candidates=("清蒸",),
            ),
        ),
    )
    result = await DishRecognitionAgent(vision=FakeDishVision(response)).run(
        asset_id="asset-1",
        request=_request(),
    )
    assert result.status is InvocationStatus.SUCCEEDED
    assert result.recognition is not None
    assert result.recognition.overall_requires_confirmation is False


async def test_close_candidates_are_forced_to_ask_one_question() -> None:
    response = DishVisionResponse(
        image_kind="meal",
        dishes=(
            VisionDishItem(
                candidates=(
                    VisionDishCandidate(label="麻婆豆腐", confidence=0.74),
                    VisionDishCandidate(label="家常豆腐", confidence=0.69),
                ),
            ),
        ),
        suggested_question="这盘豆腐是麻辣做法，还是普通家常做法？",
    )
    result = await DishRecognitionAgent(vision=FakeDishVision(response)).run(
        asset_id="asset-1",
        request=_request(),
    )
    assert result.recognition is not None
    assert result.recognition.overall_requires_confirmation is True
    assert result.recognition.dishes[0].requires_confirmation is True
    assert result.recognition.suggested_question == "这盘豆腐是麻辣做法，还是普通家常做法？"
