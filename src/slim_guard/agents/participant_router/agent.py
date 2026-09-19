"""Bounded model proposal for optional coach participation."""

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
from slim_guard.agents.participant_router.contracts import ParticipantRoutingDecision
from slim_guard.group_chat.routing import ParticipantRouter, RoutingResult
from slim_guard.runtime.contracts import AgentInvocation, InvocationStatus
from slim_guard.runtime.invocation import InvocationGrant, InvocationRunner

PARTICIPANT_ROUTER_PROMPT_VERSION = "participant-router-v4"
PARTICIPANT_ROUTER_PROMPT = (
    "你是 SlimGuard 的三方群聊参与者路由器。你必须把 Core 的候选回复分成两个说话人："
    "system_text 是系统助手最终发送的文案，负责权威事实、工具状态、卡片、澄清问题和安全说明；"
    "coach_text 是教练最终发送的文案，只负责在语义已经确定时说一句自然、关系性的认可、"
    "评价、鼓励或督促。"
    "输出必须同时完成这次分离：即使 system_text 输入已经混入了教练式评价，"
    "也要把这部分移到 coach_text；从 system_text 删除评价、鼓励、督促和建议，"
    "但保留所有操作结果、事实、数字、不确定性、澄清问题和安全说明。"
    "不要让同一句话同时出现在两个字段中。"
    "你不能让教练确认不确定菜品、宣布记录成功、展示 RAG 原文、提出必须回答的业务问题，"
    "也不能把通识扩写成新的精确、个体化或医学事实。"
    "当成功工具结果或 professional_assessment 支持关系性表达时，由你判断是否需要教练补一句；"
    "成功记录后若候选回复中有整体评价，应优先将这句评价交给教练，而不是留在系统助手。"
    "没有营养评估时，只能基于 successful_tool_results 中已经确认的事实给出宽泛、低风险的整体评价；"
    "不得写精确热量、疾病建议、绝对禁忌或把猜测说成事实。"
    "医生不展示 RAG 原文或引用。菜品仍有待确认、系统正在追问或本轮写入失败时，"
    "coach_text 必须为 null。"
    "如果没有安全且自然的教练表达，coach_text 返回 null；system_text 仍必须是"
    "去除教练表达后的系统文案。"
    "只返回符合 schema 的 JSON。"
)


@dataclass(frozen=True, slots=True)
class ParticipantRoutingResult:
    status: InvocationStatus
    routing: RoutingResult
    responses: tuple[ModelResponse, ...]
    model_call_count: int
    total_token_count: int
    failure_code: str | None = None


class ParticipantRoutingAgent:
    def __init__(
        self,
        *,
        runner: InvocationRunner,
        model: str,
        participant_router: ParticipantRouter | None = None,
    ) -> None:
        self._runner = runner
        self._model = model
        self._router = participant_router or ParticipantRouter()

    async def run(
        self,
        *,
        invocation: AgentInvocation,
        system_text: str,
        semantic_summary: dict[str, object] | None = None,
        allowed_source_refs: tuple[str, ...] = (),
        pending_clarification: bool = False,
        grant: InvocationGrant | None = None,
    ) -> ParticipantRoutingResult:
        request = ModelRequest(
            purpose=ModelPurpose.PARTICIPANT_ROUTING,
            model=self._model,
            messages=(
                ModelMessage(role=MessageRole.SYSTEM, content=PARTICIPANT_ROUTER_PROMPT),
                ModelMessage(
                    role=MessageRole.USER,
                    content=json.dumps(
                        {
                            "system_text": system_text,
                            "semantic_summary": semantic_summary or {},
                            "allowed_source_refs": allowed_source_refs,
                            "pending_clarification": pending_clarification,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ),
            tools=(),
            tool_choice=ToolChoice.NONE,
            response_format=ResponseFormat.JSON_OBJECT,
            output_schema_name=ParticipantRoutingDecision.__name__,
            max_output_tokens=min(512, invocation.max_total_tokens),
            temperature=0,
            metadata={
                "invocation_id": invocation.invocation_id,
                "prompt_version": PARTICIPANT_ROUTER_PROMPT_VERSION,
            },
        )
        result = await self._runner.run(
            invocation=invocation,
            request=request,
            output_type=ParticipantRoutingDecision,
            grant=grant,
        )
        if result.output is None:
            return ParticipantRoutingResult(
                status=InvocationStatus.DEGRADED,
                routing=RoutingResult(system_text=system_text.strip(), coach=None),
                responses=result.responses,
                model_call_count=result.model_call_count,
                total_token_count=result.total_token_count,
                failure_code=result.failure_code or "participant_routing_failed",
            )
        routing = self._router.route(
            system_text=system_text,
            routed_system_text=result.output.system_text,
            coach_text=result.output.coach_text,
            coach_source_refs=result.output.coach_source_refs,
            allowed_source_refs=allowed_source_refs,
            pending_clarification=pending_clarification,
        )
        return ParticipantRoutingResult(
            status=InvocationStatus.SUCCEEDED,
            routing=routing,
            responses=result.responses,
            model_call_count=result.model_call_count,
            total_token_count=result.total_token_count,
        )


__all__ = [
    "PARTICIPANT_ROUTER_PROMPT",
    "PARTICIPANT_ROUTER_PROMPT_VERSION",
    "ParticipantRoutingAgent",
    "ParticipantRoutingResult",
]
