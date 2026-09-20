"""Conversation Agent for the system-assistant/coach group chat."""

from __future__ import annotations

import json
from dataclasses import dataclass

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ResponseFormat,
    ToolChoice,
)
from slim_guard.agents.conversation.contracts import ConversationDecision
from slim_guard.group_chat.routing import ConversationRouter, RoutingResult
from slim_guard.runtime.contracts import AgentInvocation, InvocationStatus
from slim_guard.runtime.invocation import InvocationGrant, InvocationRunner

CONVERSATION_PROMPT_VERSION = "conversation-v1"
CONVERSATION_PROMPT = (
    "你是 SlimGuard 的 Conversation Agent，负责把已经验证的本轮语义结果组织成三方群聊消息。"
    "system_text 是系统助手消息，负责操作状态、权威事实、必要澄清、安全说明；"
    "coach_text 是可选的教练消息，只能表达已经被允许的认可、评价、鼓励或督促。"
    "你必须把 Core 候选中的系统事实和教练表达分开，不得让两字段重复同一句话。"
    "当已确认的用户行为、餐食、运动或目标进展中存在自然且有依据的关系性表达时，"
    "可以提出一句简短教练消息；不要因为每次工具成功就机械发言，也不要为了沉默而吞掉明确的评价语义。"
    "不得改变工具状态、事实、数字、日期、不确定性或安全结论；不得让教练宣布工具成功、"
    "确认不确定菜品、提出必须回答的业务问题或展示 RAG 原文。"
    "如果存在待确认事项、必要澄清或本轮写入失败，coach_text 必须为 null。"
    "如果没有安全且自然的教练表达，也返回 null；system_text 仍必须完整、真实、面向用户。"
    "只返回符合 schema 的 JSON。"
)


@dataclass(frozen=True, slots=True)
class ConversationAgentResult:
    status: InvocationStatus
    routing: RoutingResult
    model_system_text: str | None
    responses: tuple[ModelResponse, ...]
    model_call_count: int
    total_token_count: int
    failure_code: str | None = None


class ConversationAgent:
    """Plan user-visible system and coach messages from verified semantics."""

    def __init__(
        self,
        *,
        runner: InvocationRunner,
        model: str,
        conversation_router: ConversationRouter | None = None,
    ) -> None:
        self._runner = runner
        self._model = model
        self._router = conversation_router or ConversationRouter()

    async def run(
        self,
        *,
        invocation: AgentInvocation,
        system_text: str,
        semantic_summary: dict[str, object] | None = None,
        allowed_source_refs: tuple[str, ...] = (),
        pending_clarification: bool = False,
        tool_failed: bool = False,
        grant: InvocationGrant | None = None,
    ) -> ConversationAgentResult:
        request = ModelRequest(
            purpose=ModelPurpose.CONVERSATION,
            model=self._model,
            messages=(
                ModelMessage(role=MessageRole.SYSTEM, content=CONVERSATION_PROMPT),
                ModelMessage(
                    role=MessageRole.USER,
                    content=json.dumps(
                        {
                            "system_candidate": system_text,
                            "semantic_summary": semantic_summary or {},
                            "allowed_source_refs": allowed_source_refs,
                            "pending_clarification": pending_clarification,
                            "tool_failed": tool_failed,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ),
            tools=(),
            tool_choice=ToolChoice.NONE,
            response_format=ResponseFormat.JSON_OBJECT,
            output_schema_name=ConversationDecision.__name__,
            max_output_tokens=min(512, invocation.max_total_tokens),
            temperature=0,
            metadata={
                "invocation_id": invocation.invocation_id,
                "prompt_version": CONVERSATION_PROMPT_VERSION,
            },
        )
        result = await self._runner.run(
            invocation=invocation,
            request=request,
            output_type=ConversationDecision,
            grant=grant,
        )
        if result.output is None:
            return ConversationAgentResult(
                status=InvocationStatus.DEGRADED,
                routing=RoutingResult(system_text=system_text.strip(), coach=None),
                model_system_text=None,
                responses=result.responses,
                model_call_count=result.model_call_count,
                total_token_count=result.total_token_count,
                failure_code=result.failure_code or "conversation_planning_failed",
            )
        routing = self._router.route(
            system_text=system_text,
            routed_system_text=result.output.system_text,
            coach_text=result.output.coach_text,
            coach_source_refs=result.output.coach_source_refs,
            allowed_source_refs=allowed_source_refs,
            pending_clarification=pending_clarification,
            tool_failed=tool_failed,
        )
        return ConversationAgentResult(
            status=InvocationStatus.SUCCEEDED,
            routing=routing,
            model_system_text=result.output.system_text,
            responses=result.responses,
            model_call_count=result.model_call_count,
            total_token_count=result.total_token_count,
        )


__all__ = [
    "CONVERSATION_PROMPT",
    "CONVERSATION_PROMPT_VERSION",
    "ConversationAgent",
    "ConversationAgentResult",
]
