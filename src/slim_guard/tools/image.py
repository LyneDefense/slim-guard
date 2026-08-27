from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from slim_guard.agent_models.errors import (
    InvalidModelResponse,
    ModelGatewayError,
    ModelProviderError,
    ModelTimeoutError,
    ModelTransportError,
)
from slim_guard.agent_models.vision import (
    VisionInspectionRequest,
    VisionModelGateway,
)
from slim_guard.domain.assets.repository import ImageAssetRepository
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolResult,
)
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

INSPECT_IMAGE_TOOL_NAME = "inspect_image"
IMAGE_TOOL_VERSION = "v1"

_FOCUS_PROMPTS = {
    "auto": (
        "识别这张减脂打卡图片属于体重秤、食物、运动截图还是其他内容。"
        "只描述清晰可见的事实、数值、单位和不确定之处，不提供建议，不补全被遮挡内容。"
    ),
    "weight_scale": (
        "检查这张体重秤图片。逐字报告清晰可见的体重数值、单位和测量状态；"
        "区分体重与体脂率等其他指标。看不清时明确说明，不得猜测。"
    ),
    "meal": (
        "检查这张饮食图片。描述清晰可见的主要食物、可数份数和大致份量范围；"
        "标明无法识别或被遮挡的部分，不估算精确热量。"
    ),
    "exercise": (
        "检查这张运动截图或运动图片。报告清晰可见的项目、时长、步数、距离"
        "以及设备显示的能量数据，并区分明确数据与不确定观察。"
    ),
}


class InspectImageArguments(ToolArguments):
    asset_id: str
    focus: Literal["auto", "weight_scale", "meal", "exercise"] = "auto"


class ImageToolHandlers:
    def __init__(
        self,
        *,
        assets: ImageAssetRepository,
        vision: VisionModelGateway | None,
        vision_model: str,
        max_output_tokens: int = 1024,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._assets = assets
        self._vision = vision
        self._vision_model = vision_model
        self._max_output_tokens = max_output_tokens
        self._clock = clock or self._utc_now

    async def inspect_image(
        self,
        context: ToolContext,
        arguments: InspectImageArguments,
    ) -> ToolResult:
        if self._vision is None:
            return ToolResult.failed(
                code="vision_unavailable",
                message="Image inspection is temporarily unavailable.",
                retryable=True,
            )
        asset = await self._assets.get_for_user(
            arguments.asset_id,
            user_id=context.user_id,
            at=self._clock(),
        )
        if asset is None:
            return ToolResult.failed(
                code="image_asset_unavailable",
                message="The image is missing, expired, or not available to this user.",
            )
        try:
            response = await self._vision.inspect(
                VisionInspectionRequest(
                    model=self._vision_model,
                    prompt=_FOCUS_PROMPTS[arguments.focus],
                    image_bytes=asset.content,
                    image_mime_type=asset.ref.mime_type,
                    max_output_tokens=self._max_output_tokens,
                    metadata={
                        "user_id": hashlib.sha256(context.user_id.encode()).hexdigest()
                    },
                )
            )
        except (ModelTimeoutError, ModelTransportError):
            return ToolResult.failed(
                code="vision_temporary_failure",
                message="Image inspection could not complete right now.",
                retryable=True,
            )
        except ModelProviderError as exc:
            retryable = exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500
            return ToolResult.failed(
                code="vision_provider_failure",
                message="The vision provider could not inspect this image.",
                retryable=retryable,
            )
        except InvalidModelResponse:
            return ToolResult.failed(
                code="vision_invalid_response",
                message="Image inspection returned an unusable result.",
            )
        except ModelGatewayError:
            return ToolResult.failed(
                code="vision_failure",
                message="Image inspection failed.",
            )
        return ToolResult.success(
            output={
                "asset_id": asset.ref.id,
                "focus": arguments.focus,
                "description": response.description,
            },
            source_ids=(asset.ref.id,),
        )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)


def image_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=INSPECT_IMAGE_TOOL_NAME,
            description=(
                "Inspect an image attachment owned by the current user. Pass the exact "
                "asset_id from the image_attachment input. The result contains visual "
                "observations, not an authoritative health record."
            ),
            version=IMAGE_TOOL_VERSION,
            arguments_model=InspectImageArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=45,
        ),
    )


def image_tool_executors(
    *,
    assets: ImageAssetRepository,
    vision: VisionModelGateway | None,
    vision_model: str,
    max_output_tokens: int = 1024,
    clock: Callable[[], datetime] | None = None,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = ImageToolHandlers(
        assets=assets,
        vision=vision,
        vision_model=vision_model,
        max_output_tokens=max_output_tokens,
        clock=clock,
    )
    return {
        INSPECT_IMAGE_TOOL_NAME: ToolExecutor(
            arguments_model=InspectImageArguments,
            handler=handlers.inspect_image,
        )
    }
