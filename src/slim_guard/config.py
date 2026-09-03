from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "sqlite+aiosqlite:///./data/slim_guard.sqlite3"


class Settings(DatabaseSettings):
    app_env: str = "development"
    http_host: str = "0.0.0.0"
    http_port: int = Field(default=8000, ge=1, le=65535)
    wecom_api_base_url: str = "https://qyapi.weixin.qq.com"
    wecom_corp_id: str = ""
    wecom_kf_secret: str = ""
    wecom_open_kf_id: str = ""
    wecom_callback_token: str = ""
    wecom_callback_aes_key: str = ""
    agent_runtime_mode: Literal["legacy", "harness", "shadow"] = "harness"
    agent_code_revision: str = "development"
    agent_fallback_reply_text: str = "抱歉，我刚才没有成功分析这条记录，请稍后再发一次。"
    reply_delivery_mode: Literal["automatic", "internal_review"] = "automatic"
    wecom_human_idle_timeout_seconds: int = Field(default=600, ge=60, le=86_400)
    wecom_session_watchdog_interval_seconds: int = Field(default=30, ge=5, le=3600)
    wecom_outbox_recovery_interval_seconds: int = Field(default=30, ge=5, le=3600)
    wecom_outbox_send_stale_seconds: int = Field(default=120, ge=30, le=3600)
    wecom_customer_profile_refresh_seconds: int = Field(default=86_400, ge=300, le=2_592_000)
    wecom_human_timeout_message: str = (
        "人工服务暂时没有响应，已结束人工接待。请再发送一次刚才的内容，"
        "SlimGuard 减脂助手会继续为你服务。"
    )
    log_level: str = "INFO"
    admin_username: str = ""
    admin_password: str = ""
    admin_session_ttl_hours: int = Field(default=12, ge=1, le=168)
    mobile_api_enabled: bool = False
    mobile_auth_secret: str = ""
    mobile_access_token_ttl_minutes: int = Field(default=15, ge=5, le=1440)
    mobile_refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)
    mobile_otp_ttl_seconds: int = Field(default=300, ge=60, le=900)
    mobile_otp_resend_seconds: int = Field(default=60, ge=30, le=600)
    mobile_otp_hourly_limit: int = Field(default=5, ge=1, le=20)
    mobile_dev_otp_enabled: bool = True
    mobile_sms_webhook_url: str = ""
    mobile_sms_webhook_token: str = ""
    mobile_wecom_binding_ttl_minutes: int = Field(default=10, ge=2, le=60)
    callback_body_limit_bytes: int = Field(default=1_048_576, ge=1024)
    wecom_http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    wecom_media_max_bytes: int = Field(default=10_485_760, ge=1024, le=20_971_520)
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_text_model: str = "glm-5.2"
    zhipu_vision_model: str = "glm-5v-turbo"
    zhipu_http_timeout_seconds: float = Field(default=45.0, gt=0, le=120)
    zhipu_max_output_tokens: int = Field(default=1024, ge=64, le=32_768)
    agent_reply_max_chars: int = Field(default=1500, ge=100, le=4000)
    agent_image_retention_seconds: int = Field(
        default=604_800,
        ge=3600,
        le=2_592_000,
    )
    asset_maintenance_interval_seconds: int = Field(default=21_600, ge=60, le=86_400)
    memory_preload_max_facts: int = Field(default=30, ge=1, le=100)
    memory_health_review_days: int = Field(default=180, ge=30, le=730)
    memory_recent_turn_count: int = Field(default=3, ge=1, le=10)
    memory_recent_dialogue_max_chars: int = Field(default=1500, ge=100, le=10_000)
    memory_recent_image_count: int = Field(default=3, ge=1, le=10)
    memory_handoff_ttl_days: int = Field(default=14, ge=1, le=90)
    memory_ingestion_enabled: bool = True
    memory_ingestion_history_count: int = Field(default=20, ge=1, le=100)
    memory_ingestion_history_max_chars: int = Field(default=6000, ge=100, le=20_000)
    memory_recall_enabled: bool = True
    memory_recall_search_limit: int = Field(default=12, ge=1, le=100)
    memory_recall_max_selected: int = Field(default=8, ge=1, le=20)
    memory_semantic_enabled: bool = False
    mem0_base_url: str = "http://mem0:8000"
    mem0_api_key: str = ""
    mem0_namespace: str = "slim_guard"
    mem0_http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    memory_index_sync_interval_seconds: int = Field(default=5, ge=1, le=3600)
    memory_index_sync_batch_size: int = Field(default=20, ge=1, le=100)
    memory_index_sync_max_attempts: int = Field(default=10, ge=1, le=100)
    agent_transcript_body_retention_days: int = Field(default=30, ge=1, le=3650)
    memory_revoked_value_retention_days: int = Field(default=30, ge=0, le=3650)
    memory_maintenance_interval_seconds: int = Field(default=21_600, ge=60, le=86_400)
    routine_scheduler_enabled: bool = True
    routine_scheduler_interval_seconds: int = Field(default=30, ge=5, le=3600)
    routine_job_lease_seconds: int = Field(default=120, ge=30, le=3600)
    routine_send_retry_seconds: int = Field(default=120, ge=30, le=3600)
    routine_max_lateness_seconds: int = Field(default=7200, ge=60, le=43_200)
    routine_agent_timeout_seconds: int = Field(default=45, ge=5, le=120)
    routine_max_attempts: int = Field(default=3, ge=1, le=10)
    wecom_proactive_active_window_hours: int = Field(default=48, ge=1, le=48)
    wecom_proactive_max_messages: int = Field(default=3, ge=1, le=5)

    @cached_property
    def wecom_callback_is_configured(self) -> bool:
        return all(
            (
                self.wecom_corp_id,
                self.wecom_callback_token,
                self.wecom_callback_aes_key,
            )
        )

    @cached_property
    def wecom_api_is_configured(self) -> bool:
        return all((self.wecom_corp_id, self.wecom_kf_secret, self.wecom_open_kf_id))

    @model_validator(mode="after")
    def validate_admin_credentials(self) -> Settings:
        if bool(self.admin_username) != bool(self.admin_password):
            raise ValueError("Admin username and password must be configured together")
        if self.mobile_api_enabled and len(self.mobile_auth_secret) < 32:
            raise ValueError(
                "MOBILE_AUTH_SECRET must contain at least 32 characters when the "
                "mobile API is enabled"
            )
        if (
            self.mobile_api_enabled
            and self.app_env == "production"
            and self.mobile_dev_otp_enabled
        ):
            raise ValueError("MOBILE_DEV_OTP_ENABLED must be false in production")
        if (
            self.mobile_api_enabled
            and self.app_env == "production"
            and not self.mobile_sms_webhook_url
        ):
            raise ValueError("MOBILE_SMS_WEBHOOK_URL is required in production")
        return self

    @cached_property
    def wecom_is_configured(self) -> bool:
        return self.wecom_callback_is_configured and self.wecom_api_is_configured

    @cached_property
    def zhipu_is_configured(self) -> bool:
        return bool(self.zhipu_api_key)

    @cached_property
    def admin_is_configured(self) -> bool:
        return bool(self.admin_username and self.admin_password)

    @cached_property
    def mobile_is_configured(self) -> bool:
        return self.mobile_api_enabled and len(self.mobile_auth_secret) >= 32
