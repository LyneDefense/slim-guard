"""Public runtime adapter for published immutable style versions."""

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from slim_guard.agent_models.embeddings import EmbeddingGateway
from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agents.contracts import AgentInvocation, ResponseContentBlock, ResponsePlan
from slim_guard.agents.style import (
    RESPONSE_STYLE_PROMPT_VERSION,
    STYLE_MAX_MODEL_CALLS,
    ResponseStyleAgent,
    StyleContext,
    StyleExample,
    StyleProfile,
)
from slim_guard.agents.style.contracts import StyleProfileSnapshot
from slim_guard.agents.style.retrieval import similar_examples
from slim_guard.db.models import new_uuid, utc_now
from slim_guard.db.session import Database
from slim_guard.runtime.invocation import InvocationRunner

from .models import Style, Version


def profile(version: str, style_id: str, name: str, guide: dict[str, Any]) -> StyleProfile:
    rules = tuple(r["text"] for r in guide.get("rules", []) if r["confidence"] == "stable")
    return StyleProfile(
        profile_id=style_id,
        version=version,
        display_name=name,
        description="只采用本版本的稳定表达规则。候选及冲突规则不进入运行时。",
        tone_rules=rules or ("忠实原意，自然简洁，不新增内容。",),
        prohibited_phrases=tuple(guide.get("prohibited_phrases", [])),
    )


class RuntimeStyles:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def resolve(self) -> str:
        async with self.database.session() as s:
            row = await s.scalar(select(Style).where(Style.is_default.is_(True)))
            return (row.active_version_id if row else None) or "doctor_builtin_v1"

    async def get_runtime_snapshot(self, version: str) -> StyleProfileSnapshot | None:
        async with self.database.session() as s:
            row = await s.get(Version, version)
            if row is None or row.status != "published":
                return None
            style = await s.get(Style, row.style_id)
            if style is None:
                return None
            return StyleProfileSnapshot(
                profile=profile(row.id, row.style_id, style.name, row.guide),
                examples=tuple(
                    StyleExample(
                        example_id=e["id"],
                        style_profile_version=row.id,
                        original_response=e["original_response"],
                        text=e["desired_response"],
                        embedding=tuple(e.get("embedding", [])),
                        embedding_model=e.get("embedding_model", ""),
                    )
                    for e in row.examples
                ),
            )

    async def get_profile(self, version: str) -> StyleProfile | None:
        snapshot = await self.get_runtime_snapshot(version)
        return snapshot.profile if snapshot else None


async def evaluate_example(
    gateway: ModelGateway,
    model: str,
    version_id: str,
    guide: dict[str, Any],
    example: dict[str, Any],
    corpus: list[dict[str, Any]] | None = None,
    embedding: EmbeddingGateway | None = None,
) -> dict[str, Any]:
    turn_id = new_uuid()
    context = StyleContext(
        turn_id=turn_id,
        profile=profile(version_id, "evaluation", "待评审风格", guide),
        examples=await similar_examples(
            example["original_response"],
            tuple(
                StyleExample(
                    example_id=e["id"],
                    style_profile_version=version_id,
                    original_response=e["original_response"],
                    text=e["desired_response"],
                    embedding=tuple(e.get("embedding", [])),
                    embedding_model=e.get("embedding_model", ""),
                )
                for e in (corpus or [])
                if e["id"] != example.get("id")
                and e["original_response"].strip() != example["original_response"].strip()
            ),
            embedding,
        ),
        response_plan=ResponsePlan(
            content_blocks=(
                ResponseContentBlock(
                    block_id="neutral", kind="social_act", text=example["original_response"]
                ),
            )
        ),
    )
    invocation = AgentInvocation(
        invocation_id=new_uuid(),
        trace_id=new_uuid(),
        thread_id=new_uuid(),
        turn_id=turn_id,
        graph_version="style-build-v2",
        agent_role="response_style",
        agent_version=RESPONSE_STYLE_PROMPT_VERSION,
        caller="style_builder",
        input_schema="StyleContext",
        deadline_at=utc_now() + timedelta(seconds=120),
        max_model_calls=STYLE_MAX_MODEL_CALLS,
        max_tool_calls=0,
        max_total_tokens=16000,
    )
    result = await ResponseStyleAgent(runner=InvocationRunner(model=gateway), model=model).run(
        invocation=invocation, context=context
    )
    return {
        "text": result.response.text,
        "automated": {
            "passed": not result.used_fallback,
            "failure_code": result.failure_code,
            "model_calls": result.model_call_count,
            "tokens": result.total_token_count,
            "checks": list(result.checks),
        },
    }
