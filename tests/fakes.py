from __future__ import annotations

from dataclasses import dataclass

from slim_guard.integrations.wecom_kf.schemas import (
    ServiceStateSnapshot,
    ServiceStateTransition,
    SyncPage,
)
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState


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
    ) -> None:
        self.pages = pages
        self.sync_cursors: list[str | None] = []
        self.sent: list[SentText] = []
        self.service_states = service_states or {}
        self.transitions: list[StateTransition] = []
        self.sent_events: list[SentEventText] = []

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
