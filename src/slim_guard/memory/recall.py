from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from slim_guard.agent_models.errors import ModelGatewayError
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ToolChoice,
    ToolDefinition,
)
from slim_guard.harness.events import ItemType, TurnTrigger
from slim_guard.harness.initialization import InitializedTurn
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.memory.engine import MemoryEngine, MemoryEngineError, SemanticMemory

logger = logging.getLogger(__name__)

MEMORY_RECALL_POLICY_VERSION = "model-ranked-hybrid-recall-v1"
_SELECT_TOOL_NAME = "select_relevant_memories"
_RECALL_INSTRUCTIONS = """
你是 SlimGuard 的记忆召回器，不负责回复用户。
请根据当前请求的真实语义，从候选长期记忆中选择完成本轮任务确实需要的少量事实。

要求：
- 理解语义和指代，不用关键词匹配。
- 数据库候选是权威事实；语义检索分数只是召回提示，不能覆盖数据库值。
- 不选择仅仅“可能有用”的资料；但健康安全、目标比较或用户询问已保存资料时不要漏选。
- 当前请求要求保存、查询或修改某项资料时，选择能够支持该操作的已有事实。
- 不得跨用户，不得创造候选列表之外的 ID。
- 调用 select_relevant_memories 返回选择结果和一句可审计的简短理由；不要输出思维过程。
""".strip()


class _RecallSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_memory_ids: tuple[str, ...] = Field(default=(), max_length=20)
    reason_summary: str = Field(min_length=1, max_length=300)


@dataclass(frozen=True, slots=True)
class MemoryRecallResult:
    context: dict[str, Any]
    candidate_count: int
    selected_count: int
    engine_candidate_count: int
    engine_status: str
    degraded: bool
    reason_summary: str


class MemoryRecaller(Protocol):
    async def recall(
        self,
        *,
        initialized: InitializedTurn,
        current_time: datetime,
        context: Mapping[str, Any],
    ) -> MemoryRecallResult: ...


class ModelFirstMemoryRecaller:
    def __init__(
        self,
        *,
        model: ModelGateway,
        model_name: str,
        recorder: HarnessRunRecorder,
        engine: MemoryEngine | None = None,
        search_limit: int = 12,
        max_selected: int = 8,
        max_output_tokens: int = 600,
    ) -> None:
        if search_limit < 1 or max_selected < 1:
            raise ValueError("Memory recall limits must be positive")
        self._model = model
        self._model_name = model_name
        self._recorder = recorder
        self._engine = engine
        self._search_limit = search_limit
        self._max_selected = max_selected
        self._max_output_tokens = max_output_tokens

    async def recall(
        self,
        *,
        initialized: InitializedTurn,
        current_time: datetime,
        context: Mapping[str, Any],
    ) -> MemoryRecallResult:
        prepared = dict(context)
        raw_memories = prepared.get("profile_memory")
        candidates = [row for row in raw_memories if isinstance(row, dict)] if isinstance(
            raw_memories, list
        ) else []
        if not candidates:
            prepared.pop("profile_memory", None)
            return MemoryRecallResult(prepared, 0, 0, 0, "not_needed", False, "没有候选记忆")

        query = self._query(initialized)
        semantic: tuple[SemanticMemory, ...] = ()
        engine_status = "disabled"
        if self._engine is not None:
            try:
                semantic = await self._engine.search(
                    user_id=initialized.context.user_id,
                    query=query,
                    limit=self._search_limit,
                )
                engine_status = "succeeded"
            except MemoryEngineError as exc:
                engine_status = "failed"
                logger.warning(
                    "memory_recall_engine_failed",
                    extra={"turn_id": initialized.turn.id, "error_type": type(exc).__name__},
                )

        score_by_id = self._score_by_canonical_id(semantic)
        request = self._request(
            user_id=initialized.context.user_id,
            trigger=initialized.turn.trigger,
            query=query,
            candidates=candidates,
            score_by_id=score_by_id,
        )
        try:
            response = await self._model.complete(request)
            await self._recorder.record_model_response(
                turn_id=initialized.turn.id,
                request=request,
                response=response,
                call_index=0,
                started_at=current_time,
                completed_at=current_time,
            )
            selection = self._selection(response.message.content, response.message.tool_calls)
            available_ids = {
                str(candidate.get("memory_id"))
                for candidate in candidates
                if isinstance(candidate.get("memory_id"), str)
            }
            selected_ids = tuple(
                memory_id
                for memory_id in selection.selected_memory_ids
                if memory_id in available_ids
            )[: self._max_selected]
            selected_set = set(selected_ids)
            selected = [
                candidate
                for candidate in candidates
                if candidate.get("memory_id") in selected_set
            ]
            if selected:
                prepared["profile_memory"] = selected
            else:
                prepared.pop("profile_memory", None)
            result = MemoryRecallResult(
                context=prepared,
                candidate_count=len(candidates),
                selected_count=len(selected),
                engine_candidate_count=len(semantic),
                engine_status=engine_status,
                degraded=False,
                reason_summary=selection.reason_summary,
            )
        except (ModelGatewayError, ValidationError, ValueError, TypeError) as exc:
            logger.warning(
                "memory_recall_model_failed",
                extra={"turn_id": initialized.turn.id, "error_type": type(exc).__name__},
            )
            result = MemoryRecallResult(
                context=prepared,
                candidate_count=len(candidates),
                selected_count=len(candidates),
                engine_candidate_count=len(semantic),
                engine_status=engine_status,
                degraded=True,
                reason_summary="召回模型不可用，保守带入有界的数据库权威事实",
            )
        await self._recorder.record_memory_recall(
            turn_id=initialized.turn.id,
            payload={
                "policy_version": MEMORY_RECALL_POLICY_VERSION,
                "provider": self._engine.provider_name if self._engine is not None else "disabled",
                "engine_status": result.engine_status,
                "candidate_count": result.candidate_count,
                "engine_candidate_count": result.engine_candidate_count,
                "selected_count": result.selected_count,
                "degraded": result.degraded,
                "reason_summary": result.reason_summary,
            },
        )
        return result

    def _request(
        self,
        *,
        user_id: str,
        trigger: TurnTrigger,
        query: str,
        candidates: list[dict[str, Any]],
        score_by_id: dict[str, float],
    ) -> ModelRequest:
        compact = []
        for candidate in candidates:
            memory_id = candidate.get("memory_id")
            compact.append(
                {
                    "memory_id": memory_id,
                    "kind": candidate.get("kind"),
                    "key": candidate.get("key"),
                    "value": candidate.get("value"),
                    "sensitivity": candidate.get("sensitivity"),
                    "stale": candidate.get("stale"),
                    **(
                        {"semantic_score": score_by_id[memory_id]}
                        if isinstance(memory_id, str) and memory_id in score_by_id
                        else {}
                    ),
                }
            )
        tool = ToolDefinition(
            name=_SELECT_TOOL_NAME,
            description="选择本轮真正相关的权威长期记忆。",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "selected_memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": self._max_selected,
                    },
                    "reason_summary": {"type": "string", "maxLength": 300},
                },
                "required": ["selected_memory_ids", "reason_summary"],
                "additionalProperties": False,
            },
            version=MEMORY_RECALL_POLICY_VERSION,
        )
        return ModelRequest(
            purpose=ModelPurpose.MEMORY_RECALL,
            model=self._model_name,
            messages=(
                ModelMessage(role=MessageRole.SYSTEM, content=_RECALL_INSTRUCTIONS),
                ModelMessage(
                    role=MessageRole.USER,
                    content=json.dumps(
                        {
                            "trigger": trigger.value,
                            "current_request": query,
                            "authoritative_candidates": compact,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            ),
            tools=(tool,),
            tool_choice=ToolChoice.AUTO,
            max_output_tokens=self._max_output_tokens,
            temperature=0,
            metadata={
                "memory_policy_version": MEMORY_RECALL_POLICY_VERSION,
                "user_id": hashlib.sha256(user_id.encode("utf-8")).hexdigest(),
            },
        )

    @staticmethod
    def _selection(content: str | None, tool_calls: tuple[Any, ...]) -> _RecallSelection:
        for call in tool_calls:
            if call.name == _SELECT_TOOL_NAME:
                return _RecallSelection.model_validate(call.arguments)
        if content is None:
            raise ValueError("Memory recall model returned no selection")
        normalized = content.strip()
        if normalized.startswith("```"):
            lines = normalized.splitlines()
            normalized = "\n".join(lines[1:-1]).strip()
        return _RecallSelection.model_validate_json(normalized)

    @staticmethod
    def _query(initialized: InitializedTurn) -> str:
        texts = [
            str(item.payload.get("text") or "").strip()
            for item in initialized.input_items
            if item.item_type is ItemType.USER_MESSAGE
        ]
        query = "\n".join(text for text in texts if text)
        return query or f"执行 {initialized.turn.trigger.value} 定时任务"

    @staticmethod
    def _score_by_canonical_id(memories: tuple[SemanticMemory, ...]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for memory in memories:
            canonical_id = memory.metadata.get("slim_guard_memory_id")
            if not isinstance(canonical_id, str) or memory.score is None:
                continue
            scores[canonical_id] = max(scores.get(canonical_id, 0.0), memory.score)
        return scores
