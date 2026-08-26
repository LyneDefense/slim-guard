from __future__ import annotations

from functools import cached_property

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
    fixed_reply_text: str = "收到，我已经连接成功。"
    log_level: str = "INFO"
    callback_body_limit_bytes: int = Field(default=1_048_576, ge=1024)
    wecom_http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

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
