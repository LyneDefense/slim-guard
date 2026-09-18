from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
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


class CoachAgeBand(StrEnum):
    AGE_0_9 = "0_9"
    AGE_10_17 = "10_17"
    AGE_18_29 = "18_29"
    AGE_30_39 = "30_39"
    AGE_40_49 = "40_49"
    AGE_50_59 = "50_59"
    AGE_60_69 = "60_69"
    AGE_70_79 = "70_79"
    AGE_80_PLUS = "80_plus"


class CoachGoalType(StrEnum):
    LOSE_WEIGHT = "lose_weight"
    MAINTAIN_WEIGHT = "maintain_weight"
    IMPROVE_HABITS = "improve_habits"


class CoachExerciseFrequency(StrEnum):
    RARELY = "rarely"
    WEEKLY_1_2 = "weekly_1_2"
    WEEKLY_3_4 = "weekly_3_4"
    WEEKLY_5_PLUS = "weekly_5_plus"


class CoachProfileRequest(BaseModel):
    """Complete profile payload; drafts are deliberately not persisted."""

    model_config = ConfigDict(extra="forbid")

    age_band: CoachAgeBand
    height_cm: Decimal = Field(ge=Decimal("50"), le=Decimal("250"))
    current_weight_kg: Decimal = Field(ge=Decimal("10"), le=Decimal("500"))
    weight_measured_on: date
    goal_type: CoachGoalType
    target_weight_kg: Decimal = Field(ge=Decimal("10"), le=Decimal("500"))
    target_date: date
    current_body_fat_percent: Decimal | None = Field(
        default=None,
        ge=Decimal("1"),
        le=Decimal("75"),
    )
    target_body_fat_percent: Decimal | None = Field(
        default=None,
        ge=Decimal("1"),
        le=Decimal("75"),
    )
    exercise_frequency: CoachExerciseFrequency | None = None

    @field_validator(
        "height_cm",
        "current_weight_kg",
        "target_weight_kg",
        "current_body_fat_percent",
        "target_body_fat_percent",
    )
    @classmethod
    def require_at_most_one_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if not value.is_finite() or value != value.quantize(Decimal("0.1")):
            raise ValueError("Numeric profile values support at most one decimal place")
        return value

    @model_validator(mode="after")
    def validate_goal(self) -> CoachProfileRequest:
        if self.target_date <= self.weight_measured_on:
            raise ValueError("Target date must be after the weight measurement date")
        if (
            self.goal_type is CoachGoalType.LOSE_WEIGHT
            and self.target_weight_kg >= self.current_weight_kg
        ):
            raise ValueError("A weight-loss target must be below the current weight")
        return self


class CoachProfileData(BaseModel):
    age_band: CoachAgeBand
    height_cm: float
    current_weight_kg: float
    weight_measured_on: date
    goal_type: CoachGoalType
    target_weight_kg: float
    target_date: date
    current_body_fat_percent: float | None
    target_body_fat_percent: float | None
    exercise_frequency: CoachExerciseFrequency | None
    revision: int
    completed_at: datetime
    updated_at: datetime


class CoachProfileStatusView(BaseModel):
    schema_version: Literal[1] = 1
    status: Literal["required", "ready"]
    coach_enabled: bool
    profile: CoachProfileData | None = None


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
    messages: list[ChatMessageView] = Field(default_factory=list)


class ChatMessageView(BaseModel):
    id: str
    turn_id: str
    participant: Literal["user", "coach", "system_assistant"]
    # Kept for old mobile builds; new clients render participant instead.
    role: Literal["user", "assistant"]
    kind: Literal["text", "image", "card"]
    text: str | None
    card: dict[str, object] | None = None
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
