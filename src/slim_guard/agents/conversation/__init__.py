"""Conversation Agent for the three-party user/coach/system chat."""

from slim_guard.agents.conversation.agent import (
    CONVERSATION_PROMPT,
    CONVERSATION_PROMPT_VERSION,
    ConversationAgent,
    ConversationAgentResult,
)
from slim_guard.agents.conversation.contracts import (
    ConversationDecision,
    ConversationMessageProposal,
)

__all__ = [
    "CONVERSATION_PROMPT",
    "CONVERSATION_PROMPT_VERSION",
    "ConversationAgent",
    "ConversationAgentResult",
    "ConversationDecision",
    "ConversationMessageProposal",
]
