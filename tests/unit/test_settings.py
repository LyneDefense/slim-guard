from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from slim_guard.agent.manifest import build_agent_manifest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.config import DatabaseSettings, Settings
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


def test_agent_runtime_defaults_to_harness() -> None:
    settings = Settings()

    assert settings.agent_runtime_mode == "harness"
    assert settings.agent_specialist_timeout_seconds == 20
    assert settings.memory_health_review_days == 180
    assert settings.memory_recent_turn_count == 3
    assert settings.memory_recent_dialogue_max_chars == 1500
    assert settings.task_clarification_ttl_days == 14
    assert settings.memory_extraction_enabled is True
    assert settings.memory_extraction_interval_seconds == 2
    assert settings.memory_extraction_batch_size == 10
    assert settings.memory_recall_enabled is True
    assert settings.memory_recall_search_limit == 12
    assert settings.memory_recall_max_selected == 8
    assert settings.memory_semantic_enabled is False
    assert settings.agent_transcript_body_retention_days == 30
    assert settings.memory_revoked_value_retention_days == 30
    assert settings.memory_maintenance_interval_seconds == 21_600
    assert settings.mobile_api_enabled is False
    assert settings.mobile_is_configured is False


def test_style_training_execution_config_accepts_environment_strings(monkeypatch):
    monkeypatch.setenv("STYLE_TRAINING_MAX_ROUNDS", "3")
    monkeypatch.setenv("STYLE_TRAINING_MAX_SECONDS", "1200")
    settings = Settings(_env_file=None)
    assert settings.style_training_max_rounds == 3
    assert settings.style_training_max_seconds == 1200
    monkeypatch.setenv("STYLE_TRAINING_MAX_ROUNDS", "100")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_embedding_dimensions_accepts_env_style_string() -> None:
    settings = Settings(
        _env_file=None,
        NUTRITION_EMBEDDING_DIMENSIONS="1024",
    )

    assert settings.nutrition_embedding_dimensions == 1024


def test_mobile_api_requires_a_real_secret_and_production_sms_provider() -> None:
    with pytest.raises(ValidationError, match="MOBILE_AUTH_SECRET"):
        Settings(mobile_api_enabled=True, mobile_auth_secret="short")

    with pytest.raises(ValidationError, match="MOBILE_DEV_OTP_ENABLED"):
        Settings(
            app_env="production",
            mobile_api_enabled=True,
            mobile_auth_secret="x" * 32,
        )

    with pytest.raises(ValidationError, match="MOBILE_SMS_WEBHOOK_URL"):
        Settings(
            app_env="production",
            mobile_api_enabled=True,
            mobile_auth_secret="x" * 32,
            mobile_dev_otp_enabled=False,
        )

    settings = Settings(
        app_env="production",
        mobile_api_enabled=True,
        mobile_auth_secret="x" * 32,
        mobile_dev_otp_enabled=False,
        mobile_sms_webhook_url="https://sms.internal/send",
    )
    assert settings.mobile_is_configured is True


def test_mobile_test_accounts_are_development_only() -> None:
    with pytest.raises(ValidationError, match="requires MOBILE_API_ENABLED"):
        Settings(mobile_test_accounts_enabled=True)

    with pytest.raises(ValidationError, match="must be false in production"):
        Settings(
            app_env="production",
            mobile_api_enabled=True,
            mobile_auth_secret="x" * 32,
            mobile_dev_otp_enabled=False,
            mobile_sms_webhook_url="https://sms.internal/send",
            mobile_test_accounts_enabled=True,
        )

    settings = Settings(
        mobile_api_enabled=True,
        mobile_auth_secret="x" * 32,
        mobile_test_accounts_enabled=True,
    )
    assert settings.mobile_test_account_password == "123456"


def test_routine_scheduler_reserves_proactive_message_capacity() -> None:
    settings = Settings()

    assert settings.routine_scheduler_enabled is True
    assert settings.wecom_proactive_active_window_hours == 48
    assert settings.wecom_proactive_max_messages == 3

    with pytest.raises(ValidationError):
        Settings(wecom_proactive_max_messages=6)


def test_admin_credentials_must_be_complete_but_may_use_a_test_password() -> None:
    settings = Settings(admin_username="admin", admin_password="short", _env_file=None)
    assert settings.admin_is_configured is True

    with pytest.raises(ValidationError, match="configured together"):
        Settings(admin_username="admin", _env_file=None)


def test_database_settings_ignore_unrelated_invalid_app_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/migration.sqlite3")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "short")

    settings = DatabaseSettings(_env_file=None)

    assert settings.database_url.endswith("migration.sqlite3")


def test_database_settings_default_to_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = DatabaseSettings(_env_file=None)

    assert settings.database_url.startswith("postgresql+psycopg://")


def test_agent_runtime_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(agent_runtime_mode="unknown")


def test_legacy_shadow_timeout_env_alias_configures_specialist_timeout() -> None:
    settings = Settings(
        _env_file=None,
        MULTI_AGENT_SHADOW_TIMEOUT_SECONDS="45",
    )

    assert settings.agent_specialist_timeout_seconds == 45


def test_obsolete_runtime_modes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(agent_runtime_mode="shadow")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(agent_runtime_mode="legacy")  # type: ignore[arg-type]


def test_nutrition_worker_requires_cos_and_embedding_credentials() -> None:
    with pytest.raises(ValidationError, match="Tencent COS"):
        Settings(nutrition_knowledge_worker_enabled=True, _env_file=None)

    with pytest.raises(ValidationError, match="ZHIPU_API_KEY"):
        Settings(
            nutrition_knowledge_worker_enabled=True,
            tencent_cos_region="ap-shanghai",
            tencent_cos_bucket="nutrition-1234567890",
            tencent_cos_secret_id="secret-id",
            tencent_cos_secret_key="secret-key",
            _env_file=None,
        )

    settings = Settings(
        nutrition_knowledge_worker_enabled=True,
        tencent_cos_region="ap-shanghai",
        tencent_cos_bucket="nutrition-1234567890",
        tencent_cos_secret_id="secret-id",
        tencent_cos_secret_key="secret-key",
        zhipu_api_key="zhipu-key",
        _env_file=None,
    )
    assert settings.tencent_cos_is_configured is True
    assert settings.nutrition_rag_engine == "v2"


def test_nutrition_capability_can_start_without_embedding_provider() -> None:
    app = create_app(Settings(zhipu_api_key="", _env_file=None))
    assert "consult_nutrition_specialist" in dict(app.state.agent_manifest.tool_versions)


def test_nutrition_rag_v2_starts_with_required_credentials() -> None:
    app = create_app(Settings(zhipu_api_key="test-key", _env_file=None))
    assert "consult_nutrition_specialist" in dict(app.state.agent_manifest.tool_versions)


def test_harness_runtime_mode_exposes_tool_enabled_manifest() -> None:
    settings = Settings(
        agent_runtime_mode="harness",
        agent_code_revision="test-harness-commit",
    )

    app = create_app(settings)

    assert app.state.agent_runtime_mode == "harness"
    assert dict(app.state.agent_manifest.tool_versions) == {
        "get_recent_weight_trend": "v1",
        "record_body_fat": "v1",
        "get_recent_body_fat_trend": "v1",
        "inspect_image": "v4",
        "get_recent_meals": "v3",
        "record_meal": "v3",
        "get_recent_exercise": "v1",
        "get_checkin_schedule": "v1",
        "configure_checkin_schedule": "v1",
        "record_exercise": "v1",
        "record_weight": "v1",
        "update_record_status": "v1",
        "set_coaching_profile": "v8",
        "set_body_profile": "v8",
        "set_exercise_profile": "v8",
        "upsert_food_preference": "v8",
        "upsert_exercise_preference": "v8",
        "set_weight_goal": "v8",
        "set_body_fat_goal": "v8",
        "set_behavior_goal": "v8",
        "record_user_constraint": "v8",
        "list_user_memories": "v8",
        "forget_user_memory": "v8",
        "save_conversation_task": "v1",
        "get_conversation_task": "v1",
        "list_conversation_tasks": "v1",
        "link_task_authorization": "v1",
        "clear_user_memories": "v8",
        "remember_long_term_memory": "v1",
        "list_long_term_memories": "v1",
        "forget_long_term_memory": "v1",
        "clear_long_term_memories": "v1",
        "resolve_pending_user_action": "v1",
        "consult_nutrition_specialist": "v1",
    }
    assert app.state.agent_manifest.code_revision == "test-harness-commit"


def test_create_app_exposes_current_agent_manifest() -> None:
    settings = Settings(agent_runtime_mode="harness", agent_code_revision="test-commit")

    app = create_app(settings)

    assert app.state.agent_runtime_mode == "harness"
    assert app.state.agent_manifest.text_model == "glm-5.2"
    assert app.state.agent_manifest.code_revision == "test-commit"
    assert app.state.agent_manifest.version_id.startswith("agent-")


def test_create_app_passes_configured_models_and_budget_to_runtime_definition() -> None:
    settings = Settings(
        zhipu_text_model="custom-text-model",
        zhipu_vision_model="custom-vision-model",
        zhipu_max_output_tokens=2048,
        agent_reply_max_chars=800,
    )

    with patch("slim_guard.main.build_agent_manifest", wraps=build_agent_manifest) as build:
        app = create_app(settings)

    build.assert_called_once()
    definition = build.call_args.args[0]
    expected = ModelRuntimeConfig(
        provider="zhipu",
        text_model="custom-text-model",
        vision_model="custom-vision-model",
        parameters={
            "thinking": {"type": "disabled"},
            "do_sample": False,
            "max_output_tokens": 2048,
            "max_reply_chars": 800,
        },
        vision_max_output_tokens=2048,
    )
    assert definition.models == expected
    assert app.state.agent_manifest.text_model == expected.text_model
    assert app.state.agent_manifest.vision_model == expected.vision_model
    assert app.state.agent_manifest.model_parameters_dict() == expected.parameters
