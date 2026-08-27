"""Provider-independent model contracts used by the agent harness."""

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
    ToolChoice,
    ToolDefinition,
)
from slim_guard.agent_models.zhipu import ZhipuModelGateway

__all__ = [
    "MessageRole",
    "ModelGateway",
    "ModelMessage",
    "ModelPurpose",
    "ModelRequest",
    "ModelResponse",
    "ModelUsage",
    "NormalizedToolCall",
    "ToolChoice",
    "ToolDefinition",
    "ZhipuModelGateway",
]
