from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT, SLIM_GUARD_PROMPT_VERSION


def test_default_reply_style_is_conversational_and_non_templated() -> None:
    assert SLIM_GUARD_PROMPT_VERSION.endswith("v19")
    assert "真人教练一样说话" in SLIM_GUARD_HARNESS_PROMPT
    assert "普通打卡或资料更新通常回复一到三句" in SLIM_GUARD_HARNESS_PROMPT
    assert "不要逐字段报账" in SLIM_GUARD_HARNESS_PROMPT
    assert "不为了显得完整而主动扩写" in SLIM_GUARD_HARNESS_PROMPT
    assert "response_style" in SLIM_GUARD_HARNESS_PROMPT


def test_meal_images_default_to_today_and_only_ask_when_meal_type_stays_ambiguous() -> None:
    assert "否则默认属于当天，不要追问日期" in SLIM_GUARD_HARNESS_PROMPT
    assert "10:30–14:30 通常是午餐" in SLIM_GUARD_HARNESS_PROMPT
    assert "14:30–16:30 等边界时段不能仅凭时间硬猜" in SLIM_GUARD_HARNESS_PROMPT
    assert "authoritative_context.recent_meals" in SLIM_GUARD_HARNESS_PROMPT
    assert "你这是午餐还是晚餐" in SLIM_GUARD_HARNESS_PROMPT
    assert "不机械复述刚提交的数值和“已记录”" in SLIM_GUARD_HARNESS_PROMPT


def test_memory_prompt_allows_verified_historical_user_evidence_without_repetition() -> None:
    assert "role=user 且带 evidence_ref" in SLIM_GUARD_HARNESS_PROMPT
    assert "不要要求用户重新复述数值或固定句式" in SLIM_GUARD_HARNESS_PROMPT
    assert "助手消息、没有 evidence_ref 的摘要" in SLIM_GUARD_HARNESS_PROMPT
    assert "独立记忆摄取层" in SLIM_GUARD_HARNESS_PROMPT
    assert "从数据库重新读取的权威结果" in SLIM_GUARD_HARNESS_PROMPT
    assert "current_turn_memory_receipt" in SLIM_GUARD_HARNESS_PROMPT
    assert "不能把更新后的 profile_memory 值误说成更新前的旧值" in (
        SLIM_GUARD_HARNESS_PROMPT
    )
