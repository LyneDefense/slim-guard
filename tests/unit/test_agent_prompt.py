from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT, SLIM_GUARD_PROMPT_VERSION


def test_core_prompt_is_short_and_keeps_conversation_out_of_core() -> None:
    assert SLIM_GUARD_PROMPT_VERSION.endswith("v35")
    assert "Conversation Agent" in SLIM_GUARD_HARNESS_PROMPT
    assert "CoachStyleRenderer" in SLIM_GUARD_HARNESS_PROMPT
    assert "不声称工具成功" in SLIM_GUARD_HARNESS_PROMPT
    assert "不输出原始工具调用文本" in SLIM_GUARD_HARNESS_PROMPT
    assert "真人教练一样说话" not in SLIM_GUARD_HARNESS_PROMPT
    assert len(SLIM_GUARD_HARNESS_PROMPT.splitlines()) < 40


def test_core_prompt_delegates_domain_parameters_to_contracts() -> None:
    assert "具体参数格式、可用值、权限和失败语义以本轮工具契约及工具返回为准" in (
        SLIM_GUARD_HARNESS_PROMPT
    )
    assert "不要在回复中重复工具手册" in SLIM_GUARD_HARNESS_PROMPT
    assert "工具参数必须来自用户原话" in SLIM_GUARD_HARNESS_PROMPT
    assert "必须先用原 operation_id 调用 get_tool_operation 核实" in SLIM_GUARD_HARNESS_PROMPT
    assert "告知用户暂勿重发" in SLIM_GUARD_HARNESS_PROMPT


def test_core_prompt_respects_explicit_meal_type_and_separate_submissions() -> None:
    assert "按用户明确提供的餐次记录" in SLIM_GUARD_HARNESS_PROMPT
    assert "即使餐次和菜品相同也分别记录" in SLIM_GUARD_HARNESS_PROMPT
    assert "只有用户明确表示是在更正某条已有记录时才更新那条记录" in (
        SLIM_GUARD_HARNESS_PROMPT
    )


def test_core_prompt_separates_verified_context_from_unverified_summaries() -> None:
    assert "已验证的工具结果或 Harness 提供的权威上下文" in SLIM_GUARD_HARNESS_PROMPT
    assert "不把图片观察、模型常识、旧助手回复或记忆摘要当成用户已经确认的事实" in (
        SLIM_GUARD_HARNESS_PROMPT
    )
    assert "工作记忆和待确认任务" in SLIM_GUARD_HARNESS_PROMPT
