from datetime import UTC, datetime

from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.db.models import MobileAgentRequestRecord


def test_mobile_agent_request_is_exposed_as_final_trace_output() -> None:
    created_at = datetime(2026, 9, 17, 13, 7, tzinfo=UTC)
    completed_at = datetime(2026, 9, 17, 13, 7, 5, tzinfo=UTC)
    request = MobileAgentRequestRecord(
        id="request-1",
        user_id="user-1",
        idempotency_key="idem-1",
        request_hash="a" * 64,
        status="succeeded",
        final_text="你可以叫我 SlimGuard。",
        created_at=created_at,
        completed_at=completed_at,
    )

    assert AdminQueryRepository._mobile_output_view(request) == {
        "kind": "mobile",
        "content": "你可以叫我 SlimGuard。",
        "status": "accepted",
        "platform_msgid": None,
        "last_error": None,
        "attempt_started_at": created_at,
        "completed_at": completed_at,
    }
