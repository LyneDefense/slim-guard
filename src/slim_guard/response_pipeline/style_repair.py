"""One feedback-driven coach expression repair using shared execution stages."""

from slim_guard.response_pipeline.contracts import ResponseFinalizationRequest
from slim_guard.response_pipeline.planning import PlannedResponse
from slim_guard.response_pipeline.profiles import ResolvedStyleProfile
from slim_guard.response_pipeline.stages import ResponseStageExecutor, ReviewStage, StyleStage
from slim_guard.runtime.contracts import AgentArtifact


async def repair_style_once(
    *,
    stages: ResponseStageExecutor,
    request: ResponseFinalizationRequest,
    planned: PlannedResponse,
    selection: ResolvedStyleProfile,
    plan_artifact: AgentArtifact,
    neutral_artifact: AgentArtifact,
    resolution_artifact: AgentArtifact,
    first_style: StyleStage,
    first_review: ReviewStage,
    sequence: int = 0,
) -> tuple[StyleStage, ReviewStage]:
    """Repair expression only after Reviewer requests it; never rerun business tools.

    Keep the same semantic input, style snapshot and request deadline. Callers
    decide whether to publish or fall back; this operation performs no loop.
    """

    repaired = await stages.run_style(
        request=request,
        planned=planned,
        selection=selection,
        plan_artifact=plan_artifact,
        neutral_artifact=neutral_artifact,
        resolution_artifact=resolution_artifact,
        parent_invocation_id=first_review.invocation_id,
        attempt=2,
        sequence=sequence,
        review_feedback=first_review.verdict,
        prior_artifact_id=first_style.artifact.artifact_id,
        verdict_artifact_id=first_review.artifact.artifact_id,
    )
    review = await stages.run_review(
        request=request,
        planned=planned,
        selection=selection,
        styled=repaired.response,
        styled_artifact=repaired.artifact,
        plan_artifact=plan_artifact,
        parent_invocation_id=repaired.invocation_id,
        attempt=2,
        sequence=sequence,
        prior_verdict_artifact_id=first_review.artifact.artifact_id,
    )
    return repaired, review
