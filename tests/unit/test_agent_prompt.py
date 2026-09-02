from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT, SLIM_GUARD_PROMPT_VERSION


def test_default_reply_style_is_conversational_and_non_templated() -> None:
    assert SLIM_GUARD_PROMPT_VERSION.endswith("v16")
    assert "真人教练一样说话" in SLIM_GUARD_HARNESS_PROMPT
    assert "普通打卡或资料更新通常回复一到三句" in SLIM_GUARD_HARNESS_PROMPT
    assert "不要逐字段报账" in SLIM_GUARD_HARNESS_PROMPT
    assert "不为了显得完整而主动扩写" in SLIM_GUARD_HARNESS_PROMPT
    assert "response_style" in SLIM_GUARD_HARNESS_PROMPT


def test_memory_prompt_allows_verified_historical_user_evidence_without_repetition() -> None:
    assert "role=user 且带 evidence_ref" in SLIM_GUARD_HARNESS_PROMPT
    assert "不要要求用户重新复述数值或固定句式" in SLIM_GUARD_HARNESS_PROMPT
    assert "助手消息、没有 evidence_ref 的摘要" in SLIM_GUARD_HARNESS_PROMPT
