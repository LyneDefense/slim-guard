from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from slim_guard.agent_models.gateway import ToolDefinition
from slim_guard.tools.contracts import ToolArguments, ToolEffectLevel
from slim_guard.tools.errors import DuplicateToolError, UnknownToolError


class RegisteredTool(BaseModel):
    """Immutable metadata for one capability available to the Harness."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(min_length=1, max_length=4096)
    version: str = Field(min_length=1, max_length=128)
    arguments_model: type[ToolArguments]
    effect_level: ToolEffectLevel
    idempotent: bool
    requires_confirmation: bool
    timeout_seconds: float = Field(gt=0, le=300)

    def model_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.arguments_model.model_json_schema(),
            version=self.version,
        )


class ToolRegistry:
    """A fixed, ordered catalog of tools composed at application startup."""

    def __init__(self, tools: Iterable[RegisteredTool]) -> None:
        ordered_tools = tuple(tools)
        by_name: dict[str, RegisteredTool] = {}
        for tool in ordered_tools:
            if tool.name in by_name:
                raise DuplicateToolError(f"Tool is already registered: {tool.name}")
            by_name[tool.name] = tool
        self._ordered_tools = ordered_tools
        self._by_name = by_name

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self._ordered_tools)

    @property
    def versions(self) -> dict[str, str]:
        return {tool.name: tool.version for tool in self._ordered_tools}

    def resolve(self, name: str) -> RegisteredTool:
        try:
            return self._by_name[name]
        except KeyError:
            raise UnknownToolError(f"Tool is not registered: {name}") from None

    def model_definitions(self, names: Sequence[str] | None = None) -> tuple[ToolDefinition, ...]:
        tools = (
            self._ordered_tools
            if names is None
            else tuple(self.resolve(name) for name in names)
        )
        return tuple(tool.model_definition() for tool in tools)
