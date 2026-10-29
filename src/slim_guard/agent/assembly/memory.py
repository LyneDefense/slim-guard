"""Wire memory storage, recall and extraction scheduling."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.db.session import Database
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.memory.config import MemoryRuntimeConfig
from slim_guard.memory.engine import MemoryEngine
from slim_guard.memory.extraction import MemoryExtractionJobRepository
from slim_guard.memory.grounding import ConstraintMemoryWriter, ModelGroundingReviewer
from slim_guard.memory.long_term import LongTermMemoryRepository
from slim_guard.memory.recall import LongTermMemoryRecaller
from slim_guard.memory.registry import MemorySchemaRegistry
from slim_guard.memory.repository import MemoryRepository
from slim_guard.memory.working import ConversationWindowRepository
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.long_term_memory import long_term_memory_tool_executors
from slim_guard.tools.memory import memory_tool_executors


@dataclass(frozen=True, slots=True)
class MemoryComponents:
    """Assembly-only connections shared by context, tools and the Turn runtime."""

    profiles: MemoryRepository
    conversation: ConversationWindowRepository
    executors: Mapping[str, ToolExecutor[Any]]
    recaller: LongTermMemoryRecaller | None
    extraction_jobs: MemoryExtractionJobRepository | None


def build_memory_components(
    *,
    database: Database,
    config: MemoryRuntimeConfig,
    model_name: str,
    grounding_model: ModelGateway,
    extraction_model: ModelGateway | None,
    recall_model: ModelGateway | None,
    engine: MemoryEngine | None,
    recorder: HarnessRunRecorder,
    clock: Callable[[], datetime] | None,
) -> MemoryComponents:
    profiles = MemoryRepository(
        database,
        registry=MemorySchemaRegistry(health_review_days=config.health_review_days),
        clock=clock,
        # Structured profile facts remain authoritative, not Mem0 text memory.
        index_sync_enabled=False,
    )
    conversation = ConversationWindowRepository(database)
    long_term = LongTermMemoryRepository(
        database,
        clock=clock,
        index_sync_enabled=engine is not None,
    )
    return MemoryComponents(
        profiles=profiles,
        conversation=conversation,
        executors={
            **memory_tool_executors(
                profiles,
                constraint_writer=ConstraintMemoryWriter(
                    profiles, ModelGroundingReviewer(grounding_model, model_name)
                ),
            ),
            **long_term_memory_tool_executors(long_term),
        },
        recaller=(
            LongTermMemoryRecaller(
                model=recall_model,
                model_name=model_name,
                recorder=recorder,
                memories=long_term,
                engine=engine,
                search_limit=config.recall_search_limit,
                max_selected=config.recall_max_selected,
            )
            if recall_model is not None
            else None
        ),
        extraction_jobs=(
            MemoryExtractionJobRepository(database, clock=clock)
            if extraction_model is not None
            else None
        ),
    )
