"""Compile the least context needed by the response-style model."""

from __future__ import annotations

from collections.abc import Sequence

from slim_guard.agents.contracts import ProfessionalAssessment, ResponsePlan
from slim_guard.expression_style.contracts import (
    SLIMGUARD_DEFAULT_V1,
    StyleContext,
    StyleExample,
    StyleProfile,
)


class StyleContextCompiler:
    """Builds a role-scoped snapshot without user databases or raw conversations."""

    def compile(
        self,
        *,
        turn_id: str,
        response_plan: ResponsePlan,
        profile: StyleProfile = SLIMGUARD_DEFAULT_V1,
        assessment: ProfessionalAssessment | None = None,
        examples: Sequence[StyleExample] = (),
        user_input: str = "",
        minimal_context: tuple[str, ...] = (),
        compiled_prompt: str = "",
        package_hash: str = "",
    ) -> StyleContext:
        return StyleContext(
            turn_id=turn_id,
            response_plan=response_plan,
            profile=profile,
            assessment=assessment,
            examples=tuple(examples),
            user_input=user_input,
            minimal_context=minimal_context,
            compiled_prompt=compiled_prompt,
            package_hash=package_hash,
        )


__all__ = ["StyleContextCompiler"]
