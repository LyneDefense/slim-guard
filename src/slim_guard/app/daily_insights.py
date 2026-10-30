"""Infrastructure adapters and wiring for read-only daily insights."""

from dataclasses import asdict

from slim_guard.agent_models.assembly import build_model_gateway
from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.db.session import Database
from slim_guard.domain.profile.repository import UserProfileRepository
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.features.daily_insights.contracts import (
    INSIGHT_POLICY_VERSION,
    InsightEvidence,
    PersonalContext,
)
from slim_guard.features.daily_insights.generation import InsightGenerator
from slim_guard.features.daily_insights.repository import InsightRepository
from slim_guard.features.daily_insights.service import DailyInsightsService
from slim_guard.features.daily_insights.snapshot import SnapshotReader, digest
from slim_guard.features.health_records.service import RecordService
from slim_guard.memory.long_term import LongTermMemoryRepository
from slim_guard.memory.repository import MemoryRepository
from slim_guard.memory.working import ConversationWindowRepository
from slim_guard.response_pipeline.profiles import StyleProfileResolver
from slim_guard.style_management.runtime import RuntimeStyles
from slim_guard.usage.repository import UsageRepository


class InsightPersonalContext:
    """Composition adapter: model code never sees a Session or internal ORM."""

    def __init__(self, database: Database) -> None:
        self.profiles = UserProfileRepository(database)
        self.routines = RoutinePreferenceRepository(database)
        self.memories = MemoryRepository(database)
        self.long_term = LongTermMemoryRepository(database)
        self.dialogue = ConversationWindowRepository(database)
        styles = RuntimeStyles(database)
        self.styles = StyleProfileResolver(
            profiles=styles, active_version=styles, default_version="slimguard_default_v1"
        )

    async def read(self, user_id: str) -> PersonalContext:
        profile = await self.profiles.get(user_id)
        if profile is None:
            raise ValueError("insight_user_not_found")
        routine = await self.routines.get(user_id)
        personal = asdict(profile)
        facts = [
            InsightEvidence(
                id=f"profile:{digest(personal)}",
                kind="profile",
                label="用户资料与目标",
                data=personal,
            )
        ]
        for memory in await self.memories.active(user_id, limit=100):
            facts.append(
                InsightEvidence(
                    id=f"memory:{memory.id}",
                    kind="memory",
                    label="已保存的用户信息",
                    data={
                        "key": memory.key.value,
                        "value": memory.value,
                        "review_after": memory.review_after,
                        "valid_from": memory.valid_from,
                    },
                )
            )
        for item in await self.long_term.active(user_id, limit=30):
            if item.sensitivity.value != "restricted":
                facts.append(
                    InsightEvidence(
                        id=f"memory:{item.id}",
                        kind="memory",
                        label="已保存的生活习惯",
                        data={
                            "text": item.content_text,
                            "valid_from": item.valid_from,
                            "review_after": item.review_after,
                        },
                    )
                )
        for message in await self.dialogue.recent_user_evidence(
            user_id, exclude_turn_id="", limit=8, char_limit=3000
        ):
            facts.append(
                InsightEvidence(
                    id=f"message:{message.evidence_ref}",
                    kind="user_input",
                    label="近期用户自述",
                    data={"text": message.content, "created_at": message.created_at},
                )
            )
        style = (await self.styles.resolve()).snapshot
        return PersonalContext(
            timezone=routine.timezone if routine else "Asia/Shanghai",
            evidence=tuple(facts),
            style={
                "profile": style.profile.model_dump(mode="json"),
                "guide": style.compiled_prompt[:4000],
                "package_hash": style.package_hash,
            },
        )


def build_daily_insights(
    database: Database, records: RecordService, model: ModelGateway | None, model_name: str
) -> DailyInsightsService:
    return DailyInsightsService(
        SnapshotReader(
            records,
            InsightPersonalContext(database),
            generation_version=f"{INSIGHT_POLICY_VERSION}:{model_name}",
        ),
        InsightRepository(database),
        InsightGenerator(build_model_gateway(model, UsageRepository(database)), model_name)
        if model
        else None,
    )
