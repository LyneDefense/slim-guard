"""Synthetic-only tests for arbitrary feedback classification and regression generation."""

from __future__ import annotations

import json
from datetime import timedelta

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse
from slim_guard.agents.contracts import CommunicationAct
from slim_guard.agents.style import NeutralRenderer, StyleContext
from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1, StyleProfile
from slim_guard.db.session import Database
from slim_guard.style_feedback import (
    StyleCorrectionFeedbackInput,
    StyleCorrectionFeedbackRepository,
)
from slim_guard.style_iteration_builder import StyleIterationBuilder, StyleIterationWorker
from slim_guard.style_iterations import StyleIterationCreate, StyleIterationRepository
from slim_guard.style_profiles import StyleProfileRepository
from slim_guard.style_reviews import StyleABReviewRepository


def response(value: dict) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            content=json.dumps(value, ensure_ascii=False),
        )
    )


def snapshot() -> dict:
    return {
        "source_profile_version": "TEST-style_v1",
        "source_sha256": "a" * 64,
        "counts": {
            "reviewed_a_b_cases": 0,
            "accepted_a_b_cases": 0,
            "rejected_a_b_cases": 0,
            "style_corrections": 1,
        },
        "sources": {
            "a_b_reviews": [],
            "style_corrections": [
                {
                    "feedback_id": "TEST-feedback-outside-core-suite",
                    "profile_version": "TEST-style_v1",
                    "communication_act": None,
                    "scenario": "TEST 用户在一个新的合成场景中询问记录是否完整。",
                    "user_message": "TEST 这条记录完整吗？",
                    "agent_response": "TEST 您好，已经完整了。",
                    "desired_response": "TEST 完整了。",
                    "guidance_note": "TEST 去掉客套铺垫。",
                }
            ],
        },
    }


async def test_new_scenario_is_classified_and_added_to_the_regression_suite(tmp_path):
    gateway = ScriptedModelGateway(
        (
            response(
                {
                    "items": [
                        {
                            "feedback_id": "TEST-feedback-outside-core-suite",
                            "classification": "style_existing_act",
                            "primary_act": "acknowledge",
                            "summary": "TEST 属于简短确认的表达纠正。",
                            "generalized_rule": "TEST 确认时去掉无意义客套。",
                            "synthetic_scenario": {
                                "title": "TEST 新的合成确认场景",
                                "user_situation": "TEST 用户提交一条完整记录。",
                                "known_context": ["TEST 系统已确认记录完整。"],
                                "response_goal": "TEST 简短确认记录完整。",
                            },
                            "social_act": "TEST 简短确认既定状态。",
                        }
                    ]
                }
            ),
        )
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'builder.sqlite3'}")
    builder = StyleIterationBuilder(database=database, gateway=gateway, model="TEST-model")
    classifications = await builder.classify(snapshot())
    cases = builder.build_cases(snapshot=snapshot(), classifications=classifications)
    assert len(cases) == 13
    assert cases[-1].case_id.startswith("feedback-")
    assert cases[-1].response_plan.communication_act.value == "acknowledge"
    assert cases[-1].scenario.title == "TEST 新的合成确认场景"
    assert classifications[0]["derived_case_ids"] == [cases[-1].case_id]
    assert not builder.blocking_classifications(classifications)
    gateway.assert_exhausted()
    await database.close()


async def test_non_style_feedback_is_exposed_as_a_blocker_instead_of_forced_into_an_act(
    tmp_path,
):
    gateway = ScriptedModelGateway(
        (
            response(
                {
                    "items": [
                        {
                            "feedback_id": "TEST-feedback-outside-core-suite",
                            "classification": "professional_content",
                            "primary_act": None,
                            "summary": "TEST 该纠正试图新增专业结论。",
                            "generalized_rule": None,
                            "synthetic_scenario": None,
                            "social_act": None,
                        }
                    ]
                }
            ),
        )
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'blocker.sqlite3'}")
    builder = StyleIterationBuilder(database=database, gateway=gateway, model="TEST-model")
    classifications = await builder.classify(snapshot())
    assert builder.blocking_classifications(classifications) == classifications
    gateway.assert_exhausted()
    await database.close()


async def test_worker_builds_exact_pending_review_cases_without_publishing(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'worker.sqlite3'}")
    await database.migrate()
    profiles = StyleProfileRepository(database)
    await profiles.create_profile("TEST-style")
    await profiles.append_version(
        profile_id="TEST-style",
        version="TEST-style_v1",
        display_name="TEST source style",
        description="TEST source expression only",
        tone_rules=("TEST source rule",),
        status="evaluated",
    )
    correction = await StyleCorrectionFeedbackRepository(database).append(
        StyleCorrectionFeedbackInput(
            profile_version="TEST-style_v1",
            communication_act=None,
            scenario="TEST outside the twelve base scenarios.",
            user_message="TEST is this complete?",
            agent_response="TEST hello, this is complete.",
            desired_response="TEST complete.",
            deidentified_confirmed=True,
            expression_only_confirmed=True,
        ),
        actor="TEST-reviewer",
    )
    classification_item = {
        "feedback_id": correction["feedback_id"],
        "classification": "style_existing_act",
        "primary_act": "acknowledge",
        "summary": "TEST existing acknowledgement style.",
        "generalized_rule": "TEST remove empty greeting.",
        "synthetic_scenario": {
            "title": "TEST synthetic completion acknowledgement",
            "user_situation": "TEST user submitted a complete item.",
            "known_context": ["TEST system confirmed completion."],
            "response_goal": "TEST acknowledge completion briefly.",
        },
        "social_act": "TEST acknowledge the confirmed completion.",
    }
    proposal = {
        "display_name": "TEST candidate style",
        "description": "TEST candidate expression rules only",
        "tone_rules": ["TEST concise and direct"],
        "prohibited_phrases": [],
        "preferred_max_paragraphs": 2,
        "examples": [
            {
                "communication_act": act.value,
                "text": f"TEST {act.value} [confirmed content]",
            }
            for act in CommunicationAct
        ],
    }
    classification = {
        "source_kind": "style_feedback",
        "source_id": correction["feedback_id"],
        **classification_item,
        "derived_case_ids": [StyleIterationBuilder._feedback_case_id(correction["feedback_id"])],
    }
    preflight = StyleIterationBuilder(
        database=database,
        gateway=ScriptedModelGateway(()),
        model="TEST-model",
    )
    build_snapshot = {
        **snapshot(),
        "sources": {
            "a_b_reviews": [],
            "style_corrections": [correction],
        },
    }
    cases = preflight.build_cases(
        snapshot=build_snapshot,
        classifications=(classification,),
    )
    candidate_profile = StyleProfile(
        profile_id="TEST-style",
        version="TEST-style_v2",
        display_name=str(proposal["display_name"]),
        description=str(proposal["description"]),
        tone_rules=tuple(proposal["tone_rules"]),
        prohibited_phrases=("我是章医生", "作为章医生", "保证瘦", "一定能瘦"),
        preferred_max_paragraphs=2,
    )
    steps = [response({"items": [classification_item]}), response(proposal)]
    for case in cases:
        for profile in (SLIMGUARD_DEFAULT_V1, candidate_profile):
            rendered = NeutralRenderer().render(
                StyleContext(
                    turn_id="TEST-worker-render",
                    response_plan=case.response_plan,
                    profile=profile,
                )
            )
            steps.append(response(json.loads(rendered.model_dump_json())))
    steps.extend(
        response(
            {
                "style_match": True,
                "semantic_fidelity": True,
                "privacy_preserved": True,
                "no_impersonation_or_abuse": True,
                "no_added_professional_claims": True,
                "reason": "TEST synthetic automated pass",
            }
        )
        for _ in cases
    )
    gateway = ScriptedModelGateway(steps)
    repository = StyleIterationRepository(database)
    run = await repository.create_run(
        StyleIterationCreate(
            source_profile_version="TEST-style_v1",
            idempotency_key="TEST-worker-idempotency",
            reviewed_inputs_confirmed=True,
        ),
        actor="TEST-admin",
        model_configured=True,
    )
    worker = StyleIterationWorker(
        builder=StyleIterationBuilder(
            database=database,
            gateway=gateway,
            model="TEST-model",
        ),
        worker_id="TEST-worker",
        lease=timedelta(seconds=30),
    )
    assert await worker.run_once()
    result = await repository.get_run(run["run_id"])
    assert result["status"] == "ready_for_review"
    assert result["artifacts"]["bundle"]["profile"]["version"] == "TEST-style_v2"
    assert len(result["artifacts"]["cases"]) == 13
    assert await profiles.get_profile_asset("TEST-style_v2") is None
    stats = await StyleABReviewRepository(database).statistics(
        candidate_profile_version="TEST-style_v2"
    )
    assert stats["counts"]["pending_case_count"] == 13
    gateway.assert_exhausted()
    await database.close()
