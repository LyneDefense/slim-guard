from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from slim_guard.agents.contracts import ContractModel, InvocationStatus, KnowledgeCitation


class DishEntityMatchStatus(StrEnum):
    EXACT = "exact"
    ALIAS = "alias"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class DishRuleEffect(StrEnum):
    ALLOW = "allow"
    ADJUST = "adjust"
    LIMIT = "limit"
    AVOID = "avoid"
    REQUIRE_CONFIRMATION = "require_confirmation"


class DishLookupInput(ContractModel):
    dish_ref: str = Field(pattern=r"^dish-[A-Za-z0-9_-]+$", max_length=128)
    name: str = Field(min_length=1, max_length=128)
    preparation_terms: tuple[str, ...] = Field(default=(), max_length=10)


class DishConstraintInput(ContractModel):
    value: str = Field(min_length=1, max_length=256)
    evidence_ref: str = Field(min_length=1, max_length=256)


class DishLookupPlan(ContractModel):
    schema_version: Literal["1"] = "1"
    dishes: tuple[DishLookupInput, ...] = Field(min_length=1, max_length=20)
    user_goal_tags: tuple[str, ...] = Field(default=(), max_length=16)
    applicability_tags: tuple[str, ...] = Field(default=(), max_length=32)
    constraints: tuple[DishConstraintInput, ...] = Field(default=(), max_length=32)
    rag_queries: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("user_goal_tags", "applicability_tags", "rag_queries")
    @classmethod
    def validate_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(" ".join(item.split()) for item in value)
        if any(not item for item in normalized):
            raise ValueError("Lookup plan lists cannot contain blank values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Lookup plan lists cannot contain duplicates")
        return normalized


class DishEntityMatch(ContractModel):
    status: DishEntityMatchStatus
    query_name: str = Field(min_length=1, max_length=128)
    dish_entity_id: str | None = Field(default=None, min_length=1, max_length=128)
    canonical_name: str | None = Field(default=None, min_length=1, max_length=128)
    source_version: str | None = Field(default=None, min_length=1, max_length=128)
    candidate_entity_ids: tuple[str, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        matched = self.status in {DishEntityMatchStatus.EXACT, DishEntityMatchStatus.ALIAS}
        if matched and (
            self.dish_entity_id is None
            or self.canonical_name is None
            or self.source_version is None
        ):
            raise ValueError("A matched dish requires an entity, name, and source version")
        if not matched and self.dish_entity_id is not None:
            raise ValueError("An unresolved dish cannot select an entity")
        if self.status is DishEntityMatchStatus.AMBIGUOUS and not self.candidate_entity_ids:
            raise ValueError("An ambiguous dish needs candidate entity IDs")
        if self.status is not DishEntityMatchStatus.AMBIGUOUS and self.candidate_entity_ids:
            raise ValueError("Only ambiguous matches may contain candidate entity IDs")
        return self


class DishTraitEvidence(ContractModel):
    trait_id: str = Field(min_length=1, max_length=128)
    trait: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=1000)
    certainty: Literal["defined", "typical", "possible"]
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=16)


class DishRuleEvidence(ContractModel):
    rule_id: str = Field(min_length=1, max_length=128)
    condition_type: Literal["general", "goal", "constraint", "medical_boundary"]
    effect: DishRuleEffect
    statement: str = Field(min_length=1, max_length=1000)
    applicability: tuple[str, ...] = Field(default=(), max_length=32)
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    user_constraint_refs: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_avoid(self) -> Self:
        if self.effect is DishRuleEffect.AVOID and (
            self.condition_type not in {"constraint", "medical_boundary"}
            or not self.user_constraint_refs
        ):
            raise ValueError("Avoid rules require a matching user constraint")
        return self


class DishRagEvidence(ContractModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=16_000)
    citation_ref: str = Field(min_length=1, max_length=128)


class DishEvidence(ContractModel):
    dish_ref: str = Field(pattern=r"^dish-[A-Za-z0-9_-]+$", max_length=128)
    entity_match: DishEntityMatch
    traits: tuple[DishTraitEvidence, ...] = Field(default=(), max_length=64)
    rules: tuple[DishRuleEvidence, ...] = Field(default=(), max_length=64)
    rag_evidence: tuple[DishRagEvidence, ...] = Field(default=(), max_length=20)
    citations: tuple[KnowledgeCitation, ...] = Field(default=(), max_length=64)
    missing_information: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        groups = (
            tuple(item.trait_id for item in self.traits),
            tuple(item.rule_id for item in self.rules),
            tuple(item.evidence_id for item in self.rag_evidence),
            tuple(item.citation_id for item in self.citations),
        )
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("Dish evidence IDs must be unique within each group")
        return self


class DishEvidenceBundle(ContractModel):
    schema_version: Literal["1"] = "1"
    dishes: tuple[DishEvidence, ...] = Field(min_length=1, max_length=20)
    corpus_status: Literal["empty", "available", "unavailable", "error"]
    retrieval_receipt_ids: tuple[str, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def validate_refs(self) -> Self:
        refs = tuple(item.dish_ref for item in self.dishes)
        if len(refs) != len(set(refs)):
            raise ValueError("Dish evidence references must be unique")
        return self


@dataclass(frozen=True, slots=True)
class NutritionRetrievalResult:
    status: InvocationStatus
    evidence: DishEvidenceBundle | None
    tool_call_count: int
    failure_code: str | None = None


__all__ = [
    "DishEntityMatch",
    "DishEntityMatchStatus",
    "DishConstraintInput",
    "DishEvidence",
    "DishEvidenceBundle",
    "DishLookupInput",
    "DishLookupPlan",
    "DishRagEvidence",
    "DishRuleEffect",
    "DishRuleEvidence",
    "DishTraitEvidence",
    "NutritionRetrievalResult",
]
