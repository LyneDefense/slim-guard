from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SyncMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    msgid: str
    open_kfid: str
    external_userid: str | None = None
    send_time: int = Field(ge=0)
    origin: int
    servicer_userid: str | None = None
    msgtype: str
    text: dict[str, Any] | None = None


class SyncPage(BaseModel):
    next_cursor: str | None = None
    has_more: bool = False
    msg_list: list[SyncMessage] = Field(default_factory=list)


class KfAccount(BaseModel):
    open_kfid: str
    name: str
    avatar: str | None = None
