"""Calendar-scoped facts. Pagination is never mistaken for a complete day."""

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from slim_guard.features.daily_insights.contracts import (
    INSIGHT_POLICY_VERSION,
    InsightEvidence,
    InsightSnapshot,
    PersonalContextReader,
)
from slim_guard.features.health_records.queries import RecordQuery
from slim_guard.features.health_records.references import RecordKind
from slim_guard.features.health_records.service import RecordService
from slim_guard.features.health_records.views import RecordView


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


def record_evidence(record: RecordView, *, today: bool) -> InsightEvidence:
    data = record.model_dump(mode="json")
    content = data["content"]
    # Image identity is not nutrition evidence; no raw images are sent again.
    content.pop("photo_asset_ids", None)
    kind = record.ref.kind
    if kind == "meal":
        label = "、".join(food["name"] or "未确认菜品" for food in content["foods"])
    elif kind == "exercise":
        label = str(content["activity_name"])
    elif kind == "weight":
        label = f"体重 {content['weight_grams'] / 1000:g} kg"
    else:
        label = f"体脂 {content['basis_points'] / 100:g}%"
    return InsightEvidence(
        id=f"record:{kind}:{record.ref.record_id}:r{record.ref.revision}",
        kind=kind,
        label=label,
        data=data,
        today=today,
    )


class SnapshotReader:
    def __init__(
        self,
        records: RecordService,
        context: PersonalContextReader,
        *,
        generation_version: str = INSIGHT_POLICY_VERSION,
    ) -> None:
        self.records, self.context = records, context
        self.version = generation_version

    async def read(self, user_id: str, now: datetime) -> InsightSnapshot:
        context = await self.context.read(user_id)
        day = now.astimezone(ZoneInfo(context.timezone)).date()
        evidence = list(context.evidence)
        kinds: tuple[RecordKind, ...] = ("meal", "exercise", "weight", "body_fat")
        for kind in kinds:
            cursor = None
            while True:
                page = await self.records.search(
                    user_id,
                    RecordQuery(
                        kind=kind,
                        from_date=day,
                        to_date=day,
                        timezone=context.timezone,
                        limit=100,
                        cursor=cursor,
                    ),
                )
                evidence.extend(record_evidence(item, today=True) for item in page.records)
                if not page.has_more:
                    break
                if not page.next_cursor or page.next_cursor == cursor:
                    raise ValueError("Invalid record pagination")
                cursor = page.next_cursor
            # Historical context is intentionally bounded; never infer a full
            # streak or missing meals from this sample.
            history = await self.records.search(
                user_id,
                RecordQuery(
                    kind=kind,
                    from_date=day - timedelta(days=7),
                    to_date=day - timedelta(days=1),
                    timezone=context.timezone,
                    limit=12,
                ),
            )
            evidence.extend(record_evidence(item, today=False) for item in history.records)
        count = sum(item.today for item in evidence)
        scope = {
            "date": day.isoformat(),
            "timezone": context.timezone,
            "record_refs": sorted(item.id for item in evidence if item.today),
        }
        evidence.append(
            InsightEvidence(
                id=f"today:{digest(scope)}",
                kind="day_scope",
                label="本日已记录范围",
                data=scope,
            )
        )
        ordered = tuple(sorted(evidence, key=lambda item: item.id))
        context_key = digest(
            {
                "personal": [e.model_dump() for e in context.evidence if e.kind != "user_input"],
                "style": context.style,
                "generation_version": self.version,
            }
        )
        key = digest(
            {
                "date": day,
                "timezone": context.timezone,
                "facts": [e.model_dump() for e in ordered],
                "context": context_key,
            }
        )
        return InsightSnapshot(
            user_id=user_id,
            day=day,
            timezone=context.timezone,
            as_of=now,
            context_key=context_key,
            evidence=ordered,
            style=context.style,
            today_record_count=count,
            fingerprint=key,
            generation_version=self.version,
        )
