from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from slim_guard.agent_models.vision import (
    VisionCertainty,
    VisionInspectionRequest,
    VisionInspectionResponse,
    VisionObservation,
)
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.assets.contracts import SaveImageAssetCommand
from slim_guard.domain.assets.repository import ImageAssetRepository
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResultStatus
from slim_guard.tools.image import ImageToolHandlers, InspectImageArguments

NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)


class FakeVisionGateway:
    def __init__(self) -> None:
        self.requests: list[VisionInspectionRequest] = []

    async def inspect(self, request: VisionInspectionRequest) -> VisionInspectionResponse:
        self.requests.append(request)
        return VisionInspectionResponse(
            category="weight_scale",
            description="体重秤显示 77.6 kg。",
            observations=(
                VisionObservation(
                    label="体重",
                    detail="屏幕显示 77.6 kg",
                    certainty=VisionCertainty.CLEAR,
                ),
            ),
            requires_user_confirmation=False,
        )

    async def close(self) -> None:
        return None


async def prepare_asset(
    tmp_path: Path,
) -> tuple[Database, ImageAssetRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'image-tools.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                SlimGuardUser(id="user-2", first_seen_at=NOW, last_seen_at=NOW),
            )
        )
    repository = ImageAssetRepository(database)
    created = await repository.save(
        SaveImageAssetCommand(
            user_id="user-1",
            content=b"\x89PNG\r\n\x1a\nimage",
            declared_mime_type="image/png",
            channel_id="default",
            source_message_id="image-message-1",
            expires_at=NOW + timedelta(days=7),
        )
    )
    return database, repository, created.asset.id


def context(*, user_id: str = "user-1") -> ToolContext:
    return ToolContext(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="call-inspect",
        user_id=user_id,
        agent_version_id="agent-version-1",
        execution_mode=ToolExecutionMode.EVALUATION,
    )


async def test_inspect_image_reads_owned_asset_and_returns_observation(tmp_path: Path) -> None:
    database, assets, asset_id = await prepare_asset(tmp_path)
    vision = FakeVisionGateway()
    handlers = ImageToolHandlers(
        assets=assets,
        vision=vision,
        vision_model="glm-5v-turbo",
        clock=lambda: NOW,
    )
    try:
        result = await handlers.inspect_image(
            context(),
            InspectImageArguments(asset_id=asset_id, focus="weight_scale"),
        )

        assert result.status is ToolResultStatus.SUCCEEDED
        assert result.output["description"] == "体重秤显示 77.6 kg。"
        assert result.output["category"] == "weight_scale"
        assert result.output["observations"][0]["certainty"] == "clear"
        assert result.output["requires_user_confirmation"] is False
        assert result.source_ids == (asset_id,)
        assert vision.requests[0].model == "glm-5v-turbo"
        assert vision.requests[0].image_mime_type == "image/png"
        assert "不得猜测" in vision.requests[0].prompt
    finally:
        await database.close()


async def test_inspect_image_cannot_read_another_users_asset(tmp_path: Path) -> None:
    database, assets, asset_id = await prepare_asset(tmp_path)
    vision = FakeVisionGateway()
    handlers = ImageToolHandlers(
        assets=assets,
        vision=vision,
        vision_model="glm-5v-turbo",
        clock=lambda: NOW,
    )
    try:
        result = await handlers.inspect_image(
            context(user_id="user-2"),
            InspectImageArguments(asset_id=asset_id),
        )

        assert result.status is ToolResultStatus.FAILED
        assert result.failure is not None
        assert result.failure.code == "image_asset_unavailable"
        assert vision.requests == []
    finally:
        await database.close()
