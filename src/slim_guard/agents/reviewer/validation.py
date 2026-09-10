"""Deterministic integrity checks around the semantic reviewer verdict."""

from __future__ import annotations

import re

from slim_guard.agents.contracts import (
    ContentBlockKind,
    RepairTarget,
    ReviewerIssueType,
    ReviewerVerdict,
    ReviewerVerdictStatus,
)
from slim_guard.agents.reviewer.contracts import (
    ReviewerContext,
    ReviewerValidationIssue,
    ReviewerValidationIssueCode,
    ReviewerValidationReport,
)


class ReviewerVerdictValidator:
    """Reject contradictory verdicts and independently detectable integrity failures."""

    def validate(
        self,
        context: ReviewerContext,
        verdict: ReviewerVerdict,
    ) -> ReviewerValidationReport:
        issues: list[ReviewerValidationIssue] = []
        detected = self._detected_issue_types(context)
        declared = {item.type for item in verdict.issues}
        if verdict.issue_type is not None:
            declared.add(verdict.issue_type)

        if verdict.verdict is ReviewerVerdictStatus.PASS:
            if verdict.reason_summary is not None:
                issues.append(
                    ReviewerValidationIssue(
                        ReviewerValidationIssueCode.PASS_REASON_PRESENT,
                        "reason_summary",
                    )
                )
            if detected:
                issues.append(
                    ReviewerValidationIssue(
                        ReviewerValidationIssueCode.PASS_CONTRADICTS_RESPONSE,
                        "styled_response",
                    )
                )
        else:
            if not declared:
                issues.append(
                    ReviewerValidationIssue(
                        ReviewerValidationIssueCode.NON_PASS_ISSUE_MISSING,
                        "issue_type",
                    )
                )
            if verdict.reason_summary is None and not verdict.issues:
                issues.append(
                    ReviewerValidationIssue(
                        ReviewerValidationIssueCode.NON_PASS_REASON_MISSING,
                        "reason_summary",
                    )
                )
            if detected.difference(declared):
                issues.append(
                    ReviewerValidationIssue(
                        ReviewerValidationIssueCode.DETECTED_ISSUE_NOT_DECLARED,
                        "issue_type",
                    )
                )

        if verdict.verdict is ReviewerVerdictStatus.REPAIR and not self._valid_target(
            verdict.repair_target,
            declared,
        ):
            issues.append(
                ReviewerValidationIssue(
                    ReviewerValidationIssueCode.INVALID_REPAIR_DIRECTION,
                    "repair_target",
                )
            )

        return ReviewerValidationReport(
            issues=tuple(issues),
            detected_issue_types=tuple(sorted(detected, key=lambda item: item.value)),
        )

    @staticmethod
    def _valid_target(
        target: RepairTarget | None,
        issue_types: set[ReviewerIssueType],
    ) -> bool:
        if target is RepairTarget.RESPONSE_STYLE:
            return issue_types.issubset(
                {
                    ReviewerIssueType.STYLE_DRIFT,
                    ReviewerIssueType.CHANGED_MEANING,
                    ReviewerIssueType.CHANGED_UNCERTAINTY,
                    ReviewerIssueType.ABUSIVE_TONE,
                    ReviewerIssueType.OMITTED_REQUIRED_CONTENT,
                    ReviewerIssueType.DISH_IDENTITY_STRENGTHENED,
                    ReviewerIssueType.UNSUPPORTED_DISH_GUIDANCE,
                    ReviewerIssueType.UNSUPPORTED_AVOIDANCE,
                    ReviewerIssueType.FORBIDDEN_NUTRITION_ESTIMATE,
                }
            )
        if target is RepairTarget.NUTRITION_EXPERT:
            return issue_types.issubset(
                {
                    ReviewerIssueType.UNSUPPORTED_CLAIM,
                    ReviewerIssueType.UNSUPPORTED_PROFESSIONAL_CLAIM,
                    ReviewerIssueType.MEDICAL_OVERREACH,
                    ReviewerIssueType.UNSUPPORTED_DISH_GUIDANCE,
                    ReviewerIssueType.UNSUPPORTED_AVOIDANCE,
                    ReviewerIssueType.FORBIDDEN_NUTRITION_ESTIMATE,
                }
            )
        if target is RepairTarget.ORCHESTRATOR:
            return issue_types == {ReviewerIssueType.MISSING_USER_EVIDENCE}
        return False

    @staticmethod
    def _detected_issue_types(context: ReviewerContext) -> set[ReviewerIssueType]:
        detected: set[ReviewerIssueType] = set()
        plan = context.response_plan
        styled = context.styled_response
        if styled.style_profile_version != context.style_profile.version:
            detected.add(ReviewerIssueType.STYLE_DRIFT)
        block_ids = {block.block_id for block in plan.content_blocks}
        if not set(styled.used_block_ids).issubset(block_ids):
            detected.add(ReviewerIssueType.CHANGED_MEANING)
        required_blocks = {block.block_id for block in plan.content_blocks if block.required}
        if not required_blocks.issubset(styled.used_block_ids):
            detected.add(ReviewerIssueType.OMITTED_REQUIRED_CONTENT)
        if not set(plan.citation_refs).issubset(styled.preserved_citation_refs):
            detected.add(ReviewerIssueType.OMITTED_REQUIRED_CONTENT)
        if not set(styled.preserved_citation_refs).issubset(plan.citation_refs):
            detected.add(ReviewerIssueType.UNSUPPORTED_CLAIM)

        def source_refs(kind: ContentBlockKind, *, required_only: bool = False) -> set[str]:
            return {
                reference
                for block in plan.content_blocks
                if block.kind is kind and (not required_only or block.required)
                for reference in block.source_refs
            }

        assessment = context.assessment
        claim_ids = (
            {claim.claim_id for claim in assessment.findings}
            if assessment is not None
            else source_refs(ContentBlockKind.CLAIM)
        )
        action_ids = (
            {action.action_id for action in assessment.actions}
            if assessment is not None
            else source_refs(ContentBlockKind.ACTION)
        )
        risk_flags = (
            set(assessment.risk_flags)
            if assessment is not None
            else source_refs(ContentBlockKind.RISK)
        )
        if not set(styled.used_claim_ids).issubset(claim_ids):
            detected.add(ReviewerIssueType.UNSUPPORTED_CLAIM)
        if not set(styled.used_action_ids).issubset(action_ids):
            detected.add(ReviewerIssueType.UNSUPPORTED_CLAIM)
        if not set(styled.preserved_risk_flags).issubset(risk_flags):
            detected.add(ReviewerIssueType.UNSUPPORTED_CLAIM)
        if (
            not risk_flags.issubset(styled.preserved_risk_flags)
            or not source_refs(ContentBlockKind.CLAIM, required_only=True).issubset(
                styled.used_claim_ids
            )
            or not source_refs(ContentBlockKind.ACTION, required_only=True).issubset(
                styled.used_action_ids
            )
        ):
            detected.add(ReviewerIssueType.OMITTED_REQUIRED_CONTENT)
        if context.available_evidence_ids is not None:
            referenced = set(context.directive.evidence_refs) if context.directive else set()
            if assessment is not None:
                referenced.update(
                    reference for claim in assessment.findings for reference in claim.evidence_refs
                )
            if not referenced.issubset(context.available_evidence_ids):
                detected.add(ReviewerIssueType.MISSING_USER_EVIDENCE)
        if "add_nutrition_estimate" in plan.prohibited_transformations and re.search(
            r"(?:\d+(?:\.\d+)?\s*(?:千卡|卡路里|kcal|克|g\b)|"
            r"(?:热量|蛋白质|脂肪|碳水)[^。；，,]{0,12}\d)",
            styled.text,
            re.IGNORECASE,
        ):
            detected.add(ReviewerIssueType.FORBIDDEN_NUTRITION_ESTIMATE)
        plan_text = "\n".join(block.text for block in plan.content_blocks)
        if (
            "add_avoidance" in plan.prohibited_transformations
            and not re.search(r"(?:避免|不能吃|禁食)", plan_text)
            and re.search(r"(?:绝对不能吃|禁止食用|必须避免|千万别吃)", styled.text)
        ):
            detected.add(ReviewerIssueType.UNSUPPORTED_AVOIDANCE)
        return detected


__all__ = ["ReviewerVerdictValidator"]
