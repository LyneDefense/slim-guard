from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def detect_image_mime_type(content: bytes, declared: str | None = None) -> str:
    normalized = (declared or "").split(";", 1)[0].strip().lower()
    detected: str | None = None
    if content.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif content.startswith((b"GIF87a", b"GIF89a")):
        detected = "image/gif"
    elif len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        detected = "image/webp"
    if detected is None:
        raise ValueError("Unsupported or unrecognized image format")
    if normalized and normalized != detected:
        raise ValueError("Declared image type does not match its content")
    return detected


class SaveImageAssetCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    content: bytes = Field(min_length=1, max_length=20_971_520)
    declared_mime_type: str | None = Field(default=None, max_length=128)
    channel_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_message_id: str | None = Field(default=None, min_length=1, max_length=256)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_source_and_expiry(self) -> Self:
        if (self.channel_id is None) != (self.source_message_id is None):
            raise ValueError("Image source channel and message ID must be supplied together")
        if self.expires_at.utcoffset() is None:
            raise ValueError("Image asset expiry must be timezone-aware")
        detect_image_mime_type(self.content, self.declared_mime_type)
        return self

    @property
    def mime_type(self) -> str:
        return detect_image_mime_type(self.content, self.declared_mime_type)


@dataclass(frozen=True, slots=True)
class ImageAssetRef:
    id: str
    user_id: str
    content_sha256: str
    mime_type: str
    size_bytes: int
    channel_id: str | None
    source_message_id: str | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ImageAsset:
    ref: ImageAssetRef
    content: bytes


@dataclass(frozen=True, slots=True)
class ImageAssetCreation:
    asset: ImageAssetRef
    created: bool
