"""Provider-independent model contracts used by the agent harness."""

from slim_guard.agent_models.fake import ModelScriptStep, ScriptedModelGateway
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
from slim_guard.agent_models.vision import (
    VisionInspectionRequest,
    VisionInspectionResponse,
    VisionModelGateway,
)
from slim_guard.agent_models.zhipu import ZhipuModelGateway
from slim_guard.agent_models.zhipu_vision import ZhipuVisionModelGateway

__all__ = [
    "MessageRole",
    "ModelScriptStep",
    "ModelGateway",
    "ModelMessage",
    "ModelPurpose",
    "ModelRequest",
    "ModelResponse",
    "ModelUsage",
    "NormalizedToolCall",
    "ScriptedModelGateway",
    "ToolChoice",
    "ToolDefinition",
    "VisionInspectionRequest",
    "VisionInspectionResponse",
    "VisionModelGateway",
    "ZhipuModelGateway",
    "ZhipuVisionModelGateway",
]
