from __future__ import annotations

import json
from datetime import UTC, datetime

from slim_guard.admin.presentation import context_sources, execution_summary, present_event
from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.db.models import AgentItemRecord


def test_workflow_trace_events_are_presented_in_plain_language() -> None:
    cases = [
        (
            "invocation_started",
            {
                "invocation_id": "invocation-1",
                "agent_role": "nutrition_expert",
                "agent_version": "nutrition-v1",
                "attempt": 1,
                "parent_invocation_id": "invocation-parent",
                "input_artifact_ids": ["artifact-input"],
                "allowed_tool_names": ["lookup_nutrition_guidance"],
                "privacy_scopes": ["health_records"],
                "reason_summary": "需要核对饮食结论",
                "started_at": "2026-09-04T08:00:00+00:00",
            },
            "decision",
            "启动营养专业 Agent",
            "只提供已声明的信息范围",
        ),
        (
            "invocation_result",
            {
                "invocation_id": "invocation-1",
                "status": "succeeded",
                "output_artifact_id": "artifact-output",
                "model_call_count": 1,
                "tool_call_count": 0,
                "total_token_count": 42,
                "failure_code": None,
                "completed_at": "2026-09-04T08:00:01+00:00",
            },
            "observation",
            "Agent 调用结果：成功",
            "类型校验",
        ),
        (
            "artifact_created",
            {
                "artifact_id": "artifact-output",
                "artifact_type": "assessment",
                "producer_role": "nutrition_expert",
                "schema_version": "1",
                "parent_artifact_ids": ["artifact-input"],
                "payload_sha256": "a" * 64,
            },
            "observation",
            "生成结构化产物：专业评估",
            "不复制敏感正文",
        ),
        (
            "workflow_transition",
            {
                "from_node": "nutrition_expert",
                "to_node": "response_style",
                "transition_type": "route",
                "reason_code": "assessment_ready",
                "attempt": 1,
            },
            "decision",
            "工作流进入：表达风格处理",
            "营养专业判断",
        ),
        (
            "response_adopted",
            {"artifact_id": "artifact-output", "mode": "shadow", "final": False},
            "output",
            "采用候选回复",
            "尚未作为最终回复发送",
        ),
        (
            "response_degraded",
            {
                "artifact_id": "artifact-output",
                "reason_code": "style_timeout",
                "fallback_type": "legacy_response",
            },
            "output",
            "回复进入降级路径",
            "现有回复",
        ),
    ]

    for operation, details, stage, title, summary_text in cases:
        presentation = present_event(
            {
                "event_type": "agent_item",
                "operation": operation,
                "details": details,
            }
        )

        assert presentation["stage"] == stage
        assert presentation["title"] == title
        assert summary_text in presentation["summary"]


def test_artifact_presentation_never_exposes_an_unexpected_payload() -> None:
    sensitive_text = "用户的完整健康信息"
    presentation = present_event(
        {
            "event_type": "agent_item",
            "operation": "artifact_created",
            "details": {
                "artifact_id": "artifact-output",
                "artifact_type": "assessment",
                "producer_role": "nutrition_expert",
                "schema_version": "1",
                "parent_artifact_ids": [],
                "payload_sha256": "a" * 64,
                "payload": {"raw_response": sensitive_text},
            },
        }
    )

    assert sensitive_text not in json.dumps(presentation, ensure_ascii=False)


def test_model_tool_choice_is_presented_as_an_explicit_decision() -> None:
    presentation = present_event(
        {
            "event_type": "agent_item",
            "operation": "model_message",
            "details": {
                "call_index": 1,
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{"name": "record_weight", "arguments": {"weight_kg": 77.6}}]
                },
                "usage": {"input_tokens": 120, "output_tokens": 20},
            },
        }
    )

    assert presentation["stage"] == "decision"
    assert presentation["title"] == "模型选择下一步动作"
    assert "记录体重" in presentation["summary"]
    assert "隐藏思维" in presentation["summary"]


def test_memory_ingestion_model_is_presented_as_a_separate_stage() -> None:
    presentation = present_event(
        {
            "event_type": "agent_item",
            "operation": "model_message",
            "details": {
                "purpose": "memory_ingestion",
                "call_index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{"name": "set_body_profile", "arguments": {"height_value": 179}}]
                },
                "usage": {"input_tokens": 200, "output_tokens": 30},
            },
        }
    )

    assert presentation["title"] == "模型提取需要写入的长期记忆"
    assert "保存身高档案" in presentation["summary"]
    assert {"label": "调用用途", "value": "提取并核对长期记忆"} in presentation["facts"]


def test_memory_recall_is_presented_in_plain_language() -> None:
    presentation = present_event(
        {
            "event_type": "agent_item",
            "operation": "memory_recall",
            "details": {
                "engine_status": "succeeded",
                "candidate_count": 9,
                "engine_candidate_count": 4,
                "selected_count": 2,
                "degraded": False,
                "reason_summary": "用户正在询问身高和目标体重。",
            },
        }
    )

    assert presentation["title"] == "筛选本轮相关记忆"
    assert "9 条数据库候选" in presentation["summary"]
    assert "选择了 2 条" in presentation["summary"]


def test_memory_ingestion_receipt_explains_an_update_in_plain_language() -> None:
    presentation = present_event(
        {
            "event_type": "agent_item",
            "operation": "memory_ingestion",
            "details": {
                "model_called": True,
                "proposed_count": 1,
                "succeeded_count": 1,
                "changed_count": 1,
                "failure_codes": [],
                "changes": [
                    {
                        "action": "updated",
                        "key": "profile.height",
                        "previous_value": {"millimeters": 1790},
                        "current_value": {"millimeters": 1780},
                    }
                ],
            },
        }
    )

    assert presentation["title"] == "核对本轮长期记忆变更"
    assert "身高从 179 cm 更新为 178 cm" in presentation["summary"]
    assert {
        "label": "身高",
        "value": "179 cm → 178 cm",
        "detail": "已更新",
    } in presentation["facts"]


def test_tool_result_is_presented_as_an_observation() -> None:
    presentation = present_event(
        {
            "event_type": "agent_item",
            "operation": "tool_result",
            "details": {
                "execution": {
                    "tool_name": "set_weight_goal",
                    "policy_decision": "allow",
                    "result": {
                        "status": "succeeded",
                        "output": {"target_weight_kg": 70},
                    },
                }
            },
        }
    )

    assert presentation["stage"] == "observation"
    assert presentation["title"] == "观察到工具结果：设置目标体重"
    assert {"label": "目标体重（kg）", "value": "70"} in presentation["facts"]


def test_context_snapshot_explains_memory_without_exposing_prompt_as_summary() -> None:
    presentation = present_event(
        {
            "event_type": "agent_item",
            "operation": "context_snapshot",
            "details": {
                "request": {
                    "model": "glm-test",
                    "messages": [
                        {"role": "system", "content": "system secret"},
                        {"role": "system", "content": "权威用户事实：{}"},
                        {"role": "system", "content": "近期对话工作记忆：{}"},
                        {"role": "user", "content": "继续"},
                    ],
                },
                "allowed_tool_names": ["set_weight_goal"],
            },
        }
    )

    assert "长期记忆和权威健康事实" in presentation["summary"]
    assert "最近对话工作记忆" in presentation["summary"]
    assert "system secret" not in presentation["summary"]


def test_execution_summary_counts_harness_actions() -> None:
    summary = execution_summary(
        [
            {"operation": "context_snapshot"},
            {"operation": "model_message"},
            {"operation": "tool_call"},
            {"operation": "tool_result"},
            {"operation": "memory_ingestion"},
            {"operation": "model_message"},
        ]
    )

    assert summary == {
        "architecture": "harness",
        "model_call_count": 2,
        "tool_call_count": 1,
        "observation_count": 1,
        "context_snapshot_count": 1,
        "memory_ingestion_count": 1,
        "memory_recall_count": 0,
    }


def test_context_sources_distinguish_durable_memory_records_and_dialogue() -> None:
    sources = context_sources(
        [
            {
                "operation": "context_snapshot",
                "details": {
                    "request": {
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "权威用户事实（只读）："
                                    '{"profile_memory":[{"key":"goal.target_weight",'
                                    '"value":{"grams":65000},"stale":false}],'
                                    '"recent_weights":[{"weight_kg":"77",'
                                    '"measured_at":"2026-08-31T08:00:00+00:00"}]}'
                                ),
                            },
                            {
                                "role": "system",
                                "content": (
                                    "近期对话工作记忆（非权威）："
                                    '{"recent_dialogue":[{"messages":['
                                    '{"role":"user","content":"我身高179cm"},'
                                    '{"role":"assistant","content":"收到"}]}]}'
                                ),
                            },
                        ]
                    }
                },
            }
        ]
    )

    assert sources[0]["title"] == "长期记忆"
    assert sources[0]["items"][0]["value"] == "65 kg"
    assert sources[1]["title"] == "权威健康记录"
    assert sources[1]["items"][0]["value"] == "77 kg"
    assert sources[2]["title"] == "最近对话 Working Memory"
    assert sources[2]["items"][0]["value"] == "我身高179cm"
    assert sources[2]["items"][0]["detail"] == "对话原文，不是长期记忆"


def test_context_sources_show_current_turn_memory_receipt() -> None:
    sources = context_sources(
        [
            {
                "operation": "context_snapshot",
                "details": {
                    "request": {
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "权威用户事实（只读）："
                                    '{"current_turn_memory_receipt":{'
                                    '"authority":"memory_tool_result","changes":[{'
                                    '"action":"updated","key":"profile.height",'
                                    '"previous_value":{"millimeters":1790},'
                                    '"current_value":{"millimeters":1780}}]}}'
                                ),
                            }
                        ]
                    }
                },
            }
        ]
    )

    receipt = next(source for source in sources if source["kind"] == "current_turn_memory_receipt")
    assert receipt["title"] == "本轮记忆变更"
    assert receipt["items"] == [
        {
            "label": "身高",
            "value": "179 cm → 178 cm",
            "detail": "本轮已更新",
        }
    ]


def test_agent_message_does_not_duplicate_final_text() -> None:
    presentation = present_event(
        {
            "event_type": "agent_item",
            "operation": "agent_message",
            "details": {"text": "这是一段最终回复"},
        }
    )

    assert "这是一段最终回复" not in presentation["summary"]
    assert "页面顶部展示" in presentation["summary"]


def test_agent_item_uses_persisted_operation_timing_and_not_fake_zero() -> None:
    item = AgentItemRecord(
        id="item-1",
        thread_id="thread-1",
        turn_id="turn-1",
        sequence=1,
        item_type="model_message",
        status="completed",
        payload_json=json.dumps(
            {
                "started_at": "2026-08-31T08:00:00+00:00",
                "completed_at": "2026-08-31T08:00:01.250000+00:00",
            }
        ),
        created_at=datetime(2026, 8, 31, 8, 0, 2, tzinfo=UTC),
    )
    view = AdminQueryRepository._item_view(item, None)

    assert view["started_at"] == datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
    assert view["duration_ms"] == 1250

    historical = AgentItemRecord(
        id="item-2",
        thread_id="thread-1",
        turn_id="turn-1",
        sequence=2,
        item_type="agent_message",
        status="completed",
        payload_json='{"text":"完成"}',
        created_at=datetime(2026, 8, 31, 8, 0, 3, tzinfo=UTC),
    )
    historical_view = AdminQueryRepository._item_view(historical, None)

    assert historical_view["completed_at"] is None
    assert historical_view["duration_ms"] is None
