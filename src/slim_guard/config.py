from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    http_host: str = "0.0.0.0"
    http_port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "sqlite+aiosqlite:///./data/slim_guard.sqlite3"
    wecom_api_base_url: str = "https://qyapi.weixin.qq.com"
    wecom_corp_id: str = ""
    wecom_kf_secret: str = ""
    wecom_open_kf_id: str = ""
    wecom_callback_token: str = ""
    wecom_callback_aes_key: str = ""
    agent_fallback_reply_text: str = "抱歉，我刚才没有成功分析这条记录，请稍后再发一次。"
    reply_delivery_mode: Literal["automatic", "internal_review"] = "automatic"
    wecom_human_idle_timeout_seconds: int = Field(default=600, ge=60, le=86_400)
    wecom_session_watchdog_interval_seconds: int = Field(default=30, ge=5, le=3600)
    wecom_customer_profile_refresh_seconds: int = Field(default=86_400, ge=300, le=2_592_000)
    wecom_human_timeout_message: str = (
        "人工服务暂时没有响应，已结束人工接待。请再发送一次刚才的内容，"
        "SlimGuard 减脂助手会继续为你服务。"
    )
    log_level: str = "INFO"
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

    @cached_property
    def wecom_is_configured(self) -> bool:
        return self.wecom_callback_is_configured and self.wecom_api_is_configured

    @cached_property
    def zhipu_is_configured(self) -> bool:
        return bool(self.zhipu_api_key)
