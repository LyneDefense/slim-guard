from slim_guard.agents.conversation.agent import CONVERSATION_PROMPT, CONVERSATION_PROMPT_VERSION


def test_conversation_prompt_considers_coach_reply_for_personal_health_sharing() -> None:
    assert CONVERSATION_PROMPT_VERSION == "conversation-v6-coach-default"
    assert "医生教练是默认对话入口" in CONVERSATION_PROMPT
    assert "不要仅因其他细节仍不确定而让教练整轮沉默" in CONVERSATION_PROMPT
    assert "内部检索、读取上下文或咨询专家不等于需要通知用户" in CONVERSATION_PROMPT
    assert "不要求每轮出现系统消息" in CONVERSATION_PROMPT
    assert "必须恰好包含一条 system_assistant" not in CONVERSATION_PROMPT
