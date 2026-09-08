"""All conversation/example fixtures here are synthetic TEST DATA, not doctor exports."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse, ToolChoice
from slim_guard.agents.contracts import ResponseContentBlock, ResponsePlan, StyledResponse
from slim_guard.style_corpus import (
    CorpusReview,
    ExportMessage,
    OfflineStyleCorpus,
    StyleAssetBundle,
    StyleEvalCase,
    merge_messages,
    parse_export,
    redact,
)


@pytest.fixture
def corpus(tmp_path: Path) -> Iterator[OfflineStyleCorpus]:
    repository = OfflineStyleCorpus(tmp_path / "offline-test-corpus.sqlite")
    yield repository
    repository.close()


def judgment_response(**updates: object) -> ModelResponse:
    return response(
        {
            "related": True,
            "communication_act": "acknowledge",
            "example_text": "[测试示例] 收到了，[既定事实]。",
            "tone_rules": ["测试规则：用简洁短句确认既定事实。"],
            "reason": "测试数据：回复针对前文记录。",
            **updates,
        }
    )


def response(payload: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)
        )
    )


def eval_response(**updates: object) -> ModelResponse:
    return response(
        {
            "style_match": True,
            "semantic_fidelity": True,
            "privacy_preserved": True,
            "no_impersonation_or_abuse": True,
            "no_added_professional_claims": True,
            "reason": "测试评估：表达与原计划一致。",
            **updates,
        }
    )


def messages() -> tuple[ExportMessage, ...]:
    return (
        ExportMessage(sender="测试客户甲", text="测试：今天已经完成记录。手机号13800138000。"),
        ExportMessage(sender="测试医生账号", text="测试：收到。"),
        ExportMessage(sender="测试医生账号", text="测试：下次继续记录。"),
    )


MAPPING = {"测试客户甲": "测试参与者", "测试医生账号": "章之文"}


def approval(**updates: object) -> CorpusReview:
    return CorpusReview.model_validate(
        {
            "actor": "test-human-reviewer",
            "decision": "approve",
            "note": "仅用于测试的人工审核记录。",
            "privacy_confirmed": True,
            "expression_only_confirmed": True,
            **updates,
        }
    )


async def approved_bundle(corpus: OfflineStyleCorpus) -> StyleAssetBundle:
    candidates = await corpus.import_messages(
        messages(),
        sender_mapping=MAPPING,
        gateway=ScriptedModelGateway([judgment_response()]),
        model="test-model",
    )
    corpus.review(candidates[0].candidate_id, approval())
    return corpus.build_bundle(
        profile_id="test_style", version="test_style_v1", display_name="测试风格候选"
    )


def eval_case(*, profile_version: str = "test_style_v1") -> StyleEvalCase:
    return StyleEvalCase(
        case_id="test-case-1",
        response_plan=ResponsePlan(
            communication_act="acknowledge",
            content_blocks=(
                ResponseContentBlock(
                    block_id="test-social",
                    kind="social_act",
                    text="测试：收到你的消息。",
                ),
            ),
        ),
        styled_response=StyledResponse(
            text="测试：收到你的消息。",
            used_block_ids=("test-social",),
            style_profile_version=profile_version,
        ),
    )


def test_parse_json_and_tsv_then_merge_only_same_sender_and_conversation() -> None:
    parsed = parse_export(
        json.dumps({"messages": [item.model_dump() for item in messages()]}), format="json"
    )
    merged = merge_messages(parsed)
    assert len(merged) == 2
    assert merged[1].text == "测试：收到。\n测试：下次继续记录。"
    assert parse_export("测试甲\t测试内容\n测试乙\t测试回复", format="text")[1].sender == "测试乙"
    separate = merge_messages(
        (
            messages()[1],
            messages()[1].model_copy(update={"conversation_id": "another-test-conversation"}),
        )
    )
    assert len(separate) == 2


@pytest.mark.parametrize("text,format", [("[]", "json"), ("{}", "json"), ("missing tab", "text")])
def test_malformed_exports_fail_explicitly(text: str, format: str) -> None:
    with pytest.raises(ValueError):
        parse_export(text, format=format)  # type: ignore[arg-type]


async def test_import_redacts_before_model_and_storage_and_requires_explicit_sender_mapping(
    corpus: OfflineStyleCorpus,
) -> None:
    gateway = ScriptedModelGateway([judgment_response()])
    with pytest.raises(ValueError, match="Every sender"):
        await corpus.import_messages(
            messages(),
            sender_mapping={"测试医生账号": "章之文"},
            gateway=gateway,
            model="test-model",
        )
    assert gateway.requests == []
    candidates = await corpus.import_messages(
        messages(), sender_mapping=MAPPING, gateway=gateway, model="test-model"
    )
    assert len(candidates) == 1
    assert "13800138000" not in candidates[0].model_dump_json()
    assert "13800138000" not in gateway.requests[0].model_dump_json()
    assert gateway.requests[0].tool_choice is ToolChoice.NONE
    assert gateway.requests[0].tools == ()
    with pytest.raises(ValueError, match="No human-approved"):
        corpus.build_bundle(profile_id="test", version="test_v1", display_name="测试")


def test_redaction_handles_explicit_names_contacts_ids_and_addresses() -> None:
    text = "测试姓名 测试街道1号 test@example.com 13800138000 110101199001011234 微信:wx_test"
    cleaned = redact(text, private_terms=("测试姓名", "测试街道1号"))
    for private_value in (
        "测试姓名",
        "测试街道1号",
        "test@example.com",
        "13800138000",
        "110101199001011234",
        "wx_test",
    ):
        assert private_value not in cleaned


async def test_unrelated_adjacent_reply_cannot_be_human_approved(
    corpus: OfflineStyleCorpus,
) -> None:
    candidates = await corpus.import_messages(
        messages(),
        sender_mapping=MAPPING,
        gateway=ScriptedModelGateway([judgment_response(related=False)]),
        model="test-model",
    )
    with pytest.raises(ValueError, match="relevance"):
        corpus.review(candidates[0].candidate_id, approval())


@pytest.mark.parametrize("field", ["privacy_confirmed", "expression_only_confirmed"])
async def test_approval_requires_both_human_confirmations(
    corpus: OfflineStyleCorpus,
    field: str,
) -> None:
    candidates = await corpus.import_messages(
        messages(),
        sender_mapping=MAPPING,
        gateway=ScriptedModelGateway([judgment_response()]),
        model="test-model",
    )
    with pytest.raises(ValueError, match="Approval requires"):
        corpus.review(candidates[0].candidate_id, approval(**{field: False}))
    with pytest.raises(ValidationError):
        approval(actor=" ")


async def test_reviews_append_and_latest_rejection_revokes_example(
    corpus: OfflineStyleCorpus,
) -> None:
    bundle = await approved_bundle(corpus)
    assert bundle.status == "draft"
    assert bundle.profile.version == "test_style_v1"
    corpus.review(bundle.examples[0].example_id, approval(decision="reject"))
    assert corpus.connection.execute("SELECT COUNT(*) FROM corpus_reviews").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        corpus.connection.execute("DELETE FROM corpus_reviews")
    with pytest.raises(ValueError, match="No human-approved"):
        corpus.export_bundle(bundle)


async def test_review_can_replace_candidate_text_without_mutating_candidate(
    corpus: OfflineStyleCorpus,
) -> None:
    bundle = await approved_bundle(corpus)
    original = corpus.candidates()[0]
    corpus.review(
        bundle.examples[0].example_id, approval(example_text="测试人工修订：[既定事实]。")
    )
    revised = corpus.build_bundle(
        profile_id="test_style", version="test_style_v1", display_name="测试"
    )
    assert revised.examples[0].text == "测试人工修订：[既定事实]。"
    assert corpus.candidates()[0] == original
    with pytest.raises(ValueError, match="private information"):
        corpus.review(bundle.examples[0].example_id, approval(example_text="测试：13800138000"))


async def test_export_requires_matching_passed_eval_and_excludes_chat_context(
    corpus: OfflineStyleCorpus,
) -> None:
    bundle = await approved_bundle(corpus)
    with pytest.raises(ValueError, match="passing evaluation"):
        corpus.export_bundle(bundle)
    report = await corpus.evaluate(
        bundle,
        [eval_case()],
        gateway=ScriptedModelGateway([eval_response()]),
        model="test-model",
        actor="test-evaluator",
    )
    assert report.passed
    exported = corpus.export_bundle(bundle)
    assert exported["status"] == "draft"
    assert exported["evaluation"]["passed"]
    assert "今天已经完成记录" not in json.dumps(exported, ensure_ascii=False)
    assert "context" not in exported
    corpus.review(bundle.examples[0].example_id, approval(example_text="测试新审核文本。"))
    with pytest.raises(ValueError, match="approvals changed"):
        corpus.export_bundle(bundle)


@pytest.mark.parametrize(
    "failed_dimension",
    [
        "style_match",
        "semantic_fidelity",
        "privacy_preserved",
        "no_impersonation_or_abuse",
        "no_added_professional_claims",
    ],
)
async def test_every_eval_dimension_gates_export(
    corpus: OfflineStyleCorpus,
    failed_dimension: str,
) -> None:
    bundle = await approved_bundle(corpus)
    report = await corpus.evaluate(
        bundle,
        [eval_case()],
        gateway=ScriptedModelGateway([eval_response(**{failed_dimension: False})]),
        model="test-model",
        actor="test-evaluator",
    )
    assert not report.passed
    with pytest.raises(ValueError, match="passing evaluation"):
        corpus.export_bundle(bundle)


async def test_eval_failure_and_wrong_profile_fail_closed(corpus: OfflineStyleCorpus) -> None:
    bundle = await approved_bundle(corpus)
    gateway = ScriptedModelGateway([])
    report = await corpus.evaluate(
        bundle,
        [eval_case(profile_version="other")],
        gateway=gateway,
        model="test-model",
        actor="test-evaluator",
    )
    assert not report.passed
    assert gateway.requests == []
    report = await corpus.evaluate(
        bundle, [eval_case()], gateway=gateway, model="test-model", actor="test-evaluator"
    )
    assert not report.passed
    assert report.results[0].failure_code == "evaluation_failed"


async def test_eval_requires_coverage_of_every_exported_communication_act(
    corpus: OfflineStyleCorpus,
) -> None:
    bundle = await approved_bundle(corpus)
    candidates = await corpus.import_messages(
        messages(),
        sender_mapping=MAPPING,
        gateway=ScriptedModelGateway([judgment_response(communication_act="ask")]),
        model="test-model",
    )
    corpus.review(candidates[0].candidate_id, approval())
    bundle = corpus.build_bundle(
        profile_id=bundle.profile.profile_id,
        version=bundle.profile.version,
        display_name=bundle.profile.display_name,
    )
    report = await corpus.evaluate(
        bundle,
        [eval_case()],
        gateway=ScriptedModelGateway([eval_response()]),
        model="test-model",
        actor="test-evaluator",
    )
    assert not report.passed
    assert report.missing_acts == ("ask",)


def test_application_database_is_rejected_without_adding_corpus_tables(tmp_path: Path) -> None:
    path = tmp_path / "test-application.sqlite"
    with sqlite3.connect(path) as application:
        application.execute("CREATE TABLE users (id TEXT)")
    with pytest.raises(ValueError, match="separate offline"):
        OfflineStyleCorpus(path)
    with sqlite3.connect(path) as application:
        tables = application.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert tables == [("users",)]
