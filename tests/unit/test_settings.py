from __future__ import annotations

import pytest
from pydantic import ValidationError

from slim_guard.config import Settings
from slim_guard.main import create_app


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


def test_zhipu_models_are_configured_separately_by_modality() -> None:
    settings = Settings(zhipu_api_key="test-zhipu-key")

    assert settings.zhipu_is_configured is True
    assert settings.zhipu_text_model == "glm-5.2"
    assert settings.zhipu_vision_model == "glm-5v-turbo"


def test_agent_runtime_defaults_to_legacy() -> None:
    settings = Settings()

    assert settings.agent_runtime_mode == "legacy"


def test_routine_scheduler_reserves_proactive_message_capacity() -> None:
    settings = Settings()

    assert settings.routine_scheduler_enabled is True
    assert settings.wecom_proactive_active_window_hours == 48
    assert settings.wecom_proactive_max_messages == 3

    with pytest.raises(ValidationError):
        Settings(wecom_proactive_max_messages=6)


def test_agent_runtime_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(agent_runtime_mode="unknown")  # type: ignore[arg-type]


def test_unimplemented_shadow_runtime_mode_fails_fast() -> None:
    settings = Settings(agent_runtime_mode="shadow")

    with pytest.raises(ValueError, match="not implemented yet"):
        create_app(settings)


def test_harness_runtime_mode_exposes_tool_enabled_manifest() -> None:
    settings = Settings(
        agent_runtime_mode="harness",
        agent_code_revision="test-harness-commit",
    )

    app = create_app(settings)

    assert app.state.agent_runtime_mode == "harness"
    assert dict(app.state.agent_manifest.tool_versions) == {
        "get_recent_weight_trend": "v1",
        "inspect_image": "v1",
        "get_recent_meals": "v1",
        "record_meal": "v1",
        "get_recent_exercise": "v1",
        "get_checkin_schedule": "v1",
        "configure_checkin_schedule": "v1",
        "record_exercise": "v1",
        "record_weight": "v1",
        "update_record_status": "v1",
    }
    assert app.state.agent_manifest.code_revision == "test-harness-commit"


def test_create_app_exposes_current_agent_manifest() -> None:
    settings = Settings(agent_code_revision="test-commit")

    app = create_app(settings)

    assert app.state.agent_runtime_mode == "legacy"
    assert app.state.agent_manifest.text_model == "glm-5.2"
    assert app.state.agent_manifest.code_revision == "test-commit"
    assert app.state.agent_manifest.version_id.startswith("agent-")
