"""Deterministic fallback renderer that cannot invent response content."""

from __future__ import annotations

from slim_guard.agents.contracts import ContentBlockKind, StyledResponse
from slim_guard.agents.style.contracts import StyleContext


class NeutralRenderer:
    """Render every plan block in original order with no model dependency."""

    def render(self, context: StyleContext) -> StyledResponse:
        plan = context.response_plan
        text = "\n".join(block.text.strip() for block in plan.content_blocks)
        claim_ids = self._source_refs(context, ContentBlockKind.CLAIM)
        action_ids = self._source_refs(context, ContentBlockKind.ACTION)
        risk_flags = (
            tuple(context.assessment.risk_flags)
            if context.assessment is not None
            else self._source_refs(context, ContentBlockKind.RISK)
        )
        return StyledResponse(
            text=text,
            used_block_ids=tuple(block.block_id for block in plan.content_blocks),
            used_claim_ids=claim_ids,
            used_action_ids=action_ids,
            preserved_risk_flags=risk_flags,
            preserved_citation_refs=plan.citation_refs,
            style_profile_version=context.profile.version,
        )

    @staticmethod
    def _source_refs(
        context: StyleContext,
        kind: ContentBlockKind,
    ) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                reference
                for block in context.response_plan.content_blocks
                if block.kind is kind
                for reference in block.source_refs
            )
        )


__all__ = ["NeutralRenderer"]
