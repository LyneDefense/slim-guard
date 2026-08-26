from __future__ import annotations

import base64
from pathlib import Path

import pytest

from slim_guard.config import Settings


@pytest.fixture
def encoding_aes_key() -> str:
    return base64.b64encode(bytes(range(32))).decode("ascii").rstrip("=")


@pytest.fixture
def test_settings(tmp_path: Path, encoding_aes_key: str) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}",
        wecom_corp_id="ww-test-corp",
        wecom_kf_secret="test-secret",
        wecom_open_kf_id="wk-test",
        wecom_callback_token="callback-token",
        wecom_callback_aes_key=encoding_aes_key,
        agent_fallback_reply_text="暂时无法分析。",
        log_level="WARNING",
    )
