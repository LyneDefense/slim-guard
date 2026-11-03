"""Wire direct coach generation; published styles remain managed offline."""

from collections.abc import Callable
from datetime import datetime

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agents.coach import CoachResponder
from slim_guard.db.session import Database
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.response_pipeline import AgentResponseFinalizer, StyleProfileResolver
from slim_guard.runtime.invocation import InvocationRunner, InvocationStore
from slim_guard.style_management.runtime import RuntimeStyles


def build_response_finalizer(
    *,
    database: Database,
    model: ModelGateway,
    model_name: str,
    invocation_store: InvocationStore,
    recorder: HarnessRunRecorder,
    graph_version: str,
    clock: Callable[[], datetime] | None,
) -> AgentResponseFinalizer:
    runner = InvocationRunner(model=model, clock=clock)
    return AgentResponseFinalizer(
        coach=CoachResponder(runner=runner, model=model_name),
        profile_resolver=StyleProfileResolver(
            profiles=RuntimeStyles(database),
            active_version=RuntimeStyles(database),
            default_version="slimguard_default_v1",
        ),
        persistence=invocation_store,
        recorder=recorder,
        graph_version=graph_version,
        clock=clock,
    )
