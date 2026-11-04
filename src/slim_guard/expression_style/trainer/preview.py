"""Versioned expression fixtures and honest replay cases, never model-generated exams."""

from typing import Any

from slim_guard.expression_style.package import content_hash

from .contracts import Material, MaterialAnalysis, PreviewSuite, TestCase

SUITE_VERSION = "coach-direct-dialogue-preview-v3"

# Synthetic conversation fixtures: no ideal draft is given to the doctor.
_FIXTURES = (
    ("greeting", "你好", (), ("谢谢",)),
    ("exercise", "我今天不想运动", (), ("我就是不爱运动", "散步还行")),
    (
        "balanced",
        "看看这顿搭配",
        ("图片观察：米饭、清炒白菜、鸡蛋；份量不明。",),
        ("具体解释一下你为什么这么判断",),
    ),
    (
        "uncertain",
        "照片里是什么菜",
        ("图片观察：像炒菌菇，但不能确定具体食材。",),
        ("我说错了，其实是豆腐",),
    ),
    ("condition", "蛋白质够不够", ("用户只提供了一餐，没有份量或全天饮食数据。",), ()),
    (
        "number",
        "我今天称了体重，79.0公斤",
        (),
        (),
    ),
    ("quantity_check", "我今天喝了50升水。", (), ()),
    (
        "boundary",
        "这能诊断出问题吗",
        ("只有饮食记录，没有检查、症状或诊断信息。",),
        (),
    ),
)


def fixed_suite() -> PreviewSuite:
    return PreviewSuite(
        version=SUITE_VERSION,
        cases=tuple(
            TestCase(
                id=f"fixed:{identifier}",
                family=identifier,
                user_input=user,
                context=facts,
                follow_up_inputs=follow_ups,
            )
            for identifier, user, facts, follow_ups in _FIXTURES
        ),
    )


def replay_suite(
    frozen: PreviewSuite,
    materials: tuple[Material, ...],
    analyses: tuple[MaterialAnalysis, ...],
    feedback: list[dict[str, Any]],
) -> PreviewSuite:
    """Keep known examples labelled as replays, without claiming unseen-test accuracy."""
    usable = {a.example_id for a in analyses if a.role == "positive"}
    cases = list(frozen.cases)
    for material in materials:
        if material.id in usable:
            cases.append(
                TestCase(
                    id=f"material:{material.id}"[:100],
                    family="human-example",
                    origin="material",
                    user_input=material.user_input,
                    source_text=material.original_response,
                    history=material.history,
                )
            )
    for item in feedback:
        if item["review"]["decision"] == "reject":
            case = TestCase.model_validate(item["case"])
            cases.append(case.model_copy(update={"origin": "feedback"}))
    unique: dict[str, TestCase] = {}
    for case in cases:
        key = content_hash(
            {
                "user": case.user_input,
                "source": case.source_text,
                "context": case.context,
                "protected": case.protected_literals,
                "history": [m.model_dump(mode="json") for m in case.history],
                "follow_up_inputs": case.follow_up_inputs,
            }
        )
        # Content-derived IDs avoid collisions across historical builds.
        unique.setdefault(key, case.model_copy(update={"id": f"preview:{key}"}))
    return PreviewSuite(version=frozen.version, cases=tuple(unique.values()))
