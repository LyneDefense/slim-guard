"""Real SQLite regression coverage for user-scoped workflow trace facets."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.agents.contracts import payload_sha256
from slim_guard.db.models import (
    AgentArtifactRecord,
    AgentInvocationRecord,
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    InteractionTraceRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database

NOW = datetime(2026, 9, 8, tzinfo=UTC)


@pytest.fixture
async def traces(tmp_path: Path) -> AsyncIterator[AdminQueryRepository]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test-admin-trace-filters.sqlite'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        for owner in ("test-user-a", "test-user-b"):
            session.add(SlimGuardUser(id=owner, first_seen_at=NOW, last_seen_at=NOW))
            session.add(
                AgentThreadRecord(
                    id=f"thread-{owner}", user_id=owner, created_at=NOW, last_active_at=NOW
                )
            )
        session.add(
            AgentVersionRecord(
                id="test-runtime-version", manifest_json="{}", code_revision="test", created_at=NOW
            )
        )
        for index, label in enumerate(("off", "shadow", "canary", "on", "legacy", "other-user")):
            owner = "test-user-b" if label == "other-user" else "test-user-a"
            created = NOW + timedelta(minutes=6 - index)
            turn_id = f"turn-{label}"
            trace_id = f"trace-{label}"
            session.add(
                AgentTurnRecord(
                    id=turn_id,
                    thread_id=f"thread-{owner}",
                    agent_version_id="test-runtime-version",
                    trigger_type="user_message",
                    status="completed",
                    created_at=created,
                    updated_at=created,
                    completed_at=created,
                )
            )
            session.add(
                InteractionTraceRecord(
                    id=trace_id,
                    user_id=owner,
                    agent_turn_id=turn_id,
                    agent_version_id="test-runtime-version",
                    trigger_type="user_message",
                    reply_kind="agent",
                    generation_status="degraded" if label == "on" else "succeeded",
                    delivery_status="failed" if label == "on" else "accepted",
                    created_at=created,
                    completed_at=created,
                )
            )
            if label not in {"off", "legacy"}:
                roles = (
                    ("response_style", "response_reviewer")
                    if label == "canary"
                    else ("response_style",)
                )
                for role in roles:
                    version = (
                        "reviewer-v2"
                        if role == "response_reviewer"
                        else ("style-v1" if label == "shadow" else "style-v2")
                    )
                    session.add(
                        AgentInvocationRecord(
                            id=f"inv-{label}-{role}",
                            trace_id=trace_id,
                            turn_id=turn_id,
                            graph_version="graph-v1" if label == "shadow" else "graph-v2",
                            agent_role=role,
                            agent_version=version,
                            attempt=1,
                            caller="coordinator",
                            input_schema_version="1",
                            deadline_at=created + timedelta(seconds=30),
                            max_model_calls=2,
                            max_tool_calls=0,
                            max_total_tokens=1024,
                            status="failed" if label == "on" else "succeeded",
                            input_payload_json=json.dumps(
                                {
                                    "mode": label,
                                    "private_raw_context": "TEST PRIVATE CHAT",
                                }
                            ),
                            started_at=created,
                            completed_at=created,
                        )
                    )
                style_payload = {
                    "text": "TEST PRIVATE RESPONSE BODY",
                    "style_profile_version": "profile-v1" if label == "shadow" else "profile-v2",
                }
                session.add(
                    AgentArtifactRecord(
                        id=f"style-{label}",
                        turn_id=turn_id,
                        producer_role="response_style",
                        artifact_type="styled_response",
                        schema_version="1",
                        parent_artifact_ids_json="[]",
                        payload_json=json.dumps(style_payload),
                        payload_sha256=payload_sha256(style_payload),
                        created_at=created,
                    )
                )
            sequence = 0
            if label in {"canary", "legacy", "other-user"}:
                sequence += 1
                session.add(
                    AgentItemRecord(
                        id=f"adopted-{label}",
                        thread_id=f"thread-{owner}",
                        turn_id=turn_id,
                        sequence=sequence,
                        item_type="response_adopted",
                        status="completed",
                        payload_json=json.dumps(
                            {
                                "artifact_id": f"style-{label}",
                                "mode": "on" if label == "other-user" else label,
                                "final": label != "on",
                            }
                        ),
                        created_at=created,
                    )
                )
            if label in {"canary", "other-user"}:
                rag_payload = {
                    "knowledge": {
                        "corpus_status": "ready",
                        "citations": [],
                        "private_text": "TEST PRIVATE KNOWLEDGE",
                    }
                }
                session.add(
                    AgentArtifactRecord(
                        id=f"rag-{label}",
                        turn_id=turn_id,
                        producer_role="coordinator",
                        artifact_type="nutrition_observations",
                        schema_version="1",
                        parent_artifact_ids_json="[]",
                        payload_json=json.dumps(rag_payload),
                        payload_sha256=payload_sha256(rag_payload),
                        created_at=created,
                    )
                )
            if label == "canary":
                sequence += 1
                session.add(
                    AgentItemRecord(
                        id=f"repair-{label}",
                        thread_id=f"thread-{owner}",
                        turn_id=turn_id,
                        sequence=sequence,
                        item_type="workflow_transition",
                        status="completed",
                        payload_json=json.dumps(
                            {
                                "from_node": "review_running",
                                "to_node": "style_running",
                                "transition_type": "route",
                                "reason_code": "review_repair",
                                "attempt": 1,
                            }
                        ),
                        created_at=created,
                    )
                )
    try:
        yield AdminQueryRepository(database)
    finally:
        await database.close()


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("off", {"trace-off", "trace-legacy"}),
        ("shadow", {"trace-shadow"}),
        ("canary", {"trace-canary"}),
        ("on", {"trace-on"}),
    ],
)
async def test_mode_filters_include_historical_legacy_as_off(traces, mode, expected):
    result = await traces.list_traces(user_id="test-user-a", limit=20, offset=0, mode=mode)
    assert {item["id"] for item in result["items"]} == expected
    assert result["total"] == len(expected)
    assert all(item["mode"] == mode for item in result["items"])


@pytest.mark.parametrize(
    "facet,true_trace",
    [
        ("agent_failure", "trace-on"),
        ("rag", "trace-canary"),
        ("repair", "trace-canary"),
        ("degraded", "trace-on"),
    ],
)
async def test_boolean_facets_filter_true_and_false_without_cross_user_leak(
    traces, facet, true_trace
):
    positive = await traces.list_traces(user_id="test-user-a", limit=20, offset=0, **{facet: True})
    negative = await traces.list_traces(user_id="test-user-a", limit=20, offset=0, **{facet: False})
    assert [item["id"] for item in positive["items"]] == [true_trace]
    assert positive["total"] == 1
    assert negative["total"] == 4
    assert true_trace not in {item["id"] for item in negative["items"]}
    assert all(item[facet] is False for item in negative["items"])
    assert "trace-other-user" not in {item["id"] for item in negative["items"]}


async def test_version_filters_use_invocation_versions_and_style_asset_version(traces):
    result = await traces.list_traces(
        user_id="test-user-a",
        limit=20,
        offset=0,
        mode="canary",
        graph_version="graph-v2",
        agent_version="reviewer-v2",
        profile_version="profile-v2",
        rag=True,
        repair=True,
        degraded=False,
    )
    assert [item["id"] for item in result["items"]] == ["trace-canary"]
    item = result["items"][0]
    assert set(item["agent_versions"]) == {"style-v2", "reviewer-v2"}
    assert item["graph_version"] == "graph-v2"
    assert item["profile_versions"] == ["profile-v2"]
    runtime_version = await traces.list_traces(
        user_id="test-user-a", limit=20, offset=0, agent_version="test-runtime-version"
    )
    assert runtime_version["total"] == 0


async def test_pagination_total_is_computed_after_facet_filters(traces):
    result = await traces.list_traces(user_id="test-user-a", limit=1, offset=1, agent_failure=False)
    assert result["total"] == 4
    assert result["limit"] == 1
    assert result["offset"] == 1
    assert [item["id"] for item in result["items"]] == ["trace-shadow"]
    beyond = await traces.list_traces(user_id="test-user-a", limit=1, offset=9, agent_failure=False)
    assert beyond["total"] == 4
    assert beyond["items"] == []


async def test_facets_compose_with_existing_delivery_and_generation_filters(traces):
    result = await traces.list_traces(
        user_id="test-user-a",
        limit=20,
        offset=0,
        mode="on",
        delivery_status="failed",
        generation_status="degraded",
        agent_failure=True,
    )
    assert [item["id"] for item in result["items"]] == ["trace-on"]
    mismatch = await traces.list_traces(
        user_id="test-user-a", limit=20, offset=0, mode="on", delivery_status="accepted"
    )
    assert mismatch["total"] == 0


async def test_trace_facets_do_not_expose_candidate_bodies_or_invocation_context(traces):
    result = await traces.list_traces(user_id="test-user-a", limit=20, offset=0)
    assert result["total"] == 5
    serialized = json.dumps(result, default=str)
    assert "TEST PRIVATE" not in serialized
    assert "private_raw_context" not in serialized
    assert "private_text" not in serialized
    assert await traces.list_traces(user_id="does-not-exist", limit=20, offset=0) is None


def test_dish_trace_facets_cover_confirmation_match_suitability_and_review() -> None:
    artifacts = [
        {
            "artifact_type": "dish_recognition",
            "payload": {"overall_requires_confirmation": True},
        },
        {"artifact_type": "confirmed_dish_set", "payload": {"dishes": []}},
        {
            "artifact_type": "dish_evidence_bundle",
            "payload": {
                "dishes": [
                    {"entity_match": {"status": "alias"}, "rag_evidence": []}
                ]
            },
        },
        {
            "artifact_type": "diet_guidance_assessment",
            "payload": {
                "dishes": [{"suitability": "suitable_with_adjustment"}]
            },
        },
        {"artifact_type": "reviewer_verdict", "payload": {"verdict": "pass"}},
    ]
    confirmation, matches, suitabilities = AdminQueryRepository._dish_facets(artifacts)
    assert confirmation == "confirmed"
    assert matches == ["alias"]
    assert suitabilities == ["suitable_with_adjustment"]
    assert AdminQueryRepository._review_verdict_facet(artifacts) == "pass"
