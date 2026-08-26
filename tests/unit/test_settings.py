from __future__ import annotations

from slim_guard.config import Settings


def test_callback_can_be_configured_before_secret_and_open_kfid(
    encoding_aes_key: str,
) -> None:
    settings = Settings(
        wecom_corp_id="ww-test",
        wecom_callback_token="token",
        wecom_callback_aes_key=encoding_aes_key,
        wecom_kf_secret="",
        wecom_open_kf_id="",
    )

    assert settings.wecom_callback_is_configured is True
    assert settings.wecom_api_is_configured is False
    assert settings.wecom_is_configured is False


def test_project_specific_openai_key_is_supported() -> None:
    settings = Settings(openai_key="test-openai-key")

    assert settings.openai_is_configured is True
    assert settings.openai_model == "gpt-4.1-mini"
