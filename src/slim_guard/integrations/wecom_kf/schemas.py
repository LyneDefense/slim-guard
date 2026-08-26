from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SyncMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    msgid: str
    external_userid: str | None = None
    send_time: int = Field(ge=0)
    origin: int
    servicer_userid: str | None = None
    msgtype: str
    text: dict[str, Any] | None = None
    image: dict[str, Any] | None = None


class SyncPage(BaseModel):
    next_cursor: str | None = None
    has_more: bool = False
    msg_list: list[SyncMessage] = Field(default_factory=list)


class KfAccount(BaseModel):
    open_kfid: str
    name: str
    avatar: str | None = None


class ServiceStateSnapshot(BaseModel):
    service_state: int
    servicer_userid: str | None = None


class ServiceStateTransition(BaseModel):
    msg_code: str | None = None


class CustomerProfile(BaseModel):
    external_userid: str
    nickname: str | None = None
    avatar: str | None = None
    gender: int | None = None
    unionid: str | None = None


class CustomerProfileBatch(BaseModel):
    customer_list: list[CustomerProfile] = Field(default_factory=list)
    invalid_external_userid: list[str] = Field(default_factory=list)
