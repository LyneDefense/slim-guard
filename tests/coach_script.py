"""Separate expression fixtures from tests focused on Core business execution.

These older business scripts contain a final golden user response. Convert only
that fixture into CoreCompletion, then deliver its golden text from the coach
fixture. Production never passes Core prose to the coach. Full pipeline tests
script each real schema directly and do not use this adapter.
"""

import json

from slim_guard.agent_models.gateway import ModelPurpose, ModelResponse


class BusinessCoreScript:
    def __init__(self, core):
        self.core = core
        self.coach_requests = []
        self.review_requests = []
        self._golden = "收到。"

    async def complete(self, request):
        if request.purpose is ModelPurpose.COACH_RESPONSE:
            self.coach_requests.append(request)
            return ModelResponse.model_validate(
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"text": self._golden, "source_refs": []}),
                    }
                }
            )
        if request.purpose is ModelPurpose.COACH_REVIEW:
            self.review_requests.append(request)
            return ModelResponse.model_validate(
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "fidelity_passed": True,
                                "role_passed": True,
                                "expression_passed": True,
                                "issues": [],
                            }
                        ),
                    }
                }
            )
        response = await self.core.complete(request)
        if request.purpose is ModelPurpose.HARNESS_TURN and not response.message.tool_calls:
            self._golden = response.message.content or "收到。"
            failed = []
            for message in request.messages:
                if message.role.value == "tool":
                    observation = json.loads(message.content)
                    if observation.get("status") == "failed" and message.tool_call_id:
                        failed.append(message.tool_call_id)
            return response.model_copy(
                update={
                    "message": response.message.model_copy(
                        update={"content": json.dumps({"failed_operation_refs": failed})}
                    )
                }
            )
        return response

    async def close(self):
        await self.core.close()
