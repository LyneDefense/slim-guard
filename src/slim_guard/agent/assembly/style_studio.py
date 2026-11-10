"""Composition for the admin studio; never construct a real-user turn runtime."""

from slim_guard.agent.assembly.coach_sandbox import CoachSandbox
from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.style_management.studio.model import StyleStudioModel
from slim_guard.style_management.studio.ports import StyleStudioModelPort


def build_style_studio(
    gateway: ModelGateway, model: str, request_seconds: float
) -> StyleStudioModelPort:
    return StyleStudioModel(
        gateway=gateway,
        preview=CoachSandbox(gateway, model, int(request_seconds)),
        model=model,
        request_seconds=request_seconds,
    )
