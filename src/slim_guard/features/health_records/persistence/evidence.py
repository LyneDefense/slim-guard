from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import AgentItemRecord, AgentThreadRecord, ImageAssetRecord
from slim_guard.features.health_records.content import MealContent
from slim_guard.features.health_records.persistence.edit_sources import MealEditSource
from slim_guard.features.health_records.provenance import FieldEvidence
from slim_guard.features.health_records.references import RecordError
from slim_guard.features.health_records.views import RecordContent


async def validate_evidence(session: AsyncSession, user_id: str, evidence: FieldEvidence) -> None:
    if evidence.source_kind == "legacy":
        raise RecordError("New writes cannot claim legacy provenance")
    for reference in evidence.source_refs:
        item = await session.scalar(
            select(AgentItemRecord)
            .join(
                AgentThreadRecord,
                AgentThreadRecord.id == AgentItemRecord.thread_id,
            )
            .where(AgentItemRecord.id == reference, AgentThreadRecord.user_id == user_id)
        )
        if item is not None:
            if evidence.source_kind == "image_recognized":
                raise RecordError(
                    "Image-recognized facts require an image-asset reference; "
                    "use user_reported for facts stated in a user message"
                )
            if evidence.source_kind == "user_reported" and item.item_type != "user_message":
                raise RecordError(
                    "User-reported facts require a user-message reference; "
                    "use image_recognized for facts sourced from an image"
                )
            continue
        asset = await session.get(ImageAssetRecord, reference)
        if asset is not None and asset.user_id == user_id:
            if evidence.source_kind == "user_reported":
                raise RecordError(
                    "User-reported facts require a user-message reference; "
                    "use image_recognized for facts sourced from an image"
                )
            continue
        edit = await session.get(MealEditSource, reference)
        if edit is not None and edit.user_id == user_id and evidence.source_kind == "user_reported":
            continue
        raise RecordError("Evidence reference is unavailable to this user")


async def validate_meal_photos(session: AsyncSession, user_id: str, content: RecordContent) -> None:
    if not isinstance(content, MealContent):
        return
    for asset_id in content.photo_asset_ids:
        asset = await session.get(ImageAssetRecord, asset_id)
        if asset is None or asset.user_id != user_id:
            raise RecordError("Meal photo is unavailable to this user")
