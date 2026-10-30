import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.features.daily_insights.contracts import (
    InsightClaim,
    InsightDraft,
    InsightEvidence,
    InsightReview,
    InsightSnapshot,
    InsightText,
    PersonalContext,
)
from slim_guard.features.daily_insights.generation import (
    InsightGenerator,
    reusable_headline,
    validate_draft,
)
from slim_guard.features.daily_insights.models import DailyInsightRecord
from slim_guard.features.daily_insights.repository import InsightJob, InsightRepository
from slim_guard.features.daily_insights.service import DailyInsightsService
from slim_guard.features.daily_insights.snapshot import SnapshotReader
from slim_guard.features.health_records.queries import RecordPage
from slim_guard.features.health_records.views import RecordView

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def record(identity="meal1", revision=1, *, name="米饭", day="2026-10-30", issue=False):
    value = RecordView.model_validate(
        {
            "ref": {"kind": "meal", "record_id": identity, "revision": revision},
            "status": "active",
            "occurrence": {
                "precision": "date",
                "on": day,
                "timezone": "Asia/Shanghai",
                "basis": "user_reported",
            },
            "recorded_at": NOW,
            "updated_at": NOW,
            "content": {
                "kind": "meal",
                "meal_type": "lunch",
                "foods": [{"item_id": "f1", "name": name}],
            },
        }
    )
    return value


class Context:
    timezone = "Asia/Shanghai"
    evidence = ()

    async def read(self, user_id):
        return PersonalContext(timezone=self.timezone, evidence=self.evidence)


class Records:
    def __init__(self, items=()):
        self.items, self.calls = list(items), []

    async def search(self, user_id, query):
        self.calls.append((user_id, query))
        if query.kind != "meal":
            return RecordPage(records=(), next_cursor=None, has_more=False)
        items = [r for r in self.items if query.from_date <= r.occurrence.on <= query.to_date]
        start = int(query.cursor or "0")
        page = items[start : start + query.limit]
        more = start + query.limit < len(items)
        return RecordPage(
            records=tuple(page),
            has_more=more,
            next_cursor=str(start + query.limit) if more else None,
        )


def draft(snapshot, headline="今天吃什么？"):
    facts = [e for e in snapshot.evidence if e.today]
    return InsightDraft(
        headline=InsightText(text=headline, claims=()),
        summary=InsightText(
            text="今天记录了米饭。",
            claims=(
                InsightClaim(
                    text="今天记录了米饭。", kind="observation", source_refs=(facts[0].id,)
                ),
            ),
        )
        if facts
        else None,
    )


def job(snapshot, previous=None, previous_snapshot=None):
    return InsightJob(snapshot, "running", "token", previous, previous_snapshot, None, None)


class Model:
    def __init__(self):
        self.requests = []
        self.bad_first = False
        self.reject = False
        self.style_suggestions = ()

    async def complete(self, request):
        self.requests.append(request)
        payload = json.loads(request.messages[-1].content)
        if request.output_schema_name == "InsightReview":
            value = InsightReview(
                passed=not self.reject,
                issues=("unsupported_nutrient_amount",) if self.reject else (),
                style_suggestions=self.style_suggestions,
            ).model_dump()
        elif self.bad_first and len(self.requests) == 1:
            value = {"headline": 123}
        else:
            snapshot = InsightSnapshot.model_validate(
                {
                    **{k: v for k, v in payload.items() if k in InsightSnapshot.model_fields},
                    "user_id": "u1",
                    "fingerprint": "x",
                }
            )
            value = draft(snapshot).model_dump()
        return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))


@pytest.fixture
async def env(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'insights.sqlite'}")
    async with db.engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        await connection.run_sync(lambda sync: SlimGuardUser.__table__.create(sync))
        await connection.run_sync(lambda sync: DailyInsightRecord.__table__.create(sync))
    async with db.session() as session, session.begin():
        session.add_all(
            [SlimGuardUser(id=u, first_seen_at=NOW, last_seen_at=NOW) for u in ("u1", "u2")]
        )
    records, context = Records([record()]), Context()
    snapshots = SnapshotReader(records, context)
    repo, model = InsightRepository(db), Model()
    clock = [NOW]
    service = DailyInsightsService(
        snapshots, repo, InsightGenerator(model, "test"), clock=lambda: clock[0]
    )
    try:
        yield db, records, context, repo, model, service, clock
    finally:
        await db.close()


async def test_read_is_fast_and_generation_is_independent_cached_and_persisted(env):
    db, records, context, repo, model, service, clock = env
    pending = await service.get("u1")
    assert pending.status == "pending" and not model.requests
    assert await service.run_once()
    ready = await service.get("u1")
    assert ready.status == "ready" and ready.headline == "今天吃什么？"
    assert ready.summary == "今天记录了米饭。" and ready.evidence[0].label == "米饭"
    assert len(model.requests) == 2
    assert not await service.run_once()
    assert (await service.get("u1")).status == "ready"
    async with db.session() as session:
        row = await session.get(DailyInsightRecord, ("u1", date(2026, 10, 30)))
        assert row.review_json and row.output_snapshot_json
    # Navigation/restarted services can use the durable result without a model call.
    restarted = DailyInsightsService(
        service.snapshots, repo, service.generator, clock=service.clock
    )
    assert (await restarted.get("u1")).summary == ready.summary
    assert len(model.requests) == 2


async def test_snapshot_reads_more_than_one_hundred_records_and_uses_local_day():
    records, context = Records([record(str(i)) for i in range(131)]), Context()
    reader = SnapshotReader(records, context)
    snapshot = await reader.read("u1", datetime(2026, 10, 29, 16, 5, tzinfo=UTC))
    assert snapshot.day == date(2026, 10, 30) and snapshot.today_record_count == 131
    assert (
        len([q for _, q in records.calls if q.kind == "meal" and q.from_date == snapshot.day]) == 2
    )


@pytest.mark.parametrize("mutation", ["edit", "delete", "new", "timezone"])
async def test_changed_sources_invalidate_the_summary_before_model_regeneration(env, mutation):
    _, records, context, _, model, service, clock = env
    await service.get("u1")
    await service.run_once()
    if mutation == "edit":
        records.items = [record(revision=2, name="白菜")]
    elif mutation == "delete":
        records.items = []
    elif mutation == "new":
        records.items.append(record("meal2"))
    else:
        context.timezone = "America/Los_Angeles"
    changed = await service.get("u1")
    assert changed.status == "pending" and changed.summary is None
    assert len(model.requests) == 2


async def test_different_owners_never_share_a_generation_or_evidence(env):
    _, _, _, repo, model, service, _ = env
    await service.get("u1")
    await service.run_once()
    assert (await service.get("u2")).status == "pending"
    assert (await service.get("u2")).summary is None
    assert len(model.requests) == 2


async def test_zero_today_records_never_invents_a_daily_assessment(env):
    _, records, _, _, _, service, _ = env
    records.items = [record(day="2026-10-29")]
    assert (await service.get("u1")).summary_state == "empty"
    await service.run_once()
    assert (await service.get("u1")).summary is None
    assert (await service.get("u1")).headline


async def test_generator_repairs_schema_errors_and_records_semantic_review_feedback(env):
    _, _, _, _, model, service, _ = env
    model.bad_first = True
    await service.get("u1")
    await service.run_once()
    assert (await service.get("u1")).status == "ready"
    repair = json.loads(model.requests[1].messages[-1].content)
    assert repair["repair_feedback"]
    assert all(r.tools == () and r.tool_choice.value == "none" for r in model.requests)


async def test_semantic_rejection_never_publishes_an_unsupported_nutrient_judgement(env):
    db, _, _, _, model, service, _ = env
    model.reject = True
    await service.get("u1")
    await service.run_once()
    result = await service.get("u1")
    assert result.status == "failed" and result.summary is None
    assert result.failure_code == "review_failed"
    async with db.session() as session:
        row = await session.get(DailyInsightRecord, ("u1", NOW.date()))
        assert json.loads(row.review_json)["issues"] == ["unsupported_nutrient_amount"]
    assert len(model.requests) == 4
    assert json.loads(model.requests[2].messages[-1].content)["repair_feedback"] == [
        "unsupported_nutrient_amount"
    ]
    assert not await service.run_once()
    assert (await service.get("u1", retry=True)).status == "pending"


@pytest.mark.parametrize("unsafe", [False, True])
async def test_tone_advice_is_audited_without_overriding_evidence_and_safety(env, unsafe):
    db, _, _, _, model, service, _ = env
    model.style_suggestions = ("可以更简洁，但客观限定记录范围不等于要求用户补录",)
    model.reject = unsafe
    await service.get("u1")
    await service.run_once()
    result = await service.get("u1")
    assert result.status == ("failed" if unsafe else "ready")
    assert bool(result.summary) is not unsafe
    if not unsafe:
        assert len(model.requests) == 2  # No business retry for a stylistic preference.
        async with db.session() as session:
            row = await session.get(DailyInsightRecord, ("u1", NOW.date()))
            assert json.loads(row.review_json)["style_suggestions"] == list(model.style_suggestions)


async def test_stale_worker_cannot_overwrite_a_newer_snapshot_or_newer_lease(env):
    _, records, _, repo, _, service, clock = env
    first = await service.snapshots.read("u1", NOW)
    await repo.request(first, NOW)
    old = await repo.claim(NOW)
    assert old and not await repo.claim(NOW)
    clock[0] = NOW + timedelta(minutes=3)
    newer = await repo.claim(clock[0])
    assert newer and newer.token != old.token
    assert not await repo.finish(old, clock[0], output=draft(first))
    records.items.append(record("meal2"))
    latest = await service.snapshots.read("u1", clock[0])
    await repo.request(latest, clock[0])
    assert not await repo.finish(newer, clock[0], output=draft(first))


async def test_foreign_or_missing_references_and_nonliteral_claims_are_rejected(env):
    snapshot = await env[5].snapshots.read("u1", NOW)
    output = draft(snapshot)
    for refs, text in [
        (("foreign",), output.summary.text),
        ((), output.summary.text),
        ((snapshot.evidence[0].id,), "不在正文中的话"),
    ]:
        candidate = output.model_copy(
            update={
                "summary": output.summary.model_copy(
                    update={
                        "claims": (InsightClaim(text=text, kind="observation", source_refs=refs),)
                    }
                )
            }
        )
        with pytest.raises(ValueError):
            validate_draft(candidate, snapshot)


async def test_headline_is_stable_on_additions_but_changes_when_its_own_fact_changes(env):
    _, records, _, _, _, service, _ = env
    old = await service.snapshots.read("u1", NOW)
    first = draft(old)
    record_ref = next(e.id for e in old.evidence if e.today)
    first = first.model_copy(
        update={
            "headline": InsightText(
                text="这顿看着挺香。",
                claims=(
                    InsightClaim(
                        text="这顿看着挺香。", kind="assessment", source_refs=(record_ref,)
                    ),
                ),
            )
        }
    )
    records.items.append(record("meal2"))
    current = await service.snapshots.read("u1", NOW)
    assert reusable_headline(job(old, first, old), current) == first.headline
    records.items[0] = record(revision=2, name="白菜")
    current = await service.snapshots.read("u1", NOW)
    assert reusable_headline(job(old, first, old), current) is None


async def test_snapshot_fingerprint_and_evidence_are_stable_across_json_round_trip(env):
    _, _, context, _, _, service, _ = env
    context.evidence = (
        InsightEvidence(id="profile:1", kind="profile", label="资料", data={"at": NOW}),
    )
    snapshot = await service.snapshots.read("u1", NOW)
    restored = InsightSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored.evidence == snapshot.evidence
    assert (
        await service.snapshots.read("u1", NOW + timedelta(minutes=1))
    ).fingerprint == snapshot.fingerprint


async def test_deleting_the_owner_cascades_cached_profile_and_assessment_data(env):
    db, _, _, _, _, service, _ = env
    await service.get("u1")
    await service.run_once()
    async with db.session() as session, session.begin():
        await session.execute(delete(SlimGuardUser).where(SlimGuardUser.id == "u1"))
    async with db.session() as session:
        assert await session.scalar(select(DailyInsightRecord)) is None


async def test_edit_during_generation_without_polling_cannot_publish_stale_summary(env):
    _, records, _, _, _, service, clock = env
    await service.get("u1")
    started, release = asyncio.Event(), asyncio.Event()
    original = service.generator.generate

    async def slow(job):
        started.set()
        await release.wait()
        return await original(job)

    service.generator.generate = slow
    task = asyncio.create_task(service.run_once())
    await started.wait()
    records.items[0] = record(revision=2, name="白菜")
    release.set()
    assert await task
    current = await service.get("u1")
    assert current.status == "pending" and current.summary is None
    clock[0] += timedelta(seconds=3)
    assert await service.run_once()


async def test_timeout_is_local_and_cancellation_leaves_a_recoverable_lease(env):
    _, _, _, repo, _, service, clock = env
    await service.get("u1")
    original = service.generator.generate

    async def timeout(job):
        raise TimeoutError("provider timeout")

    service.generator.generate = timeout
    assert await service.run_once()
    assert (await service.get("u1")).failure_code == "generation_failed"
    await service.get("u1", retry=True)
    started = asyncio.Event()

    async def blocking(job):
        started.set()
        await asyncio.Event().wait()

    service.generator.generate = blocking
    task = asyncio.create_task(service.run_once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await repo.claim(clock[0]) is None
    service.generator.generate = original
    clock[0] += timedelta(minutes=3)
    assert await service.run_once()
    assert (await service.get("u1")).status == "ready"


async def test_profile_memory_and_generation_version_invalidate_cached_copy(env):
    _, _, context, _, _, service, _ = env
    await service.get("u1")
    await service.run_once()
    context.evidence = (
        InsightEvidence(id="memory:new", kind="memory", label="习惯", data={"text": "不吃辣"}),
    )
    changed = await service.get("u1")
    assert changed.status == "pending" and changed.headline is None
    previous = await service.snapshots.read("u1", NOW)
    service.snapshots.version = "daily-insights-next-model"
    assert (await service.snapshots.read("u1", NOW)).fingerprint != previous.fingerprint


async def test_unconfigured_model_never_schedules_jobs(env):
    _, _, _, repo, _, service, _ = env
    service.generator = None
    view = await service.get("u1")
    assert view.status == "unavailable" and view.summary is None
    assert await repo.claim(NOW) is None
