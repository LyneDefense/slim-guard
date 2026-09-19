from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT, SLIM_GUARD_PROMPT_VERSION


def test_default_reply_style_is_conversational_and_non_templated() -> None:
    assert SLIM_GUARD_PROMPT_VERSION.endswith("v28")
    assert "真人教练一样说话" in SLIM_GUARD_HARNESS_PROMPT
    assert "普通打卡或资料更新通常回复一到三句" in SLIM_GUARD_HARNESS_PROMPT
    assert "不要逐字段报账" in SLIM_GUARD_HARNESS_PROMPT
    assert "不为了显得完整而主动扩写" in SLIM_GUARD_HARNESS_PROMPT
    assert "response_style" in SLIM_GUARD_HARNESS_PROMPT


def test_meal_images_default_to_today_and_only_ask_when_meal_type_stays_ambiguous() -> None:
    assert "否则默认属于当天，不要追问日期" in SLIM_GUARD_HARNESS_PROMPT
    assert "10:30–14:30 通常是午餐" in SLIM_GUARD_HARNESS_PROMPT
    assert "14:30–16:30 等边界时段不能仅凭时间硬猜" in SLIM_GUARD_HARNESS_PROMPT
    assert "近期饮食不会默认出现在上下文中" in SLIM_GUARD_HARNESS_PROMPT
    assert "get_recent_meals" in SLIM_GUARD_HARNESS_PROMPT
    assert "你这是午餐还是晚餐" in SLIM_GUARD_HARNESS_PROMPT
    assert "回复长度、是否复述记录" in SLIM_GUARD_HARNESS_PROMPT
    assert "record_meal 已经成功" not in SLIM_GUARD_HARNESS_PROMPT
    assert "不自动强制调用任何" in SLIM_GUARD_HARNESS_PROMPT
    assert "RAG 无相关证据时，可以由专业 Agent 使用低风险通识" in SLIM_GUARD_HARNESS_PROMPT


def test_memory_prompt_allows_verified_historical_user_evidence_without_repetition() -> None:
    assert "role=user 且带 evidence_ref" in SLIM_GUARD_HARNESS_PROMPT
    assert "不要要求用户重新复述数值或固定句式" in SLIM_GUARD_HARNESS_PROMPT
    assert "助手消息、没有 evidence_ref 的摘要" in SLIM_GUARD_HARNESS_PROMPT
    assert "最终回复持久化后由异步提取器处理" in SLIM_GUARD_HARNESS_PROMPT
    assert "remember_long_term_memory" in SLIM_GUARD_HARNESS_PROMPT
    assert "PostgreSQL 中的结构化 Profile" in SLIM_GUARD_HARNESS_PROMPT
    assert "Mem0 语义召回" in SLIM_GUARD_HARNESS_PROMPT
