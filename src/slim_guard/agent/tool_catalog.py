"""Assemble the Core tool catalog without repositories or runtime configuration."""

from slim_guard.agents.nutrition import nutrition_agent_tool_definitions
from slim_guard.features.health_records.tools import health_record_tool_definitions
from slim_guard.tasks.tools import task_tool_definitions
from slim_guard.tool_operations.tools import operation_tool_definitions
from slim_guard.tools.image import image_tool_definitions
from slim_guard.tools.long_term_memory import long_term_memory_tool_definitions
from slim_guard.tools.memory import memory_tool_definitions
from slim_guard.tools.registry import ToolRegistry
from slim_guard.tools.routine import routine_tool_definitions


def build_core_tool_registry() -> ToolRegistry:
    """Expose business and nutrition tools; Core decides which ones to call."""

    return ToolRegistry(
        (
            *health_record_tool_definitions(),
            *image_tool_definitions(),
            *routine_tool_definitions(),
            *memory_tool_definitions(),
            *task_tool_definitions(),
            *long_term_memory_tool_definitions(),
            *operation_tool_definitions(),
            *nutrition_agent_tool_definitions(),
        )
    )
