"""Deterministic integrity checks applied after style generation."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from slim_guard.agents.contracts import ContentBlockKind, StyledResponse
from slim_guard.agents.style.contracts import StyleContext


class StyleIntegrityIssueCode(StrEnum):
    PROFILE_VERSION_CHANGED = "profile_version_changed"
    REQUIRED_BLOCK_OMITTED = "required_block_omitted"
    PROTECTED_BLOCK_OMITTED = "protected_block_omitted"
    UNKNOWN_BLOCK_ADDED = "unknown_block_added"
    CLAIM_REFERENCE_CHANGED = "claim_reference_changed"
    ACTION_REFERENCE_CHANGED = "action_reference_changed"
    RISK_REFERENCE_CHANGED = "risk_reference_changed"
    CITATION_REFERENCE_CHANGED = "citation_reference_changed"
    PROTECTED_CONTENT_CHANGED = "protected_content_changed"
    NUMBER_CHANGED = "number_changed"
    PROHIBITED_PHRASE_USED = "prohibited_phrase_used"
    UNSUPPORTED_CONTENT_ADDED = "unsupported_content_added"


@dataclass(frozen=True, slots=True)
class StyleIntegrityIssue:
    code: StyleIntegrityIssueCode
    subject: str


@dataclass(frozen=True, slots=True)
class StyleValidationReport:
    issues: tuple[StyleIntegrityIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code.value for issue in self.issues)


class StyleIntegrityError(ValueError):
    def __init__(self, report: StyleValidationReport) -> None:
        joined = ", ".join(report.issue_codes) or "unknown"
        super().__init__(f"Styled response failed deterministic integrity checks: {joined}")
        self.report = report


_PROTECTED_KINDS = frozenset(
    {
        ContentBlockKind.FACT,
        ContentBlockKind.CLAIM,
        ContentBlockKind.ACTION,
        ContentBlockKind.RISK,
        ContentBlockKind.UNCERTAINTY,
    }
)
_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)*(?:%|％)?")
_IGNORABLE_TEXT = re.compile(r"[\s\u3000，。！？；：、,.!?;:'\"“”‘’（）()【】\[\]《》<>—–-]+")
_HIGH_CONSEQUENCE_TERMS = (
    "已记录",
    "已保存",
    "保存成功",
    "写入失败",
    "患有",
    "得了",
    "诊断",
    "糖尿病",
    "高血压",
    "胰岛素抵抗",
    "处方",
    "卡路里",
    "热量",
    "建议",
    "应该",
    "必须",
)


class StyleResponseValidator:
    """Checks invariants that do not require another language model.

    Protected blocks intentionally remain verbatim modulo whitespace and punctuation.
    This conservative boundary prevents a style-only model from changing facts,
    conclusions, actions, risks, or uncertainty before the reviewer exists.
    """

    def validate(
        self,
        context: StyleContext,
        response: StyledResponse,
    ) -> StyleValidationReport:
        plan = context.response_plan
        issues: list[StyleIntegrityIssue] = []
        known_blocks = {block.block_id for block in plan.content_blocks}
        used_blocks = set(response.used_block_ids)
        required_blocks = {block.block_id for block in plan.content_blocks if block.required}
        protected_blocks = {
            block.block_id for block in plan.content_blocks if block.kind in _PROTECTED_KINDS
        }

        if response.style_profile_version != context.profile.version:
            issues.append(
                StyleIntegrityIssue(
                    StyleIntegrityIssueCode.PROFILE_VERSION_CHANGED,
                    "style_profile_version",
                )
            )
        for block_id in sorted(required_blocks - used_blocks):
            issues.append(
                StyleIntegrityIssue(StyleIntegrityIssueCode.REQUIRED_BLOCK_OMITTED, block_id)
            )
        for block_id in sorted(protected_blocks - used_blocks):
            issues.append(
                StyleIntegrityIssue(StyleIntegrityIssueCode.PROTECTED_BLOCK_OMITTED, block_id)
            )
        for block_id in sorted(used_blocks - known_blocks):
            issues.append(
                StyleIntegrityIssue(StyleIntegrityIssueCode.UNKNOWN_BLOCK_ADDED, block_id)
            )

        expected_claims = self._source_refs(context, ContentBlockKind.CLAIM)
        expected_actions = self._source_refs(context, ContentBlockKind.ACTION)
        expected_risks = self._risk_refs(context)
        self._compare_refs(
            issues,
            StyleIntegrityIssueCode.CLAIM_REFERENCE_CHANGED,
            "used_claim_ids",
            expected_claims,
            set(response.used_claim_ids),
        )
        self._compare_refs(
            issues,
            StyleIntegrityIssueCode.ACTION_REFERENCE_CHANGED,
            "used_action_ids",
            expected_actions,
            set(response.used_action_ids),
        )
        self._compare_refs(
            issues,
            StyleIntegrityIssueCode.RISK_REFERENCE_CHANGED,
            "preserved_risk_flags",
            expected_risks,
            set(response.preserved_risk_flags),
        )
        self._compare_refs(
            issues,
            StyleIntegrityIssueCode.CITATION_REFERENCE_CHANGED,
            "preserved_citation_refs",
            set(plan.citation_refs),
            set(response.preserved_citation_refs),
        )

        normalized_response = self._semantic_text(response.text)
        for block in plan.content_blocks:
            if block.kind not in _PROTECTED_KINDS:
                continue
            if self._semantic_text(block.text) not in normalized_response:
                issues.append(
                    StyleIntegrityIssue(
                        StyleIntegrityIssueCode.PROTECTED_CONTENT_CHANGED,
                        block.block_id,
                    )
                )

        selected_text = "\n".join(
            block.text for block in plan.content_blocks if block.block_id in used_blocks
        )
        if self._numbers(selected_text) != self._numbers(response.text):
            issues.append(StyleIntegrityIssue(StyleIntegrityIssueCode.NUMBER_CHANGED, "text"))

        normalized_candidate = unicodedata.normalize("NFKC", response.text).casefold()
        normalized_source = unicodedata.normalize("NFKC", selected_text).casefold()
        for term in _HIGH_CONSEQUENCE_TERMS:
            normalized_term = unicodedata.normalize("NFKC", term).casefold()
            if normalized_term in normalized_candidate and normalized_term not in normalized_source:
                issues.append(
                    StyleIntegrityIssue(
                        StyleIntegrityIssueCode.UNSUPPORTED_CONTENT_ADDED,
                        term,
                    )
                )
        for phrase in context.profile.prohibited_phrases:
            if unicodedata.normalize("NFKC", phrase).casefold() in normalized_candidate:
                issues.append(
                    StyleIntegrityIssue(
                        StyleIntegrityIssueCode.PROHIBITED_PHRASE_USED,
                        phrase,
                    )
                )

        return StyleValidationReport(issues=tuple(issues))

    def require_valid(self, context: StyleContext, response: StyledResponse) -> None:
        report = self.validate(context, response)
        if not report.is_valid:
            raise StyleIntegrityError(report)

    @staticmethod
    def _compare_refs(
        issues: list[StyleIntegrityIssue],
        code: StyleIntegrityIssueCode,
        subject: str,
        expected: set[str],
        actual: set[str],
    ) -> None:
        if expected != actual:
            issues.append(StyleIntegrityIssue(code, subject))

    @staticmethod
    def _source_refs(context: StyleContext, kind: ContentBlockKind) -> set[str]:
        return {
            reference
            for block in context.response_plan.content_blocks
            if block.kind is kind
            for reference in block.source_refs
        }

    @classmethod
    def _risk_refs(cls, context: StyleContext) -> set[str]:
        if context.assessment is not None:
            return set(context.assessment.risk_flags)
        return cls._source_refs(context, ContentBlockKind.RISK)

    @staticmethod
    def _semantic_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return _IGNORABLE_TEXT.sub("", normalized)

    @staticmethod
    def _numbers(value: str) -> Counter[str]:
        normalized = unicodedata.normalize("NFKC", value)
        return Counter(
            match.group(0).replace(",", "")
            for match in _NUMBER_PATTERN.finditer(normalized)
        )


__all__ = [
    "StyleIntegrityError",
    "StyleIntegrityIssue",
    "StyleIntegrityIssueCode",
    "StyleResponseValidator",
    "StyleValidationReport",
]
