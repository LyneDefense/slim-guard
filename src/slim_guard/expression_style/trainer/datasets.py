"""Independent test generation and isolation from material/consumed test families."""

import re
from difflib import SequenceMatcher
from typing import Any

from .client import TrainingClient
from .contracts import ConsistencyCheck, Material, TestSuite


def normalized_input(value: str) -> str:
    return re.sub(r"[\W\d_]+", "", value.casefold())


def validate_suite(
    suite: TestSuite, materials: tuple[Material, ...], consumed: list[dict[str, Any]]
) -> None:
    cases = (*suite.development, *suite.acceptance)
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("测试 ID 重复")
    if {c.family for c in suite.development} & {c.family for c in suite.acceptance}:
        raise ValueError("开发题与最终验收题题族重叠")
    old_families = {c.get("family") for c in consumed}
    if old_families & {c.family for c in suite.acceptance}:
        raise ValueError("已消费题族不能再次标记为独立验收")
    seen = [normalized_input(m.user_input) for m in materials]
    seen.extend(normalized_input(c.get("user_input", "")) for c in consumed)
    for case in cases:
        key = normalized_input(case.user_input)
        if not key or any(s and SequenceMatcher(None, s, key).ratio() >= 0.9 for s in seen):
            raise ValueError("测试包含素材复述、重复题或仅替换数字的题")
        seen.append(key)
        if any(v not in case.source_text for v in case.protected_literals):
            raise ValueError("测试保护内容不在中性原稿中")


async def prepare_suite(
    client: TrainingClient,
    materials: tuple[Material, ...],
    consumed: list[dict[str, Any]],
) -> TestSuite:
    exclusions = {
        "excluded_inputs": [m.user_input for m in materials],
        "consumed_tests": [
            {"family": c.get("family"), "user_input": c.get("user_input")} for c in consumed
        ],
    }
    issues: list[str] = []
    previous: dict[str, Any] | None = None
    for attempt in range(1, 3):
        suite = await client.ask(
            TestSuite,
            "独立构建中文健康管理助手的表达验收题。生成6-10条 development 和12条 acceptance。"
            "不按意图标签分类，每题使用自然用户输入、必要已知语境及正确中性原稿。"
            "覆盖身份、记录成功/失败、条件、否定、数值、风险、不确定性、简短和多段表达。"
            "原稿只依据题目事实，不制定新医学处方；没有调用真实业务工具。"
            "两集合题族隔离，不能只换数字、人名或菜名。图片只写为合成描述。"
            "不复述给出的排除输入/已用题族；没有目标风格或参考答案，不针对候选定制题目。",
            {**exclusions, "repair_issues": issues, "rejected_suite": previous},
            stage="datasets",
        )
        previous = suite.model_dump(mode="json")
        try:
            validate_suite(suite, materials, consumed)
        except ValueError as exc:
            issues = [str(exc)]
        else:
            checked = await client.ask(
                ConsistencyCheck,
                "审查这些测试的原稿是否得到用户输入和上下文支持，是否有自相矛盾或杜撰操作结果；"
                "同时核对 development/acceptance 是否存在语义近重复、相同题族。"
                "也要检查与 excluded_inputs/consumed_tests 的语义重叠，不只看字面。"
                "合成题可在 context 明确假设记录成功或失败，不要求真实执行工具。"
                "passed=true 必须 issues=[]，否则给出可核对的问题，不改写题目。",
                {"suite": previous, **exclusions},
                stage="datasets",
            )
            issues = list(checked.issues) if not checked.passed or checked.issues else []
            if not checked.passed and not issues:
                issues = ["测试审查未通过但没有提供原因"]
        await client.port.save(
            "dataset_audit",
            {"attempt": attempt, "passed": not issues, "issues": issues, "suite": previous},
            stage="datasets",
            message=f"独立测试第 {attempt} 次质量检查：{'通过' if not issues else '需修复'}",
        )
        if not issues:
            await client.port.save(
                "suite",
                suite.model_dump(mode="json"),
                stage="datasets",
                message=f"已冻结开发题 {len(suite.development)} 条、"
                f"独立验收题 {len(suite.acceptance)} 条",
            )
            return suite
    raise ValueError("独立测试两次质量检查仍未通过，请新建构建：" + "；".join(issues))
