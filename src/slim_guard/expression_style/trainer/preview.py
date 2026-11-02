"""Versioned expression fixtures and honest replay cases, never model-generated exams."""

from typing import Any

from slim_guard.expression_style.package import content_hash

from .contracts import Material, MaterialAnalysis, PreviewSuite, TestCase

SUITE_VERSION = "expression-preview-v1"

# These are synthetic rewrite inputs, not scenario routing rules or medical evidence.
_FIXTURES = (
    ("greeting", "你好", "你好，我是称小二，可以帮你记录饮食、运动和体重。", ()),
    ("thanks", "谢谢你", "不用客气，很高兴能帮到你。", ()),
    ("balanced", "看看这顿搭配", "这顿包含主食、蔬菜和蛋白质来源，搭配比较完整。", ()),
    ("uncertain", "照片里是什么菜", "看起来像炒菌菇，但仅凭图片还不能确定具体种类。", ()),
    ("condition", "蛋白质够不够", "如果今天其他餐也没有蛋白质来源，今天的蛋白质摄入可能不足。", ()),
    (
        "number",
        "我今天称了体重",
        "今天记录的体重是 79.0 kg，单次变化还不能说明长期趋势。",
        ("79.0 kg",),
    ),
    ("failure", "刚才记录成功了吗", "这次记录没有保存成功，目前还不能确认已记录。", ()),
    (
        "boundary",
        "这能诊断出问题吗",
        "仅凭这些饮食记录不能作出诊断，有持续不适应咨询专业医生。",
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
                source_text=source,
                context=("合成的表达试答；中性原稿中的事实和条件已作为本题前提给定。",),
                protected_literals=literals,
            )
            for identifier, user, source, literals in _FIXTURES
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
            }
        )
        # Content-derived IDs avoid collisions across historical builds.
        unique.setdefault(key, case.model_copy(update={"id": f"preview:{key}"}))
    return PreviewSuite(version=frozen.version, cases=tuple(unique.values()))
