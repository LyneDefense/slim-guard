"""System-only Conversation model fixture for tests focused on Core business tools.

This runs the real response pipeline, but keeps its model script separate from
the Core script under test. Coach/style/repair behavior has dedicated tests.
"""

import json

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
)


class SystemConversationScript:
    def __init__(self, core: ModelGateway) -> None:
        self.core = core
        self.conversation_requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if request.purpose is not ModelPurpose.CONVERSATION:
            return await self.core.complete(request)
        self.conversation_requests.append(request)
        data = json.loads(request.messages[-1].content or "{}")
        return ModelResponse(
            message=ModelMessage(
                role=MessageRole.ASSISTANT,
                content=json.dumps(
                    {
                        "schema_version": "2",
                        "messages": [
                            {
                                "participant": "system_assistant",
                                "requires_response": False,
                                "text": data["system_candidate"],
                                "source_refs": [],
                            }
                        ],
                    }
                ),
            )
        )

    async def close(self) -> None:
        await self.core.close()
