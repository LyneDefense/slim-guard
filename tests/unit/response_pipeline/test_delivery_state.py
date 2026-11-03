"""Delivery of an explicit error notice is not successful doctor generation."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from slim_guard.agent.runtime import AgentRuntimeResult
from slim_guard.domain.routine.contracts import ReminderKind
from slim_guard.harness.termination import HarnessTermination
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState
from slim_guard.mobile.service import MobileApplicationService
from slim_guard.services.routine_scheduler import RoutineSchedulerService


@pytest.mark.parametrize(
    "failure,generation", [(None, "succeeded"), ("structured_output_invalid", "degraded")]
)
async def test_mobile_keeps_generation_and_delivery_outcomes_separate(failure, generation):
    traces = AsyncMock()
    service = SimpleNamespace(_traces=traces)
    result = AgentRuntimeResult(
        thread_id="thread",
        turn_id="turn",
        agent_version_id="version",
        termination=HarnessTermination.FINAL_RESPONSE,
        final_text="可发送的消息或错误提示。",
        failure_code=failure,
    )
    await MobileApplicationService._finish_trace(service, "trace", result, succeeded=True)
    assert traces.mark_generation.call_args.kwargs["status"] == generation
    assert traces.mark_generation.call_args.kwargs["failure_code"] == failure
    assert traces.mark_delivery.call_args.kwargs["status"] == "accepted"


async def test_scheduled_reply_failure_is_not_delivered_as_a_successful_reminder():
    now = datetime.now(UTC)
    deliveries, jobs = AsyncMock(), AsyncMock()
    deliveries.get.return_value = None
    runtime = AsyncMock()
    runtime.run_scheduled.return_value = AgentRuntimeResult(
        thread_id="thread",
        turn_id="turn",
        agent_version_id="version",
        termination=HarnessTermination.FINAL_RESPONSE,
        final_text="错误提示。",
        failure_code="structured_output_invalid",
    )
    service = SimpleNamespace(
        _max_lateness=timedelta(hours=1),
        _agent_timeout=timedelta(seconds=60),
        _preferences=SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(timezone="UTC", time_for=lambda kind: "09:00")
            )
        ),
        _checkins=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(has_weight=False))),
        _deliveries=deliveries,
        _jobs=jobs,
        _runtime=runtime,
        _traces=None,
        _policy=SimpleNamespace(
            evaluate=AsyncMock(
                return_value=SimpleNamespace(
                    allowed=True,
                    route=SimpleNamespace(
                        channel_id="channel", open_kfid="kf", external_userid="user"
                    ),
                )
            )
        ),
        _conversation_control=SimpleNamespace(
            ensure_agent_control=AsyncMock(return_value=WeComServiceState.SMART_ASSISTANT)
        ),
        _trigger=RoutineSchedulerService._trigger,
    )
    job = SimpleNamespace(
        id="job", scheduled_for=now, local_date=now.date(), user_id="user", kind=ReminderKind.WEIGHT
    )
    await RoutineSchedulerService._handle_traced(service, job, now=now, trace_id=None)
    deliveries.prepare.assert_not_called()
    assert jobs.finish.call_args.kwargs["result_code"] == "structured_output_invalid"
