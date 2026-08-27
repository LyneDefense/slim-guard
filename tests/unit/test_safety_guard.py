from __future__ import annotations

from slim_guard.harness.events import ItemStatus, ItemType
from slim_guard.harness.safety import (
    DefaultInputSafetyPolicy,
    HealthRiskLevel,
    SafetyAssessment,
    SlimGuardOutputGuard,
)
from slim_guard.harness.state_repository import ItemRef


def user_item(text: str) -> tuple[ItemRef, ...]:
    return (
        ItemRef(
            id="item-1",
            turn_id="turn-1",
            sequence=1,
            item_type=ItemType.USER_MESSAGE,
            status=ItemStatus.COMPLETED,
            payload={"text": text},
        ),
    )


def test_input_policy_hard_gates_explicit_emergency_and_minor_signals() -> None:
    policy = DefaultInputSafetyPolicy()

    emergency = policy.assess(user_item("我刚跑完步，现在胸痛而且呼吸困难"))
    minor = policy.assess(user_item("我今年15岁，想一个月减20斤"))
    normal = policy.assess(user_item("今天早上空腹77.6kg"))

    assert emergency.level is HealthRiskLevel.EMERGENCY
    assert emergency.code == "medical_emergency"
    assert emergency.blocks_tools is True
    assert minor.code == "minor"
    assert minor.blocks_tools is True
    assert normal.level is HealthRiskLevel.NORMAL
    assert normal.blocks_tools is False


def test_output_guard_replaces_diagnosis_and_dangerous_advice() -> None:
    guard = SlimGuardOutputGuard()
    normal = SafetyAssessment(HealthRiskLevel.NORMAL, "none", False)

    diagnosis = guard.review(
        text="你患有甲状腺疾病，建议每天服用20mg药物。",
        assessment=normal,
        tool_outcomes=(),
    )

    assert diagnosis.modified is True
    assert diagnosis.code == "prohibited_medical_advice"
    assert "不能提供疾病诊断" in diagnosis.text
