from __future__ import annotations

from slim_guard.admin.presentation import execution_summary, present_event


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
    }
