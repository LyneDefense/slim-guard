from datetime import UTC, datetime, timedelta

import pytest

from slim_guard.admin.calls.projection import business_status, duration, project, summary

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def trace(**changes):
    return {
        "trace": {
            "id": "trace",
            "created_at": NOW,
            "completed_at": NOW + timedelta(seconds=9),
            "generation_status": "succeeded",
            "delivery_status": "accepted",
            **changes,
        },
        "input": {"messages": [{"item_id": "source", "text": "晚餐"}], "images": []},
        "timeline": [],
        "invocations": [],
        "artifacts": [],
        "tool_executions": [],
    }


def receipt(status, identity="operation-1"):
    return {
        "operation": "tool_result",
        "details": {
            "execution": {
                "mutation_kind": "health_record",
                "result": {"operation": {"operation_id": identity, "status": status}},
            }
        },
    }


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["succeeded"], "succeeded"),
        (["unknown"], "unknown"),
        (["not_applied"], "failed"),
        (["succeeded", "not_applied"], "partial"),
    ],
)
def test_business_uses_receipts_not_model_prose(states, expected):
    data = trace()
    data["timeline"] = [receipt(s, str(i)) for i, s in enumerate(states)]
    assert business_status(data) == expected


def test_status_query_resolves_same_operation_without_counting_query_as_write():
    data = trace()
    data["timeline"] = [
        receipt("unknown"),
        {
            "operation": "tool_result",
            "details": {
                "execution": {
                    "mutation_kind": "none",
                    "result": {
                        "output": {
                            "operation": {"operation_id": "operation-1", "status": "succeeded"}
                        }
                    },
                }
            },
        },
    ]
    assert business_status(data) == "succeeded"


def test_saved_business_and_failed_coach_is_partial_even_if_api_accepted():
    data = trace(generation_status="degraded")
    data["timeline"] = [receipt("succeeded")]
    result = summary(data, [])
    assert result["outcome"] == "partial"
    assert result["reply_status"] == "failed"
    assert result["client_status"] == "not_reported"


def test_final_messages_not_rejected_candidates_are_presented():
    data = trace()
    data["artifacts"] = [
        {
            "artifact_id": "candidate",
            "artifact_type": "coach_reply",
            "payload": {"output": {"text": "不应发送的候选"}},
        },
        {
            "artifact_id": "final",
            "artifact_type": "outgoing_messages",
            "payload": {"messages": [{"participant": "coach", "text": "采用稿"}]},
        },
    ]
    result = project(data, [], [])
    assert result["messages"] == [{"participant": "coach", "text": "采用稿"}]
    assert result["summary"]["reply_status"] == "succeeded"


def test_exact_usage_rows_count_once_not_invocation_rollups_and_background():
    data = trace()
    data["invocations"] = [{"invocation_id": "core", "agent_role": "core", "model_call_count": 99}]
    calls = [
        {"id": "one", "invocation_id": "core", "status": "succeeded"},
        {"id": "two", "invocation_id": "core", "status": "failed"},
        {"id": "background", "category": "background", "job_id": "job"},
    ]
    result = project(data, calls, [])
    assert result["summary"]["model_calls"] == 2
    assert result["summary"]["background_model_calls"] == 1
    assert len([n for n in result["nodes"] if n["kind"] == "model"]) == 2
    assert result["summary"]["outcome"] == "recovered"


def test_missing_ledger_is_unknown_not_fake_zero():
    data = trace()
    data["invocations"] = [{"invocation_id": "core", "agent_role": "core", "model_call_count": 3}]
    assert summary(data, [])["model_calls"] is None


def test_models_inside_tools_have_tool_parent_and_own_timing():
    data = trace()
    data["invocations"] = [{"invocation_id": "core", "agent_role": "core"}]
    data["tool_executions"] = [{"tool_call_id": "vision", "tool_name": "inspect_image"}]
    result = project(
        data,
        [
            {
                "id": "call",
                "tool_call_id": "vision",
                "invocation_id": "core",
                "started_at": NOW,
                "completed_at": NOW + timedelta(seconds=2),
            }
        ],
        [],
    )
    request = next(n for n in result["nodes"] if n["id"] == "model:call")
    assert request["parent_id"] == "tool:vision"
    assert request["duration_ms"] == 2000


def test_snapshot_attaches_only_by_actual_call_id_and_missing_io_stays_missing():
    data = trace()
    data["timeline"] = [
        {
            "operation": "model_exchange",
            "details": {"call_id": "one", "input": {"conversation": "exact"}, "output": "answer"},
        }
    ]
    result = project(data, [{"id": "one"}, {"id": "two"}], [])
    first = next(n for n in result["nodes"] if n["id"] == "model:one")
    second = next(n for n in result["nodes"] if n["id"] == "model:two")
    assert first["input"] == {"conversation": "exact"}
    assert second["input"] is None


def test_review_has_chronological_root_and_retains_causal_link():
    data = trace()
    data["invocations"] = [
        {"invocation_id": "coach", "agent_role": "coach_response"},
        {"invocation_id": "review", "agent_role": "coach_review", "parent_invocation_id": "coach"},
    ]
    result = project(data, [], [])
    review = next(n for n in result["nodes"] if n["id"] == "review")
    assert review["parent_id"] is None
    assert review["raw"]["parent_invocation_id"] == "coach"


@pytest.mark.parametrize(
    "dimension,expected",
    [("expression", "warning"), ("role", "rejected"), ("fidelity", "rejected")],
)
def test_style_advisory_is_not_presented_as_a_factual_or_generation_failure(dimension, expected):
    data = trace()
    data["invocations"] = [
        {
            "invocation_id": "review",
            "agent_role": "coach_review",
            "status": "succeeded",
            "output_artifact_id": "check",
        }
    ]
    data["artifacts"] = [
        {
            "artifact_id": "check",
            "invocation_id": "review",
            "artifact_type": "coach_grounding_review",
            "payload": {
                "output": {
                    "fidelity_passed": dimension != "fidelity",
                    "role_passed": dimension != "role",
                    "expression_passed": dimension != "expression",
                    "issues": [{"dimension": dimension}],
                }
            },
        }
    ]
    result = project(data, [], [])
    review = next(node for node in result["nodes"] if node["id"] == "review")
    assert review["status"] == expected


def test_waiting_for_user_is_not_failure():
    data = trace()
    data["timeline"] = [
        {
            "operation": "agent_message",
            "details": {"participant": "system_assistant", "requires_response": True},
        }
    ]
    assert summary(data, [])["outcome"] == "waiting"


def test_sanitized_final_artifact_does_not_hide_persisted_messages():
    data = trace()
    data["artifacts"] = [
        {
            "artifact_id": "final",
            "artifact_type": "outgoing_messages",
            "payload": {"status": "succeeded"},
        }
    ]
    data["timeline"] = [
        {"operation": "agent_message", "details": {"participant": "coach", "text": "实际采用稿"}}
    ]
    assert project(data, [], [])["messages"][0]["text"] == "实际采用稿"


def test_background_failure_does_not_change_successful_online_outcome():
    assert (
        summary(trace(), [{"id": "bg", "job_id": "job", "status": "failed"}])["outcome"]
        == "completed"
    )


def test_read_only_tool_is_not_a_failed_or_unknown_business_write():
    data = trace()
    data["tool_executions"] = [{"tool_call_id": "read"}]
    data["timeline"] = [
        {
            "operation": "tool_result",
            "details": {"execution": {"mutation_kind": "none", "result": {"status": "succeeded"}}},
        }
    ]
    assert business_status(data) == "none"


def test_reviewer_execution_success_is_not_review_approval():
    data = trace()
    data["invocations"] = [
        {
            "invocation_id": "review",
            "agent_role": "coach_review",
            "status": "succeeded",
            "output_artifact_id": "check",
        }
    ]
    data["artifacts"] = [
        {
            "artifact_id": "check",
            "artifact_type": "coach_grounding_review",
            "invocation_id": "review",
            "payload": {"output": {"role_passed": False, "issues": []}},
        }
    ]
    node = next(n for n in project(data, [], [])["nodes"] if n["id"] == "review")
    assert node["status"] == "rejected"


@pytest.mark.parametrize("end", [None, "invalid"])
def test_absent_timing_is_not_zero(end):
    assert duration(NOW, end) is None
