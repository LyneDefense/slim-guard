"""Meal-image specialist with code-owned uncertainty policy."""

from __future__ import annotations

from dataclasses import dataclass

from slim_guard.agent_models.errors import (
    InvalidModelResponse,
    ModelGatewayError,
    ModelProviderError,
    ModelTimeoutError,
    ModelTransportError,
)
from slim_guard.agent_models.vision import (
    DishVisionResponse,
    VisionInspectionRequest,
    VisionModelGateway,
)
from slim_guard.agents.contracts import InvocationStatus
from slim_guard.agents.dish_recognition.contracts import (
    DishCandidate,
    DishImageKind,
    DishRecognitionAgentResult,
    DishRecognitionResult,
    RecognizedDish,
)
from slim_guard.agents.dish_recognition.prompt import (
    DISH_RECOGNITION_PROMPT_VERSION,
)


@dataclass(frozen=True, slots=True)
class DishRecognitionPolicy:
    version: str = "dish-confirmation-policy-v1"
    automatic_confidence: float = 0.82
    minimum_margin: float = 0.15

    def requires_confirmation(self, response: DishVisionResponse, index: int) -> bool:
        item = response.dishes[index]
        candidates = sorted(item.candidates, key=lambda candidate: -candidate.confidence)
        top = candidates[0].confidence
        runner_up = candidates[1].confidence if len(candidates) > 1 else 0.0
        return bool(
            response.image_kind == "unusable"
            or response.quality_flags
            or item.uncertainty_reasons
            or top < self.automatic_confidence
            or top - runner_up < self.minimum_margin
        )


class DishRecognitionAgent:
    def __init__(
        self,
        *,
        vision: VisionModelGateway,
        policy: DishRecognitionPolicy | None = None,
    ) -> None:
        self._vision = vision
        self._policy = policy or DishRecognitionPolicy()

    async def run(
        self,
        *,
        asset_id: str,
        request: VisionInspectionRequest,
    ) -> DishRecognitionAgentResult:
        try:
            raw = await self._vision.recognize_dishes(request)
            recognition = self._normalize(asset_id=asset_id, model=request.model, raw=raw)
        except (ModelTimeoutError, ModelTransportError):
            return self._failed("vision_temporary_failure")
        except ModelProviderError:
            return self._failed("vision_provider_failure")
        except InvalidModelResponse:
            return self._failed("vision_invalid_response")
        except ModelGatewayError:
            return self._failed("vision_failure")
        except (ValueError, IndexError):
            return self._failed("dish_recognition_policy_invalid")
        return DishRecognitionAgentResult(
            status=InvocationStatus.SUCCEEDED,
            recognition=recognition,
            model_call_count=1,
            total_token_count=raw.usage.total_tokens,
            usage=raw.usage,
        )

    def _normalize(
        self,
        *,
        asset_id: str,
        model: str,
        raw: DishVisionResponse,
    ) -> DishRecognitionResult:
        dishes: list[RecognizedDish] = []
        for index, item in enumerate(raw.dishes):
            candidates = tuple(
                DishCandidate(label=candidate.label, confidence=candidate.confidence)
                for candidate in sorted(
                    item.candidates,
                    key=lambda candidate: (-candidate.confidence, candidate.label),
                )
            )
            requires_confirmation = self._policy.requires_confirmation(raw, index)
            reasons = item.uncertainty_reasons
            if requires_confirmation and not reasons:
                reasons = ("视觉证据不足以唯一确认菜名",)
            dishes.append(
                RecognizedDish(
                    dish_ref=f"dish-{index + 1}",
                    candidates=candidates,
                    visible_ingredients=item.visible_ingredients,
                    preparation_candidates=item.preparation_candidates,
                    uncertainty_reasons=reasons,
                    requires_confirmation=requires_confirmation,
                )
            )
        overall = raw.image_kind == "unusable" or any(item.requires_confirmation for item in dishes)
        question = raw.suggested_question if overall else None
        if overall and question is None:
            question = "图片里有菜品还不能确定，请告诉我它具体是什么菜？"
        return DishRecognitionResult(
            asset_id=asset_id,
            model=model,
            prompt_version=DISH_RECOGNITION_PROMPT_VERSION,
            policy_version=self._policy.version,
            image_kind=DishImageKind(raw.image_kind),
            quality_flags=raw.quality_flags,
            dishes=tuple(dishes),
            suggested_question=question,
            overall_requires_confirmation=overall,
            provider_request_id=raw.provider_request_id,
        )

    @staticmethod
    def _failed(code: str) -> DishRecognitionAgentResult:
        return DishRecognitionAgentResult(
            status=InvocationStatus.FAILED,
            recognition=None,
            model_call_count=1,
            total_token_count=0,
            failure_code=code,
        )


__all__ = ["DishRecognitionAgent", "DishRecognitionPolicy"]
