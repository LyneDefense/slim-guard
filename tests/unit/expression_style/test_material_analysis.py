"""Short IDs, input-bound validation, and durable per-batch recovery."""

import copy
import json

import pytest

from slim_guard.expression_style.trainer.contracts import Material
from slim_guard.expression_style.trainer.materials import analyze_materials
from slim_guard.expression_style.trainer.structured_output import StructuredOutputError

from .test_structured_output import client_for, diagnostic, response


def materials(count=2):
    return tuple(
        Material(
            id=f"review:dbf0b395-6c5b-4b73-b840-{i:012d}",
            revision=1,
            user_input=f"谢谢{i}",
            original_response=f"谢谢你的第{i}次反馈，很高兴帮到你。",
            desired_response=f"谢谢你的第{i}次反馈。",
        )
        for i in range(count)
    )


def analysis(count=2):
    return {
        "items": [
            {"example_id": f"m{i + 1}", "role": "positive", "reason": "含义相同，去掉客套"}
            for i in range(count)
        ]
    }


@pytest.mark.parametrize(
    "defect,code",
    [
        ("missing", "missing_references"),
        ("duplicate", "duplicate_reference"),
        ("unknown", "unknown_reference"),
        ("duplicate_of", "invalid_duplicate_reference"),
        ("self_duplicate", "invalid_duplicate_reference"),
    ],
)
async def test_valid_json_with_wrong_input_references_is_repaired(defect, code):
    original = materials()
    invalid = analysis()
    if defect == "missing":
        invalid["items"].pop()
    elif defect == "duplicate":
        invalid["items"][1]["example_id"] = "m1"
    elif defect == "unknown":
        invalid["items"][1]["example_id"] = "review:dbf0b395-6b73-b840-000000000001"
    elif defect == "duplicate_of":
        invalid["items"][1]["duplicate_of"] = "m8"
    else:
        invalid["items"][1]["duplicate_of"] = "m2"
    raw = json.dumps(invalid)
    client, gateway, port = client_for([response(raw), response(json.dumps(analysis()))])
    result = await analyze_materials(client, original)
    assert [a.example_id for a in result] == [m.id for m in original]
    payload = json.loads(gateway.requests[0].messages[1].content)
    assert [m["id"] for m in payload] == ["m1", "m2"]
    assert all(m.id not in gateway.requests[0].messages[1].content for m in original)
    history = diagnostic(port)
    assert history["status"] == "repaired" and history["failures"][0]["raw_output"] == raw
    assert any(e["type"] == code for e in history["failures"][0]["errors"])
    assert code in gateway.requests[1].messages[-1].content
    assert history["contract"]["references"] == {"m1": original[0].id, "m2": original[1].id}
    assert [a["example_id"] for a in port.checkpoints["materials"]] == [m.id for m in original]


async def test_positive_without_target_is_feedback_to_model_not_a_bad_user_input():
    original = (
        materials(1)[0].model_copy(update={"desired_response": "", "correction_opinion": "少客套"}),
    )
    fixed = {"items": [{"example_id": "m1", "role": "negative", "reason": "只有纠正意见"}]}
    client, gateway, _ = client_for(
        [response(json.dumps(analysis(1))), response(json.dumps(fixed))]
    )
    assert (await analyze_materials(client, original))[0].role == "negative"
    assert "positive_without_target" in gateway.requests[1].messages[-1].content


async def test_reordered_results_and_duplicate_references_map_back_to_original_ids():
    original = materials()
    value = analysis()
    value["items"][1].update(role="duplicate", duplicate_of="m1")
    value["items"].reverse()
    client, _, _ = client_for([response(json.dumps(value))])
    result = await analyze_materials(client, original)
    assert [a.example_id for a in result] == [m.id for m in original]
    assert result[1].duplicate_of == original[0].id


async def test_failed_batch_can_resume_without_repeating_previous_successful_batch():
    original = materials(10)
    bad = analysis()
    bad["items"][1]["example_id"] = "m0"
    client, gateway, port = client_for(
        [
            response(json.dumps(analysis(8))),
            *[response(json.dumps(bad))] * 3,
            response(json.dumps(analysis())),
        ]
    )
    with pytest.raises(StructuredOutputError, match="输出校验失败.*3"):
        await analyze_materials(client, original)
    assert len(port.checkpoints["materials"]) == 8 and port.calls == 4
    assert len([key for key in port.checkpoints if key.startswith("model:")]) == 1
    history = diagnostic(port)
    assert len(history["failures"]) == 3 and history["status"] == "unresolved"
    result = await analyze_materials(client, original)
    assert [a.example_id for a in result] == [m.id for m in original]
    assert port.calls == 5 and port.tokens == 50
    assert len(json.loads(gateway.requests[4].messages[1].content)) == 2
    assert "unknown_reference" in gateway.requests[4].messages[-1].content
    assert diagnostic(port)["status"] == "repaired" and len(diagnostic(port)["failures"]) == 3


async def test_identical_batch_payloads_do_not_share_a_cache_across_different_identities():
    first = materials(8)
    second = tuple(m.model_copy(update={"id": m.id + "-other"}) for m in first)
    client, gateway, _ = client_for([response(json.dumps(analysis(8)))] * 2)
    result = await analyze_materials(client, (*first, *second))
    assert len(gateway.requests) == 2
    assert gateway.requests[0].messages == gateway.requests[1].messages
    assert [a.example_id for a in result] == [m.id for m in (*first, *second)]
    assert [a.duplicate_of for a in result[8:]] == [m.id for m in first]


async def test_invalid_saved_output_is_revalidated_not_trusted_on_resume():
    client, gateway, port = client_for([response(json.dumps(analysis()))] * 2)
    await analyze_materials(client, materials())
    key = next(key for key in port.checkpoints if key.startswith("model:"))
    invalid = copy.deepcopy(port.checkpoints[key])
    invalid["items"][1]["example_id"] = "m0"
    port.checkpoints[key] = invalid
    await analyze_materials(client, materials())
    assert (
        len(gateway.requests) == 2
        and "unknown_reference" in gateway.requests[1].messages[-1].content
    )
    assert diagnostic(port)["failures"][0]["execution_attempt"] == 0
    assert diagnostic(port)["status"] == "repaired"


async def test_duplicate_input_id_is_rejected_before_any_model_call():
    source = materials(1)[0]
    client, gateway, _ = client_for([])
    with pytest.raises(ValueError, match="输入素材 ID 重复"):
        await analyze_materials(client, (source, source))
    assert not gateway.requests
