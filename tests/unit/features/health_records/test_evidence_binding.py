from slim_guard.features.health_records.commands import CreateRecord
from slim_guard.features.health_records.content import MealContent, MealItem
from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.features.health_records.provenance import FieldProvenance
from slim_guard.features.health_records.tools import RecordTools
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode


def test_current_user_text_selector_binds_to_persisted_message_id():
    command = CreateRecord(
        content=MealContent(
            meal_type="snack",
            foods=(MealItem(item_id="food-1", name="酸奶"),),
        ),
        occurrence=Occurrence(),
        field_provenance=(
            FieldProvenance(
                field_ref="meal_type",
                value_revision=1,
                source_kind="user_reported",
                source_refs=("current-user-message",),
            ),
        ),
    )
    context = ToolContext(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="call-1",
        user_id="user-1",
        agent_version_id="agent-1",
        execution_mode=ToolExecutionMode.LIVE,
        source_item_id="persisted-user-message-1",
    )

    bound = RecordTools._bind_sources(command, context)

    assert bound.field_provenance[0].source_refs == ("persisted-user-message-1",)
