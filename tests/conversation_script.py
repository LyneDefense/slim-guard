"""System-only Conversation model fixture for tests focused on Core business tools.

This runs the real response pipeline, but keeps conversation and semantic-review
scripts separate from Core. By default these lifecycle/tool tests declare no
mutation assertions; tests about claim fidelity supply explicit golden declarations.
"""

import json
from collections.abc import Callable

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
)


class SystemConversationScript:
    def __init__(self, core: ModelGateway, *, mutation_claims: Callable | None = None) -> None:
        self.core = core
        self.conversation_requests: list[ModelRequest] = []
        self.review_requests: list[ModelRequest] = []
        self._mutation_claims = mutation_claims

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if request.output_schema_name == "MutationClaims":
            self.review_requests.append(request)
            payload = json.loads(request.messages[-1].content or "{}")
            claims = self._mutation_claims(payload) if self._mutation_claims else []
            return ModelResponse(
                message=ModelMessage(role="assistant", content=json.dumps({"claims": claims}))
            )
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
