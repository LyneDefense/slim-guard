import copy
import json

import pytest

from slim_guard.agent_models.errors import ModelTransportError
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    ModelMessage,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
)
from slim_guard.expression_style.package import content_hash
from slim_guard.expression_style.trainer.client import TrainingClient, TrainingGateway
from slim_guard.expression_style.trainer.contracts import BuildBudget, Comparison
from slim_guard.expression_style.trainer.ports import BuildInterrupted
from slim_guard.expression_style.trainer.structured_output import StructuredOutputError

SCORES = {"fidelity": 5, "style_match": 4, "naturalness": 4, "appropriateness": 4}
VALID = {"winner": "tie", "reason": "无明确差异", "left_scores": SCORES, "right_scores": SCORES}


class MemoryPort:
    def __init__(self):
        self.checkpoints = {}
        self.events = []
        self.calls = 0
        self.tokens = 0
        self.interrupted = False

    async def load(self, key):
        return copy.deepcopy(self.checkpoints.get(key))

    async def save(self, key, value, *, stage, message):
        self.checkpoints[key] = copy.deepcopy(value)

    async def event(self, stage, message, **details):
        self.events.append({"stage": stage, "message": message, **details})

    async def reserve_call(self, *, max_calls, max_tokens):
        if self.interrupted:
            raise BuildInterrupted("cancelled")
        if self.calls >= max_calls or self.tokens >= max_tokens:
            raise ValueError("budget exhausted")
        self.calls += 1
        return {"calls": self.calls, "tokens": self.tokens}

    async def record_tokens(self, tokens):
        self.tokens += tokens


def response(raw):
    return ModelResponse(
        message=ModelMessage(role="assistant", content=raw),
        finish_reason="stop",
        usage=ModelUsage(total_tokens=10),
    )


def client_for(steps, port=None):
    port = port or MemoryPort()
    model = ScriptedModelGateway(steps)
    client = TrainingClient(TrainingGateway(model, port, BuildBudget()), "test-model")
    return client, model, port


async def ask(client):
    return await client.ask(
        Comparison, "比较表达", {"left": "甲", "right": "乙"}, stage="optimization"
    )


def diagnostic(port):
    return next(v for k, v in port.checkpoints.items() if k.startswith("validation:"))


@pytest.mark.parametrize(
    ("raw", "path", "kind"),
    [
        ("```json\n{}\n```", "$", "json_invalid"),
        ("", "$", "empty_output"),
        ("{}", "winner", "missing"),
        (json.dumps({**VALID, "winner": "candidate"}), "winner", "literal_error"),
        (
            json.dumps({**VALID, "left_scores": {**SCORES, "fidelity": 6}}),
            "left_scores.fidelity",
            "less_than_equal",
        ),
        (json.dumps({**VALID, "extra": True}), "extra", "extra_forbidden"),
    ],
)
async def test_bad_output_is_saved_then_repaired_with_field_error(raw, path, kind):
    client, model, port = client_for([response(raw), response(json.dumps(VALID))])
    assert (await ask(client)).winner == "tie"
    history = diagnostic(port)
    assert history["status"] == "repaired"
    assert len(history["failures"]) == 1
    failed = history["failures"][0]
    assert failed["raw_output"] == raw and failed["finish_reason"] == "stop"
    assert any(e["path"] == path and e["type"] == kind for e in failed["errors"])
    feedback = model.requests[1].messages[-1].content
    assert path in feedback and kind in feedback
    assert model.requests[0].messages == model.requests[1].messages[:2]
    assert port.calls == 2 and port.tokens == 20
    assert not any(raw and raw in e["message"] for e in port.events)
    assert await ask(client) == Comparison.model_validate(VALID)
    assert port.calls == 2  # Successful result retains the original cache key.


async def test_invalid_output_stops_at_three_and_resume_keeps_history_and_budget():
    client, model, port = client_for([response("{}")] * 3 + [response(json.dumps(VALID))])
    with pytest.raises(StructuredOutputError, match="Comparison.*3.*winner"):
        await ask(client)
    assert port.calls == 3 and port.tokens == 30
    assert diagnostic(port)["status"] == "unresolved"
    assert not any(key.startswith("model:") for key in port.checkpoints)
    assert len(diagnostic(port)["failures"]) == 3
    assert (await ask(client)).winner == "tie"  # Explicitly resumed execution.
    assert "validation_errors" in model.requests[3].messages[-1].content
    assert port.calls == 4 and port.tokens == 40
    assert len(diagnostic(port)["failures"]) == 3
    assert diagnostic(port)["status"] == "repaired"


async def test_retry_cannot_bypass_existing_call_budget():
    port = MemoryPort()
    port.calls = 349
    client, model, port = client_for([response("{}"), response(json.dumps(VALID))], port)
    with pytest.raises(ValueError, match="budget exhausted"):
        await ask(client)
    assert len(model.requests) == 1 and port.calls == 350
    assert diagnostic(port)["status"] == "unresolved"


async def test_retry_cannot_bypass_cancellation():
    class CancellingPort(MemoryPort):
        async def save(self, key, value, **kwargs):
            await super().save(key, value, **kwargs)
            self.interrupted = True

    client, model, port = client_for(
        [response("{}"), response(json.dumps(VALID))], CancellingPort()
    )
    with pytest.raises(BuildInterrupted):
        await ask(client)
    assert len(model.requests) == 1 and diagnostic(port)["failures"]


async def test_transport_error_is_not_mislabelled_or_retried_as_format_error():
    client, model, port = client_for([ModelTransportError("offline")])
    with pytest.raises(ModelTransportError):
        await ask(client)
    assert len(model.requests) == 1
    assert not port.checkpoints


async def test_legacy_valid_cache_is_reused_without_model_call():
    client, model, port = client_for([])
    key = "model:" + content_hash(
        {
            "model": "test-model",
            "schema": Comparison.model_json_schema(),
            "prompt": "比较表达",
            "payload": {"left": "甲", "right": "乙"},
        }
    )
    port.checkpoints[key] = VALID
    assert (await ask(client)).winner == "tie"
    assert not model.requests and port.calls == 0


async def test_long_failed_raw_output_is_preserved_but_repair_context_is_bounded():
    raw = "错" * 20_000
    client, model, port = client_for([response(raw), response(json.dumps(VALID))])
    await ask(client)
    assert diagnostic(port)["failures"][0]["raw_output"] == raw
    assert len(model.requests[1].messages[-1].content) < 14_000
    assert '"output_excerpt_only":true' in model.requests[1].messages[-1].content


async def test_unexpected_tool_call_is_preserved_never_executed_and_repaired():
    bad = ModelResponse(
        message=ModelMessage(
            role="assistant", tool_calls=(NormalizedToolCall(id="1", name="bad"),)
        ),
        finish_reason="tool_calls",
        usage=ModelUsage(total_tokens=10),
    )
    client, model, port = client_for([bad, response(json.dumps(VALID))])
    await ask(client)
    failure = diagnostic(port)["failures"][0]
    assert failure["raw_output"] is None
    assert failure["tool_calls"][0]["name"] == "bad"
    assert failure["errors"][0]["type"] == "unexpected_tool_calls"
    assert all(
        not request.tools and request.tool_choice.value == "none" for request in model.requests
    )


async def test_token_budget_is_not_reset_by_retry():
    port = MemoryPort()
    port.tokens = 999_990
    client, model, port = client_for([response("{}"), response(json.dumps(VALID))], port)
    with pytest.raises(ValueError, match="budget exhausted"):
        await ask(client)
    assert port.tokens == 1_000_000 and len(model.requests) == 1
    assert diagnostic(port)["status"] == "unresolved"


async def test_cached_success_repairs_stale_diagnostic_after_interrupted_save():
    client, model, port = client_for([response("{}"), response(json.dumps(VALID))])
    await ask(client)
    key = next(k for k in port.checkpoints if k.startswith("validation:"))
    port.checkpoints[key]["status"] = "unresolved"
    await ask(client)
    assert port.checkpoints[key]["status"] == "repaired"
    assert len(model.requests) == 2


async def test_truncated_response_records_length_finish_reason_for_repair():
    truncated = response('{"winner":').model_copy(update={"finish_reason": "length"})
    client, model, port = client_for([truncated, response(json.dumps(VALID))])
    await ask(client)
    failure = diagnostic(port)["failures"][0]
    assert failure["finish_reason"] == "length" and failure["errors"][0]["type"] == "json_invalid"
    assert '"finish_reason":"length"' in model.requests[1].messages[-1].content
