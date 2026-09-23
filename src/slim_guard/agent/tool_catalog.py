"""Assemble the Core tool catalog without repositories or runtime configuration."""

from slim_guard.agents.nutrition import nutrition_agent_tool_definitions
from slim_guard.tasks.tools import task_tool_definitions
from slim_guard.tools.body_fat import body_fat_tool_definitions
from slim_guard.tools.exercise import exercise_tool_definitions
from slim_guard.tools.image import image_tool_definitions
from slim_guard.tools.long_term_memory import long_term_memory_tool_definitions
from slim_guard.tools.meal import meal_tool_definitions
from slim_guard.tools.memory import memory_tool_definitions
from slim_guard.tools.pending import pending_action_tool_definitions
from slim_guard.tools.records import record_status_tool_definitions
from slim_guard.tools.registry import ToolRegistry
from slim_guard.tools.routine import routine_tool_definitions
from slim_guard.tools.weight import weight_tool_definitions


def build_core_tool_registry() -> ToolRegistry:
    """Expose business and nutrition tools; Core decides which ones to call."""

    return ToolRegistry(
        (
            *weight_tool_definitions(),
            *body_fat_tool_definitions(),
            *image_tool_definitions(),
            *meal_tool_definitions(),
            *exercise_tool_definitions(),
            *routine_tool_definitions(),
            *record_status_tool_definitions(),
            *memory_tool_definitions(),
            *task_tool_definitions(),
            *long_term_memory_tool_definitions(),
            *pending_action_tool_definitions(),
            *nutrition_agent_tool_definitions(),
        )
    )
