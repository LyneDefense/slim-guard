from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from slim_guard.agent.runtime import AgentRuntimeRequest, AgentRuntimeResult
from slim_guard.harness.termination import HarnessTermination
from slim_guard.services.harness_reply_agent import HarnessReplyAgent, HarnessReplyError
from slim_guard.services.reply_agent import ReplyRequest
from slim_guard.tools.contracts import ToolExecutionMode

FIXED_NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)


class FakeAgentRuntime:
    def __init__(self, result: AgentRuntimeResult) -> None:
        self.result = result
        self.requests: list[AgentRuntimeRequest] = []

    async def run_user_message(self, request: AgentRuntimeRequest) -> AgentRuntimeResult:
        self.requests.append(request)
        return self.result


def runtime_result(
    *,
    termination: HarnessTermination = HarnessTermination.FINAL_RESPONSE,
    final_text: str | None = "  已记录 77.6kg。  ",
) -> AgentRuntimeResult:
    return AgentRuntimeResult(
        thread_id="thread-1",
        turn_id="turn-1",
        agent_version_id="agent-version-1",
        termination=termination,
        final_text=final_text,
        failure_code=None,
    )


async def test_adapter_maps_wecom_text_metadata_to_live_runtime_request() -> None:
    runtime = FakeAgentRuntime(runtime_result())
    adapter = HarnessReplyAgent(
        runtime=runtime,
        max_reply_chars=1500,
        turn_timeout_seconds=90,
        clock=lambda: FIXED_NOW,
    )

    reply = await adapter.generate_reply(
        ReplyRequest(
            user_id="internal-user-1",
            nickname="小明",
            text="今天空腹 77.6kg",
            source_message_id="wecom-message-1",
            channel_id="default",
            occurred_at=FIXED_NOW - timedelta(minutes=1),
        )
    )

    assert reply == "已记录 77.6kg。"
    assert runtime.requests == [
        AgentRuntimeRequest(
            user_id="internal-user-1",
            text="今天空腹 77.6kg",
            source_message_id="wecom-message-1",
            channel_id="default",
            occurred_at=FIXED_NOW - timedelta(minutes=1),
            deadline_at=FIXED_NOW + timedelta(seconds=90),
            execution_mode=ToolExecutionMode.LIVE,
        )
    ]


async def test_adapter_maps_wecom_image_to_runtime_request() -> None:
    runtime = FakeAgentRuntime(runtime_result())
    adapter = HarnessReplyAgent(
        runtime=runtime,
        max_reply_chars=1500,
        clock=lambda: FIXED_NOW,
    )

    reply = await adapter.generate_reply(
        ReplyRequest(
            user_id="internal-user-1",
            nickname=None,
            image_bytes=b"image",
            image_mime_type="image/png",
        )
    )

    assert reply == "已记录 77.6kg。"
    assert runtime.requests[0].text is None
    assert runtime.requests[0].image_bytes == b"image"
    assert runtime.requests[0].image_mime_type == "image/png"


async def test_adapter_does_not_deliver_non_final_harness_result() -> None:
    runtime = FakeAgentRuntime(
        runtime_result(
            termination=HarnessTermination.MAX_MODEL_CALLS,
            final_text=None,
        )
    )
    adapter = HarnessReplyAgent(
        runtime=runtime,
        max_reply_chars=1500,
        clock=lambda: FIXED_NOW,
    )

    with pytest.raises(HarnessReplyError, match="max_model_calls"):
        await adapter.generate_reply(
            ReplyRequest(
                user_id="internal-user-1",
                nickname=None,
                text="今天空腹 77.6kg",
            )
        )


async def test_adapter_delivers_user_confirmation_prompt() -> None:
    runtime = FakeAgentRuntime(
        runtime_result(
            termination=HarnessTermination.WAITING_USER_CONFIRMATION,
            final_text=None,
        )
    )
    adapter = HarnessReplyAgent(
        runtime=runtime,
        max_reply_chars=1500,
        clock=lambda: FIXED_NOW,
    )

    reply = await adapter.generate_reply(
        ReplyRequest(
            user_id="internal-user-1",
            nickname=None,
            text="清空我的个性化记忆",
        )
    )

    assert "需要你再次明确确认" in reply
    assert "确认执行" in reply
