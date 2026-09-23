"""Wire nutrition consultation and its Core-facing tool adapter."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agents.nutrition import (
    NutritionAgentToolHandler,
    NutritionSpecialist,
    nutrition_agent_tool_executors,
)
from slim_guard.agents.nutrition.tools import (
    NutritionKnowledgeRepository as NutritionKnowledgePort,
)
from slim_guard.agents.nutrition.tools import NutritionToolRegistry
from slim_guard.harness.context_data import ContextDataProvider
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.runtime.invocation import InvocationStore
from slim_guard.tools.gateway import ToolExecutor


@dataclass(frozen=True, slots=True)
class NutritionComponents:
    specialist: NutritionSpecialist
    executors: Mapping[str, ToolExecutor[Any]]


def build_nutrition_components(
    *,
    model: ModelGateway,
    model_name: str,
    knowledge: NutritionKnowledgePort | None,
    state: HarnessStateRepository,
    context: ContextDataProvider,
    invocation_store: InvocationStore,
    graph_version: str,
    timeout_seconds: float,
    clock: Callable[[], datetime] | None,
) -> NutritionComponents:
    specialist = NutritionSpecialist(
        model=model,
        model_name=model_name,
        graph_version=graph_version,
        nutrition_tools=NutritionToolRegistry(
            knowledge_repository=knowledge,
        ),
        invocation_store=invocation_store,
        clock=clock,
    )
    handler = NutritionAgentToolHandler(
        specialist=specialist,
        state=state,
        context_data=context,
        timeout=timedelta(seconds=timeout_seconds),
        clock=clock,
    )
    return NutritionComponents(
        specialist=specialist,
        executors=nutrition_agent_tool_executors(handler),
    )
