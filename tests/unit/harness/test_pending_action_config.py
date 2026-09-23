import pytest
from pydantic import ValidationError

from slim_guard.harness.pending_config import PendingActionConfig


def test_pending_action_defaults_and_round_trip() -> None:
    config = PendingActionConfig()
    assert config.model_dump() == {"confirmation_ttl_seconds": 900, "review_ttl_seconds": 86_400}
    assert PendingActionConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize(
    ("field", "maximum"),
    [("confirmation_ttl_seconds", 86_400), ("review_ttl_seconds", 604_800)],
)
def test_pending_action_lifetime_bounds(field: str, maximum: int) -> None:
    for value in (1, maximum):
        assert getattr(PendingActionConfig.model_validate({field: value}), field) == value
    for value in (0, maximum + 1):
        with pytest.raises(ValidationError) as error:
            PendingActionConfig.model_validate({field: value})
        assert error.value.errors()[0]["loc"] == (field,)


def test_pending_action_config_is_immutable() -> None:
    with pytest.raises(ValidationError) as error:
        PendingActionConfig().confirmation_ttl_seconds = 100
    assert error.value.errors()[0]["type"] == "frozen_instance"


def test_pending_action_config_rejects_unknown_settings() -> None:
    with pytest.raises(ValidationError) as error:
        PendingActionConfig.model_validate({"unknown_setting": 1})
    assert error.value.errors()[0]["type"] == "extra_forbidden"
