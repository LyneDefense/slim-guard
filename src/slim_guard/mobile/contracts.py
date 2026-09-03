from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OtpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=6, max_length=32)


class OtpChallengeView(BaseModel):
    challenge_id: str
    expires_in_seconds: int
    retry_after_seconds: int
    debug_code: str | None = None


class OtpVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(min_length=1, max_length=64)
    code: str = Field(pattern=r"^\d{6}$")
    device_label: str | None = Field(default=None, max_length=128)

    @field_validator("device_label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class TestAccountView(BaseModel):
    username: str
    default_nickname: str


class AuthOptionsView(BaseModel):
    phone_login_enabled: bool = True
    test_account_login_enabled: bool
    test_accounts: list[TestAccountView]


class PasswordLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    device_label: str | None = Field(default=None, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Username must not be blank")
        return normalized

    @field_validator("device_label")
    @classmethod
    def normalize_password_login_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=40, max_length=256)


class MobileUserView(BaseModel):
    id: str
    nickname: str | None
    identity_hint: str | None
    created_at: datetime


class AuthTokenView(BaseModel):
    token_type: Literal["Bearer"] = "Bearer"
    access_token: str
    expires_in_seconds: int
    refresh_token: str
    user: MobileUserView


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(default=None, max_length=80)

    @field_validator("nickname")
    @classmethod
    def normalize_nickname(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=20_000)
    image_base64: str | None = Field(default=None, max_length=28_000_000)
    image_mime_type: Literal["image/jpeg", "image/png", "image/webp"] | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)
    occurred_at: datetime | None = None

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_content(self) -> ChatRequest:
        if self.text is None and self.image_base64 is None:
            raise ValueError("A chat request requires text or an image")
        if self.image_base64 is not None and self.image_mime_type is None:
            raise ValueError("Image MIME type is required with image content")
        if self.image_mime_type is not None and self.image_base64 is None:
            raise ValueError("Image content is required with an image MIME type")
        if self.occurred_at is not None and self.occurred_at.utcoffset() is None:
            raise ValueError("Chat occurrence time must be timezone-aware")
        return self


class ChatResponse(BaseModel):
    request_id: str
    status: Literal["running", "succeeded", "failed"]
    turn_id: str | None = None
    text: str | None = None
    failure_code: str | None = None
    replayed: bool = False


class ChatMessageView(BaseModel):
    id: str
    turn_id: str
    role: Literal["user", "assistant"]
    kind: Literal["text", "image"]
    text: str | None
    created_at: datetime


class ChatHistoryView(BaseModel):
    items: list[ChatMessageView]


class MemoryView(BaseModel):
    id: str
    key: str
    kind: str
    value: dict[str, object]
    stale: bool
    valid_from: datetime
    review_after: datetime | None


class RoutineSettingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    local_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")


class RoutineUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str | None = Field(default=None, max_length=64)
    weight: RoutineSettingRequest | None = None
    meal: RoutineSettingRequest | None = None
    daily_review: RoutineSettingRequest | None = None


class RoutineView(BaseModel):
    timezone: str
    weight_reminder_time: str | None
    meal_reminder_time: str | None
    daily_review_time: str | None


class TrendPoint(BaseModel):
    id: str
    value: float
    occurred_at: datetime


class ProgressView(BaseModel):
    weights: list[TrendPoint]
    body_fat: list[TrendPoint]
    meals: list[dict[str, object]]
    exercise: list[dict[str, object]]


class TodayView(BaseModel):
    date: str
    current_weight_kg: float | None
    current_body_fat_percent: float | None
    meals_logged: int
    exercise_logged: int
    memories: list[MemoryView]
    routine: RoutineView


class DeviceRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: str = Field(min_length=8, max_length=128)
    platform: Literal["ios", "android"]
    push_provider: Literal["expo", "apns", "fcm"] = "expo"
    push_token: str = Field(min_length=16, max_length=512)
    app_version: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=32)


class DeviceView(BaseModel):
    id: str
    installation_id: str
    platform: Literal["ios", "android"]
    push_provider: Literal["expo", "apns", "fcm"]
    app_version: str | None
    timezone: str | None
    locale: str | None
    active: bool
    last_seen_at: datetime


class WeComBindingView(BaseModel):
    id: str
    status: Literal["pending", "claimed", "expired", "revoked", "conflict"]
    code: str | None = None
    code_hint: str
    expires_at: datetime
    claimed_at: datetime | None


class AccountDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["DELETE"]
