from __future__ import annotations

from dataclasses import dataclass

from slim_guard.integrations.wecom_kf.schemas import SyncPage


@dataclass(frozen=True, slots=True)
class SentText:
    external_userid: str
    open_kfid: str
    content: str
    msgid: str


class FakeWeComClient:
    def __init__(self, pages: dict[str | None, SyncPage]) -> None:
        self.pages = pages
        self.sync_cursors: list[str | None] = []
        self.sent: list[SentText] = []

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
