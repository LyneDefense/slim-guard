from __future__ import annotations

from dataclasses import dataclass

from slim_guard.integrations.wecom_kf.client import WeComMedia
from slim_guard.integrations.wecom_kf.schemas import (
    CustomerProfile,
    CustomerProfileBatch,
    ServiceStateSnapshot,
    ServiceStateTransition,
    SyncPage,
)
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState
from slim_guard.services.reply_agent import ReplyRequest


@dataclass(frozen=True, slots=True)
class SentText:
    external_userid: str
    open_kfid: str
    content: str
    msgid: str


@dataclass(frozen=True, slots=True)
class StateTransition:
    external_userid: str
    service_state: WeComServiceState


@dataclass(frozen=True, slots=True)
class SentEventText:
    code: str
    content: str
    msgid: str


class FakeWeComClient:
    def __init__(
        self,
        pages: dict[str | None, SyncPage],
        *,
        service_states: dict[str, WeComServiceState] | None = None,
        customer_profiles: dict[str, CustomerProfile] | None = None,
        media: dict[str, WeComMedia] | None = None,
    ) -> None:
        self.pages = pages
        self.sync_cursors: list[str | None] = []
        self.sent: list[SentText] = []
        self.service_states = service_states or {}
        self.transitions: list[StateTransition] = []
        self.sent_events: list[SentEventText] = []
        self.customer_profiles = customer_profiles or {}
        self.customer_profile_requests: list[list[str]] = []
        self.media = media or {}
        self.media_requests: list[str] = []

    async def sync_messages(
        self,
        *,
        callback_token: str,
        open_kfid: str,
        cursor: str | None,
    ) -> SyncPage:
        assert callback_token
        assert open_kfid == "wk-test"
        self.sync_cursors.append(cursor)
        return self.pages[cursor]

    async def send_text(
        self,
        *,
        external_userid: str,
        open_kfid: str,
        content: str,
        msgid: str,
    ) -> None:
        self.sent.append(
            SentText(
                external_userid=external_userid,
                open_kfid=open_kfid,
                content=content,
                msgid=msgid,
            )
        )

    async def get_service_state(
        self, *, external_userid: str, open_kfid: str
    ) -> ServiceStateSnapshot:
        assert open_kfid == "wk-test"
        return ServiceStateSnapshot(
            service_state=int(
                self.service_states.get(external_userid, WeComServiceState.UNPROCESSED)
            )
        )

    async def transition_service_state(
        self,
        *,
        external_userid: str,
        open_kfid: str,
        service_state: WeComServiceState,
    ) -> ServiceStateTransition:
        assert open_kfid == "wk-test"
        self.service_states[external_userid] = service_state
        self.transitions.append(
            StateTransition(
                external_userid=external_userid,
                service_state=service_state,
            )
        )
        code = "timeout-code" if service_state is WeComServiceState.ENDED else None
        return ServiceStateTransition(msg_code=code)

    async def send_event_text(self, *, code: str, content: str, msgid: str) -> None:
        self.sent_events.append(SentEventText(code=code, content=content, msgid=msgid))

    async def get_customer_profiles(self, *, external_userids: list[str]) -> CustomerProfileBatch:
        self.customer_profile_requests.append(external_userids)
        return CustomerProfileBatch(
            customer_list=[
                self.customer_profiles.get(
                    external_userid,
                    CustomerProfile(external_userid=external_userid),
                )
                for external_userid in external_userids
            ]
        )

    async def download_media(self, *, media_id: str, max_bytes: int) -> WeComMedia:
        self.media_requests.append(media_id)
        result = self.media[media_id]
        assert len(result.content) <= max_bytes
        return result


class FakeReplyAgent:
    def __init__(self, reply: str = "AI reply") -> None:
        self.reply = reply
        self.requests: list[ReplyRequest] = []

    async def generate_reply(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        return self.reply
