"""Service failures are explicit notices, never an unreviewed chat draft in disguise."""

from slim_guard.group_chat.composer import OutgoingTextMessage
from slim_guard.group_chat.contracts import ParticipantRole

REPLY_UNAVAILABLE = "暂时没能生成回复，请稍后再试；如涉及操作，请先查看实际记录，勿重复提交。"


def reply_failure_notice() -> OutgoingTextMessage:
    return OutgoingTextMessage(
        participant=ParticipantRole.SYSTEM_ASSISTANT,
        text=REPLY_UNAVAILABLE,
        notice_text=REPLY_UNAVAILABLE,
    )
