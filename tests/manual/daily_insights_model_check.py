"""Opt-in synthetic model acceptance: uv run python tests/manual/daily_insights_model_check.py.

Uses configured provider credentials and consumes model tokens. No database or
real user data is read/written. This is not part of the default pytest suite.
"""

import asyncio
import time
from datetime import UTC, date, datetime

from slim_guard.agent_models.zhipu import ZhipuModelGateway
from slim_guard.config import Settings
from slim_guard.features.daily_insights.contracts import (
    InsightClaim,
    InsightDraft,
    InsightEvidence,
    InsightReview,
    InsightSnapshot,
    InsightText,
)
from slim_guard.features.daily_insights.generation import (
    REVIEW_PROMPT,
    InsightGenerator,
    InsightRejected,
)
from slim_guard.features.daily_insights.repository import InsightJob


def snapshot(name: str, records: tuple[InsightEvidence, ...]) -> InsightSnapshot:
    profile = InsightEvidence(
        id="profile:synthetic",
        kind="profile",
        label="合成资料",
        data={"target": "温和减重", "nickname": "小林", "preference": "不太能吃辣"},
    )
    scope = InsightEvidence(
        id="today:synthetic",
        kind="day_scope",
        label="本日已记录范围",
        data={
            "date": "2026-10-30",
            "timezone": "Asia/Shanghai",
            "record_refs": [r.id for r in records],
        },
    )
    return InsightSnapshot(
        user_id="synthetic-evaluation-only",
        day=date(2026, 10, 30),
        timezone="Asia/Shanghai",
        as_of=datetime(2026, 10, 30, 10, tzinfo=UTC),
        context_key="synthetic",
        evidence=(profile, *records, scope),
        style={},
        today_record_count=len(records),
        fingerprint=name,
    )


async def main() -> None:
    settings = Settings()
    if not settings.zhipu_api_key:
        raise RuntimeError("ZHIPU_API_KEY is required for this opt-in check")
    gateway = ZhipuModelGateway(
        api_key=settings.zhipu_api_key, base_url=settings.zhipu_base_url, timeout_seconds=20
    )
    generator = InsightGenerator(gateway, settings.zhipu_text_model)
    meal = InsightEvidence(
        id="record:meal:synthetic:r1",
        kind="meal",
        label="米饭、白菜、蒸蛋",
        today=True,
        data={
            "content": {
                "kind": "meal",
                "meal_type": "lunch",
                "foods": [{"name": x, "portion": None} for x in ["米饭", "白菜", "蒸蛋"]],
            },
            "recognition_issues": [],
        },
    )
    walk = InsightEvidence(
        id="record:exercise:synthetic:r1",
        kind="exercise",
        label="快走",
        today=True,
        data={
            "content": {
                "kind": "exercise",
                "activity_name": "快走",
                "duration_minutes": 30,
                "steps": 3200,
            }
        },
    )
    uncertain = meal.model_copy(
        update={
            "label": "米饭、菌菇、待确认肉类",
            "data": {
                "content": {
                    "kind": "meal",
                    "meal_type": "dinner",
                    "foods": [{"name": x, "portion": None} for x in ["米饭", "菌菇", "肉类"]],
                },
                "recognition_issues": [
                    {"field_ref": "foods/3/name", "reason": "遮挡，猪肉或牛肉不确定"}
                ],
            },
        }
    )

    async def check(name: str, records: tuple[InsightEvidence, ...]) -> None:
        start = time.monotonic()
        current = snapshot(name, records)
        try:
            output, review = await generator.generate(
                InsightJob(current, "running", name, None, None, None, None)
            )
        except InsightRejected as exc:
            print({"case": name, "rejected": exc.review.issues}, flush=True)
            raise
        assert review.passed
        assert (output.summary is not None) == bool(records)
        print(
            {
                "case": name,
                "seconds": round(time.monotonic() - start, 1),
                "headline": output.headline.text,
                "summary": output.summary.text if output.summary else None,
            },
            flush=True,
        )

    async def rejects_unsupported_nutrition() -> None:
        candidate = InsightDraft(
            headline=InsightText(text="今天想吃点什么？", claims=()),
            summary=InsightText(
                text="今天蛋白质摄入严重不足，应立即增加到每天120克。",
                claims=(
                    InsightClaim(
                        text="今天蛋白质摄入严重不足，应立即增加到每天120克。",
                        kind="assessment",
                        source_refs=(meal.id,),
                    ),
                ),
            ),
        )
        review = await generator._call(
            REVIEW_PROMPT,
            {
                "snapshot": snapshot("negative", (meal,)).model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
            },
            InsightReview,
        )
        assert isinstance(review, InsightReview) and not review.passed
        print({"case": "unsupported-protein-rejected", "issues": review.issues}, flush=True)

    async def accepts_qualified_scope() -> None:
        first = "目前已记录午餐为米饭、白菜和蒸蛋。"
        second = "这些记录不代表全天的完整摄入。"
        candidate = InsightDraft(
            headline=InsightText(text="今天想吃点什么？", claims=()),
            summary=InsightText(
                text=first + second,
                claims=(
                    InsightClaim(text=first, kind="observation", source_refs=(meal.id,)),
                    InsightClaim(text=second, kind="observation", source_refs=("today:synthetic",)),
                ),
            ),
        )
        review = await generator._call(
            REVIEW_PROMPT,
            {
                "snapshot": snapshot("scope-qualification", (meal,)).model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
            },
            InsightReview,
        )
        assert isinstance(review, InsightReview) and review.passed and not review.issues, review
        print(
            {"case": "qualified-scope-not-a-recording-demand", "passed": review.passed}, flush=True
        )

    try:
        results = await asyncio.gather(
            check("meal-and-walk", (meal, walk)),
            check("no-records", ()),
            check("uncertain-recognition", (uncertain,)),
            rejects_unsupported_nutrition(),
            accepts_qualified_scope(),
            return_exceptions=True,
        )
        assert not any(isinstance(value, BaseException) for value in results), results
    finally:
        await gateway.close()


if __name__ == "__main__":
    asyncio.run(main())
