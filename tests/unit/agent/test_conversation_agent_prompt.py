from slim_guard.agents.conversation.agent import CONVERSATION_PROMPT, CONVERSATION_PROMPT_VERSION


def test_conversation_prompt_considers_coach_reply_for_personal_health_sharing() -> None:
    assert CONVERSATION_PROMPT_VERSION == "conversation-v3"
    assert "不代替教练的关系性回应" in CONVERSATION_PROMPT
    assert "不要仅因其他细节仍不确定而让教练整轮沉默" in CONVERSATION_PROMPT
    assert "纯事实查询、必要澄清、安全风险" in CONVERSATION_PROMPT
