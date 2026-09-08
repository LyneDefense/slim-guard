"""CLI workflow uses synthetic test fixtures and a fake offline model only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse
from slim_guard.tools import manage_style_corpus


def _response(payload: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=json.dumps(payload))
    )


async def test_cli_import_review_eval_export_stays_offline_and_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "test-export.json"
    source.write_text(
        json.dumps(
            [
                {"sender": "test-user", "text": "TEST DATA: hello"},
                {"sender": "test-target", "text": "TEST DATA: received"},
            ]
        ),
        encoding="utf-8",
    )
    mapping = tmp_path / "test-mapping.json"
    mapping.write_text(
        json.dumps({"test-user": "测试参与者", "test-target": "章之文"}), encoding="utf-8"
    )
    gateways = [
        ScriptedModelGateway(
            [
                _response(
                    {
                        "related": True,
                        "communication_act": "acknowledge",
                        "example_text": "TEST EXAMPLE: received [fact].",
                        "tone_rules": ["TEST RULE: be concise"],
                        "reason": "TEST: relevant",
                    }
                )
            ]
        ),
        ScriptedModelGateway(
            [
                _response(
                    {
                        "style_match": True,
                        "semantic_fidelity": True,
                        "privacy_preserved": True,
                        "no_impersonation_or_abuse": True,
                        "no_added_professional_claims": True,
                        "reason": "TEST: passed",
                    }
                )
            ]
        ),
    ]
    monkeypatch.setattr(manage_style_corpus, "ZhipuModelGateway", lambda **kwargs: gateways.pop(0))
    monkeypatch.setenv("STYLE_CORPUS_API_KEY", "test-only-key")
    monkeypatch.setenv("STYLE_CORPUS_MODEL", "test-only-model")
    base = ["--database", str(tmp_path / "offline-test.sqlite")]
    parser = manage_style_corpus.parser()
    imported = await manage_style_corpus.run(
        parser.parse_args(
            [
                *base,
                "import",
                "--input",
                str(source),
                "--format",
                "json",
                "--sender-mapping",
                str(mapping),
            ]
        )
    )
    assert imported["status"] == "pending_human_review"
    pending = await manage_style_corpus.run(parser.parse_args([*base, "review"]))
    assert len(pending["candidates"]) == 1
    profile = ["--profile-id", "test_cli", "--version", "test_cli_v1", "--display-name", "TEST CLI"]
    with pytest.raises(ValueError, match="No human-approved"):
        await manage_style_corpus.run(parser.parse_args([*base, "export", *profile]))
    review_file = tmp_path / "test-review.json"
    review_file.write_text(
        json.dumps(
            {
                "actor": "test-human",
                "decision": "approve",
                "note": "TEST review",
                "privacy_confirmed": True,
                "expression_only_confirmed": True,
            }
        ),
        encoding="utf-8",
    )
    await manage_style_corpus.run(
        parser.parse_args(
            [
                *base,
                "review",
                "--candidate-id",
                imported["candidate_ids"][0],
                "--review",
                str(review_file),
            ]
        )
    )
    cases = tmp_path / "test-eval-cases.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "case_id": "test-case",
                    "scenario": {
                        "title": "TEST scenario",
                        "user_situation": "TEST user submitted one check-in.",
                        "known_context": ["TEST no additional facts are available."],
                        "response_goal": "TEST acknowledge the check-in.",
                    },
                    "response_plan": {
                        "communication_act": "acknowledge",
                        "content_blocks": [
                            {
                                "block_id": "test-block",
                                "kind": "social_act",
                                "text": "TEST: received",
                            },
                        ],
                    },
                    "styled_response": {
                        "text": "TEST: received",
                        "used_block_ids": ["test-block"],
                        "style_profile_version": "test_cli_v1",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    evaluated = await manage_style_corpus.run(
        parser.parse_args(
            [
                *base,
                "eval",
                *profile,
                "--cases",
                str(cases),
                "--actor",
                "test-evaluator",
            ]
        )
    )
    assert evaluated["passed"]
    exported = await manage_style_corpus.run(parser.parse_args([*base, "export", *profile]))
    assert exported["status"] == "draft"
    assert exported["profile"]["version"] == "test_cli_v1"
    assert "context" not in exported
    assert not gateways


async def test_cli_judging_requires_separate_explicit_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STYLE_CORPUS_API_KEY", raising=False)
    monkeypatch.delenv("STYLE_CORPUS_MODEL", raising=False)
    args = manage_style_corpus.parser().parse_args(
        [
            "--database",
            str(tmp_path / "offline-test.sqlite"),
            "import",
            "--input",
            "unused.json",
            "--format",
            "json",
            "--sender-mapping",
            "unused.json",
        ]
    )
    with pytest.raises(ValueError, match="STYLE_CORPUS_API_KEY"):
        await manage_style_corpus.run(args)
