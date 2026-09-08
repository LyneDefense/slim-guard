"""Real SQLite/API checks using explicitly invented A/B plans and test human scores."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from slim_guard.agents.style import NeutralRenderer, StyleContext
from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1
from slim_guard.db.models import AdminAuditEventRecord, StyleABHumanReviewRecord
from slim_guard.main import create_app
from slim_guard.style_evaluation import synthetic_style_suite
from slim_guard.style_reviews import (
    StyleABPairImport,
    StyleABReviewRepository,
    TrustedStyleABSource,
)

PREFIX = "/api/admin/style-ab"
CSRF = {"X-SlimGuard-CSRF": "1"}
SCORE = {
    "style_match": 4,
    "fidelity": 5,
    "appropriateness": 4,
    "decision": "accept",
    "comment": "TEST ONLY synthetic human score",
}


@pytest.fixture
async def api(test_settings):
    settings = test_settings.model_copy(
        update={
            "wecom_corp_id": "",
            "wecom_kf_secret": "",
            "wecom_open_kf_id": "",
            "wecom_callback_token": "",
            "wecom_callback_aes_key": "",
            "admin_username": "TEST-logged-in-operator",
            "admin_password": "TEST-password-not-real",
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        profile = SLIMGUARD_DEFAULT_V1.model_copy(
            update={"profile_id": "TEST-api-style", "version": "TEST-api-style-v1"}
        )
        cases = [synthetic_style_suite()[index] for index in (0, 2, 10)]
        pairs = []
        for case in cases:
            baseline, candidate = (
                NeutralRenderer().render(
                    StyleContext(
                        turn_id="TEST-turn", response_plan=case.response_plan, profile=style
                    )
                )
                for style in (SLIMGUARD_DEFAULT_V1, profile)
            )
            pairs.append(
                StyleABPairImport(
                    case_id=f"TEST-api-{case.case_id}",
                    source_sample_sha256="a" * 64,
                    scenario=case.scenario,
                    response_plan=case.response_plan,
                    baseline_response=baseline,
                    candidate_response=candidate,
                    baseline_profile_version=baseline.style_profile_version,
                    candidate_profile_version=candidate.style_profile_version,
                    baseline_generation_model="TEST-scripted",
                    candidate_generation_model="TEST-scripted",
                    candidate_bundle_sha256="b" * 64,
                    automated_evaluation_sha256="c" * 64,
                    automated_judge_model="TEST-scripted",
                    automated_judge_status="passed",
                )
            )
        repository = StyleABReviewRepository(app.state.database)
        acts = tuple(case.response_plan.communication_act for case in cases)
        source = TrustedStyleABSource(
            source_id="TEST-api-synthetic-source",
            imported_by="TEST-offline-importer",
            manifest_sha256=repository.pairs_manifest_sha256(
                pairs,
                source_id="TEST-api-synthetic-source",
                required_communication_acts=acts,
            ),
            required_communication_acts=acts,
            synthetic_confirmed=True,
            deidentified_confirmed=True,
            expression_assets_reviewed=True,
        )
        ids = await repository.import_pairs(pairs, trusted_source=source)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, repository, ids, app


async def login(client):
    result = await client.post(
        "/api/admin/auth/login",
        json={"username": "TEST-logged-in-operator", "password": "TEST-password-not-real"},
    )
    assert result.status_code == 200


@pytest.mark.parametrize("endpoint", ["list", "detail", "statistics", "review"])
async def test_every_style_review_endpoint_requires_real_admin_session(api, endpoint):
    client, repository, ids, _ = api
    if endpoint == "review":
        response = await client.post(f"{PREFIX}/cases/{ids[0]}/reviews", json=SCORE, headers=CSRF)
    else:
        path = {"list": "/cases", "detail": f"/cases/{ids[0]}", "statistics": "/statistics"}
        response = await client.get(PREFIX + path[endpoint])
    assert response.status_code == 401
    assert (await repository.get_case(ids[0]))["reviews"] == []


async def test_authenticated_list_filters_pagination_and_detail_return_synthetic_comparisons(api):
    client, _, ids, _ = api
    await login(client)
    listing = await client.get(f"{PREFIX}/cases", params={"limit": 1, "offset": 1})
    assert listing.status_code == 200
    assert listing.json()["total"] == 3
    assert len(listing.json()["items"]) == 1
    assert "response_plan" not in listing.json()["items"][0]
    assert listing.json()["items"][0]["scenario_title"]
    assert "response" not in listing.json()["items"][0]["candidate"]
    filtered = await client.get(
        f"{PREFIX}/cases",
        params={
            "communication_act": "ask",
            "candidate_profile_version": "TEST-api-style-v1",
            "decision": "pending",
        },
    )
    assert filtered.json()["total"] == 1
    detail = await client.get(f"{PREFIX}/cases/{ids[0]}")
    assert detail.status_code == 200
    assert detail.json()["scenario"]["user_situation"]
    assert detail.json()["scenario"]["known_context"]
    assert detail.json()["scenario"]["response_goal"]
    assert detail.json()["response_plan"]["communication_act"] == "acknowledge"
    assert detail.json()["candidate"]["response"]["text"]
    assert detail.json()["baseline"]["profile_version"] == SLIMGUARD_DEFAULT_V1.version
    assert detail.json()["reviews"] == []
    assert detail.json()["latest_human_review"] is None
    empty = await client.get(f"{PREFIX}/cases", params={"candidate_profile_version": "unknown"})
    assert empty.json()["total"] == 0


async def test_review_actor_is_login_identity_and_corrections_append_without_mutating_case(api):
    client, repository, ids, app = api
    await login(client)
    before = await repository.get_case(ids[0])
    first = await client.post(f"{PREFIX}/cases/{ids[0]}/reviews", json=SCORE, headers=CSRF)
    assert first.status_code == 201
    initial = first.json()
    assert initial["actor"] == "TEST-logged-in-operator"
    assert initial["supersedes_review_id"] is None
    stale = await client.post(f"{PREFIX}/cases/{ids[0]}/reviews", json=SCORE, headers=CSRF)
    assert stale.status_code == 409
    correction = await client.post(
        f"{PREFIX}/cases/{ids[0]}/reviews",
        json={**SCORE, "decision": "reject", "corrects_review_id": initial["review_id"]},
        headers=CSRF,
    )
    assert correction.status_code == 201
    assert correction.json()["review_id"] != initial["review_id"]
    assert correction.json()["supersedes_review_id"] == initial["review_id"]
    after = await repository.get_case(ids[0])
    assert len(after["reviews"]) == 2
    assert after["latest_human_review"]["decision"] == "reject"
    for key in ("baseline", "candidate", "response_plan", "source_sample_sha256"):
        assert after[key] == before[key]
    async with app.state.database.session() as session:
        reviews = list(await session.scalars(select(StyleABHumanReviewRecord)))
        audits = list(await session.scalars(select(AdminAuditEventRecord)))
    assert len(reviews) == 2
    assert {review.actor for review in reviews} == {"TEST-logged-in-operator"}
    assert len(audits) == 2
    assert {audit.action for audit in audits} == {"append_review"}
    stats = (await client.get(f"{PREFIX}/statistics")).json()
    assert stats["counts"] == {
        "case_count": 3,
        "reviewed_case_count": 1,
        "pending_case_count": 2,
        "accepted_case_count": 0,
        "rejected_case_count": 1,
    }
    assert stats["denominators"]["acceptance_rate"] == 1
    assert stats["scores"]["fidelity"] == {"sample_count": 1, "average": 5.0}
    assert (await client.get(f"{PREFIX}/cases", params={"decision": "reject"})).json()["total"] == 1
    assert (await client.get(f"{PREFIX}/cases", params={"decision": "accept"})).json()["total"] == 0
    assert (await client.get(f"{PREFIX}/cases", params={"decision": "pending"})).json()[
        "total"
    ] == 2


@pytest.mark.parametrize("headers", [{}, {"X-SlimGuard-CSRF": "0"}])
async def test_review_rejects_missing_or_wrong_csrf_header(api, headers):
    client, repository, ids, _ = api
    await login(client)
    result = await client.post(f"{PREFIX}/cases/{ids[0]}/reviews", json=SCORE, headers=headers)
    assert result.status_code == 403
    assert (await repository.get_case(ids[0]))["reviews"] == []


@pytest.mark.parametrize(
    "extra",
    [
        {"actor": "forged-other-admin"},
        {"style_match": True},
        {"fidelity": 6},
        {"appropriateness": 0},
        {"comment": " "},
        {"decision": "approve"},
    ],
)
async def test_client_actor_and_invalid_score_are_rejected_without_writes(api, extra):
    client, repository, ids, _ = api
    await login(client)
    result = await client.post(
        f"{PREFIX}/cases/{ids[0]}/reviews", json={**SCORE, **extra}, headers=CSRF
    )
    assert result.status_code == 422
    assert (await repository.get_case(ids[0]))["reviews"] == []


@pytest.mark.parametrize(
    "params",
    [{"decision": "approved"}, {"communication_act": "unknown"}, {"limit": 0}, {"offset": -1}],
)
async def test_invalid_list_filters_are_validated_by_api(api, params):
    client, _, _, _ = api
    await login(client)
    assert (await client.get(f"{PREFIX}/cases", params=params)).status_code == 422


async def test_missing_cases_logout_and_no_http_import_or_publication(api):
    client, _, ids, _ = api
    await login(client)
    assert (await client.get(f"{PREFIX}/cases/not-found")).status_code == 404
    missing = await client.post(f"{PREFIX}/cases/not-found/reviews", json=SCORE, headers=CSRF)
    assert missing.status_code == 404
    assert (await client.post(f"{PREFIX}/cases", json={})).status_code == 405
    assert (await client.post(f"{PREFIX}/publish", json={})).status_code == 404
    assert (await client.delete(f"{PREFIX}/cases/{ids[0]}")).status_code == 405
    assert (await client.post("/api/admin/auth/logout")).status_code == 200
    assert (await client.get(f"{PREFIX}/cases")).status_code == 401
