from datetime import date

import pytest
from pydantic import ValidationError

from slim_guard.features.health_records.occurrence import Occurrence


@pytest.mark.parametrize(
    "value",
    [
        {"precision": "datetime", "at": "2026-09-24T12:00:00", "basis": "user_reported"},
        {"precision": "datetime", "on": "2026-09-24", "basis": "user_reported"},
        {"precision": "date", "at": "2026-09-24T12:00:00Z", "basis": "inferred"},
        {"precision": "unknown", "on": "2026-09-24"},
        {"precision": "date", "on": "2026-09-24", "basis": "unknown"},
        {"timezone": "not/a/timezone"},
    ],
)
def test_invalid_precision_is_not_silently_guessed(value):
    with pytest.raises(ValidationError):
        Occurrence.model_validate(value)


def test_unknown_does_not_default_to_now():
    occurrence = Occurrence()
    assert occurrence.at is None
    assert occurrence.local_date("Asia/Shanghai") is None


def test_date_only_does_not_fabricate_timestamp():
    occurrence = Occurrence(
        precision="date", on=date(2026, 9, 23), timezone="Asia/Shanghai", basis="user_reported"
    )
    assert occurrence.at is None
    assert occurrence.local_date("Asia/Shanghai") == date(2026, 9, 23)


def test_timestamp_projection_uses_user_timezone():
    occurrence = Occurrence.model_validate(
        {
            "precision": "datetime",
            "at": "2026-09-23T18:30:00Z",
            "basis": "user_reported",
        }
    )
    assert occurrence.local_date("Asia/Shanghai") == date(2026, 9, 24)
    assert occurrence.local_date("UTC") == date(2026, 9, 23)
