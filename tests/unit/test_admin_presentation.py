from __future__ import annotations

import json
from datetime import UTC, datetime

from slim_guard.admin.presentation import context_sources, execution_summary, present_event
from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.db.models import AgentItemRecord


def test_model_tool_choice_is_presented_as_an_explicit_decision() -> None:
    presentation = present_event(
        {
            "event_type": "agent_item",
            "operation": "model_message",
            "details": {
                "call_index": 1,
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {"name": "record_weight", "arguments": {"weight_kg": 77.6}}
                    ]
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
                    "tool_calls": [
                        {"name": "set_body_profile", "arguments": {"height_value": 179}}
                    ]
                },
                "usage": {"input_tokens": 200, "output_tokens": 30},
            },
        }
    )

    assert presentation["title"] == "模型提取需要写入的长期记忆"
    assert "保存身高档案" in presentation["summary"]
    assert {"label": "调用用途", "value": "提取并核对长期记忆"} in presentation[
        "facts"
    ]


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
            {"operation": "model_message"},
        ]
    )

    assert summary == {
        "architecture": "harness",
        "model_call_count": 2,
        "tool_call_count": 1,
        "observation_count": 1,
            "context_snapshot_count": 1,
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
