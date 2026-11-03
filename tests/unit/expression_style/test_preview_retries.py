"""Targeted preview retry: immutable packages, versioned answers, fencing and recovery."""

import copy
import json

import pytest
from sqlalchemy import func, select

from slim_guard.agent_models.gateway import ModelMessage
from slim_guard.agents.style.agent import RESPONSE_STYLE_PROMPT_VERSION
from slim_guard.expression_style.package import Guide, make_package
from slim_guard.expression_style.review.policy import review_signature
from slim_guard.expression_style.trainer.contracts import WORKFLOW_VERSION
from slim_guard.expression_style.trainer.ports import BuildInterrupted
from slim_guard.group_chat import ROLE_POLICY_VERSION
from slim_guard.style_management.builds import BuildRepository
from slim_guard.style_management.contracts import ReviewInput, StyleInput
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.job_store import JobStore
from slim_guard.style_management.models import BuildRun, ReviewCase, Version
from slim_guard.style_management.preview_retries import PreviewRetryRepository
from slim_guard.style_management.runtime import RuntimeStyles
from slim_guard.style_management.versions import VersionRepository
from slim_guard.style_management.worker import StyleWorker

from .fakes import TrainerGateway


async def seed_preview(db, *, total=17, failed=2):
    package = make_package(
        "doctor", "retry-test-v", "重试测试", Guide(summary="自然简短，不改变原意")
    )
    report = {
        "schema_version": "2",
        "workflow_version": WORKFLOW_VERSION,
        "role_policy_version": ROLE_POLICY_VERSION,
        "package_hash": package.package_hash,
        "preview_metrics": {
            "total": total,
            "content_failed": failed,
            "changed": 0,
            "expression_advisories": 0,
        },
        "release_eligible": not failed,
    }
    async with db.session() as session, session.begin():
        session.add(
            Version(
                id=package.version_id,
                style_id="doctor",
                name="重试测试版本",
                actor="admin",
                package=package.model_dump(mode="json"),
                report=report,
            )
        )
        await session.flush()
        for i in range(total):
            source = f"可以聊第{i}个话题。"
            automated = {
                "text": source,
                "package_hash": package.package_hash,
                "passed": i >= failed,
                "fallback": i < failed,
                "failure_code": "style_integrity_invalid" if i < failed else None,
                "checks": [
                    {
                        "attempt": 1,
                        "passed": i >= failed,
                        "output": "没有保留数字。",
                        "issues": ["number_changed"] if i < failed else [],
                    }
                ],
                "execution": {
                    "model": "test-model",
                    "review": review_signature("test-model"),
                    "rewrite": RESPONSE_STYLE_PROMPT_VERSION,
                },
            }
            session.add(
                ReviewCase(
                    id=f"case-{i:02}",
                    version_id=package.version_id,
                    test_case={
                        "id": f"test-{i}",
                        "family": "synthetic",
                        "user_input": f"聊聊{i}",
                        "source_text": source,
                        "context": [],
                        "protected_literals": [],
                        "origin": "fixed",
                    },
                    user_input=f"聊聊{i}",
                    original_response=source,
                    doctor_response=source,
                    baseline_response=source,
                    automated=automated,
                    review={
                        "decision": "accept",
                        "actor": "admin",
                        "reason": "旧评审",
                        "desired_response": "",
                        "revision": 1,
                        "style_match": 4,
                        "fidelity": 5,
                        "appropriateness": 4,
                    },
                )
            )
    return package


async def rows(db):
    return (await VersionRepository(db).cases("doctor", "retry-test-v"))["items"]


def accept(revision):
    return ReviewInput(
        style_match=4, fidelity=5, appropriateness=4, decision="accept", output_revision=revision
    )


async def test_only_two_of_seventeen_are_retried_and_other_fifteen_approvals_survive(style_db):
    package = await seed_preview(style_db)
    before = copy.deepcopy(await rows(style_db))
    retries, versions = PreviewRetryRepository(style_db), VersionRepository(style_db)
    job = await retries.start(
        "doctor", package.version_id, "admin", model="test-model", request_key="one"
    )
    assert job["job_kind"] == "preview_retry" and job["preview_count"] == 2
    assert await rows(style_db) == before
    assert (await versions.versions("doctor"))[0]["preview_retry"]["id"] == job["id"]
    gateway = TrainerGateway()
    await StyleWorker(style_db, gateway, "test-model").run_once()
    assert [r.output_schema_name for r in gateway.requests] == ["StyledResponse", "ReplyCheck"] * 2
    assert all(
        "number_changed"
        in json.loads(r.messages[1].content)["review_feedback"]["issues"][0]["explanation"]
        for r in gateway.requests
        if r.output_schema_name == "StyledResponse"
    )
    after = await rows(style_db)
    assert after[2:] == before[2:]
    assert all(
        r["review"] is None and r["output_revision"] == 2 and r["automated"]["passed"]
        for r in after[:2]
    )
    builds = BuildRepository(style_db)
    detail = await builds.detail("doctor", job["id"])
    assert detail["status"] == "completed" and detail["report"]["release_eligible"]
    assert detail["report"]["preview_metrics"]["total"] == 17
    assert "retry_inputs" in detail["artifacts"] and "input_materials" not in detail["artifacts"]
    history = (await builds.artifact("doctor", job["id"], "retry_inputs"))["items"]
    assert all(r["review"]["decision"] == "accept" for r in history)
    async with style_db.session() as session:
        version = await session.get(Version, package.version_id)
        assert version.package == package.model_dump(mode="json")
        assert (
            await session.scalar(select(func.count()).select_from(Version)) == 2
        )  # Builtin + unchanged target.
    with pytest.raises(ValueError, match="逐条确认"):
        await versions.action("doctor", package.version_id, "publish")
    for case in after[:2]:
        with pytest.raises(ValueError, match="回答已更新"):
            await versions.review("doctor", case["id"], accept(1), "admin")
        with pytest.raises(ValueError, match="回答已更新"):
            await versions.review("doctor", case["id"], accept(None), "admin")
        saved = await versions.review("doctor", case["id"], accept(2), "admin")
        assert saved["review"]["revision"] == 2  # Feedback provenance must not reuse revision 1.
    await versions.action("doctor", package.version_id, "publish")
    assert await RuntimeStyles(style_db).resolve() == "doctor_builtin_v1"
    await versions.action("doctor", package.version_id, "activate")
    assert await RuntimeStyles(style_db).resolve() == package.version_id


async def test_queue_is_idempotent_and_serializes_build_review_and_publication(style_db):
    package = await seed_preview(style_db)
    repo = PreviewRetryRepository(style_db)
    job = await repo.start(
        "doctor", package.version_id, "admin", model="test-model", request_key="same"
    )
    assert (
        await repo.start(
            "doctor", package.version_id, "admin", model="test-model", request_key="same"
        )
    )["id"] == job["id"]
    with pytest.raises(ValueError, match="正在进行"):
        await repo.start("doctor", package.version_id, "admin", model="test-model")
    with pytest.raises(ValueError, match="正在进行"):
        await BuildRepository(style_db).build("doctor", "admin", model="test-model")
    versions = VersionRepository(style_db)
    with pytest.raises(ValueError, match="正在重试"):
        await versions.review("doctor", "case-00", accept(1), "admin")
    await versions.review("doctor", "case-16", accept(1), "admin")
    with pytest.raises(ValueError, match="正在重试"):
        await versions.action("doctor", package.version_id, "publish")


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("published", "已发布"),
        ("package_hash", "风格包已变化"),
        ("policy", "职责"),
        ("signature", "检查策略"),
        ("count", "清单不完整"),
        ("case_hash", "不对应"),
    ],
)
async def test_retry_rejects_changed_or_incompatible_inputs(style_db, mutation, message):
    package = await seed_preview(style_db, total=3)
    async with style_db.session() as session, session.begin():
        version = await session.get(Version, package.version_id)
        if mutation == "published":
            version.status = "published"
        elif mutation in {"package_hash", "policy", "count"}:
            report = copy.deepcopy(version.report)
            if mutation == "package_hash":
                report["package_hash"] = "wrong"
            elif mutation == "policy":
                report["role_policy_version"] = "legacy"
            else:
                report["preview_metrics"]["total"] = 99
            version.report = report
        else:
            case = await session.get(ReviewCase, "case-02")
            value = copy.deepcopy(case.automated)
            if mutation == "signature":
                value["execution"]["review"] = "old-policy"
            else:
                value["package_hash"] = "wrong"
            case.automated = value
    with pytest.raises(ValueError, match=message):
        await PreviewRetryRepository(style_db).start(
            "doctor", package.version_id, "admin", model="test-model"
        )


async def test_no_failures_and_cross_style_targets_are_rejected(style_db):
    package = await seed_preview(style_db, failed=0)
    repo = PreviewRetryRepository(style_db)
    with pytest.raises(ValueError, match="没有需要重试"):
        await repo.start("doctor", package.version_id, "admin", model="test-model")
    other = await CorpusRepository(style_db).create_style(StyleInput(name="其他风格"))
    with pytest.raises(LookupError):
        await repo.start(other["id"], package.version_id, "admin", model="test-model")


async def test_cancel_and_stale_worker_do_not_replace_answers_or_approvals(style_db):
    package = await seed_preview(style_db)
    job = await PreviewRetryRepository(style_db).start(
        "doctor", package.version_id, "admin", model="test-model"
    )
    before = await rows(style_db)
    async with style_db.session() as session, session.begin():
        run = await session.get(BuildRun, job["id"])
        run.status, run.worker_token = "running", "old-token"
    await BuildRepository(style_db).action("doctor", job["id"], "cancel")
    with pytest.raises(BuildInterrupted):
        await JobStore(style_db, job["id"], "old-token").finish(
            {
                "job_kind": "preview_retry",
                "package": package.model_dump(mode="json"),
                "cases": [],
            }
        )
    assert await rows(style_db) == before
    assert not await StyleWorker(style_db, TrainerGateway(), "test-model").run_once()


async def test_execution_failure_preserves_all_reviews_and_resume_reuses_successful_case(style_db):
    class OfflineSecond(TrainerGateway):
        broken = True

        async def complete(self, request):
            payload = json.loads(request.messages[1].content)
            if (
                self.broken
                and request.output_schema_name == "StyledResponse"
                and payload["style_context"]["user_input"] == "聊聊1"
            ):
                self.requests.append(request)
                raise RuntimeError("test provider offline")
            return await super().complete(request)

    package = await seed_preview(style_db)
    before = await rows(style_db)
    job = await PreviewRetryRepository(style_db).start(
        "doctor", package.version_id, "admin", model="test-model"
    )
    gateway = OfflineSecond()
    worker, builds = StyleWorker(style_db, gateway, "test-model"), BuildRepository(style_db)
    await worker.run_once()
    failed = await builds.detail("doctor", job["id"])
    assert failed["status"] == "failed" and failed["can_resume"]
    assert await rows(style_db) == before
    count = len(gateway.requests)
    gateway.broken = False
    await builds.action("doctor", job["id"], "resume")
    await worker.run_once()
    completed = await builds.detail("doctor", job["id"])
    assert completed["status"] == "completed", completed["error"]
    assert [r.output_schema_name for r in gateway.requests[count:]] == [
        "StyledResponse",
        "ReplyCheck",
    ]
    assert completed["usage"]["calls"] == len(gateway.requests)
    assert completed["usage"]["seconds"] >= failed["usage"]["seconds"]


async def test_still_invalid_rewrite_remains_unpublishable_and_never_fakes_success(style_db):
    class StillWrong(TrainerGateway):
        async def complete(self, request):
            result = await super().complete(request)
            if request.output_schema_name == "StyledResponse":
                value = json.loads(result.message.content)
                value["text"] = "无依据省略了原稿数字。"
                return result.model_copy(
                    update={"message": ModelMessage(role="assistant", content=json.dumps(value))}
                )
            return result

    package = await seed_preview(style_db, total=3)
    job = await PreviewRetryRepository(style_db).start(
        "doctor", package.version_id, "admin", model="test-model"
    )
    await StyleWorker(style_db, StillWrong(), "test-model").run_once()
    data = await BuildRepository(style_db).detail("doctor", job["id"])
    assert data["status"] == "completed" and not data["report"]["release_eligible"]
    assert data["report"]["preview_metrics"]["content_failed"] == 2
    assert all(
        c["review"] is None and c["automated"]["fallback"] for c in (await rows(style_db))[:2]
    )
    with pytest.raises(ValueError):
        await VersionRepository(style_db).action("doctor", package.version_id, "publish")


@pytest.mark.parametrize(
    "mutation", ["review", "package", "takeover", "report", "published", "case_list"]
)
async def test_finish_fences_changed_snapshots_and_new_worker_ownership(style_db, mutation):
    package = await seed_preview(style_db, total=3)
    job = await PreviewRetryRepository(style_db).start(
        "doctor", package.version_id, "admin", model="test-model"
    )
    async with style_db.session() as session, session.begin():
        run = await session.get(BuildRun, job["id"])
        run.status, run.worker_token = "running", "new" if mutation == "takeover" else "old"
        inputs = copy.deepcopy(run.snapshot["retry_cases"])
        if mutation == "review":
            case = await session.get(ReviewCase, "case-00")
            case.review = {**case.review, "revision": 2}
        elif mutation == "package":
            version = await session.get(Version, package.version_id)
            version.package = make_package(
                "doctor", package.version_id, "变化", Guide(summary="变化")
            ).model_dump(mode="json")
        elif mutation == "report":
            version = await session.get(Version, package.version_id)
            version.report = {**version.report, "changed_after_queue": True}
        elif mutation == "published":
            version = await session.get(Version, package.version_id)
            version.status = "published"
        elif mutation == "case_list":
            await session.delete(await session.get(ReviewCase, "case-02"))
    before = await rows(style_db)
    result = {
        "job_kind": "preview_retry",
        "package": package.model_dump(mode="json"),
        "cases": [
            {
                "case_id": item["case_id"],
                "candidate": {**item["automated"], "passed": True, "fallback": False},
            }
            for item in inputs
        ],
    }
    with pytest.raises(BuildInterrupted if mutation == "takeover" else ValueError):
        await JobStore(style_db, job["id"], "old").finish(result)
    assert await rows(style_db) == before
