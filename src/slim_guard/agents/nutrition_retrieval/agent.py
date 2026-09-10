"""Read-only dish entity and governed nutrition knowledge retrieval."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast

from slim_guard.agents.contracts import InvocationStatus, KnowledgeCitation
from slim_guard.agents.dish_recognition import ConfirmedDishSet
from slim_guard.agents.nutrition.contracts import KnowledgeCorpusStatus
from slim_guard.agents.nutrition.knowledge import (
    CitationValidationPolicy,
    KnowledgeCandidateBinder,
)
from slim_guard.agents.nutrition_retrieval.contracts import (
    DishEntityMatch,
    DishEntityMatchStatus,
    DishEvidence,
    DishEvidenceBundle,
    DishLookupInput,
    DishLookupPlan,
    DishRagEvidence,
    DishRuleEffect,
    DishRuleEvidence,
    DishTraitEvidence,
    NutritionRetrievalResult,
)
from slim_guard.dish_knowledge import (
    DishCatalogMatchStatus,
    DishCatalogRepository,
    DishCatalogStatus,
    DishRule,
)


class NutritionKnowledgeSearch(Protocol):
    async def search(
        self,
        *,
        query: str,
        max_results: int,
        metadata_filter: Mapping[str, Any] | None = None,
        retrieved_in_invocation_id: str | None = None,
    ) -> Mapping[str, Any]: ...


class NutritionRetrievalAgent:
    def __init__(
        self,
        *,
        catalog: DishCatalogRepository,
        knowledge: NutritionKnowledgeSearch | None = None,
        max_rag_results_per_dish: int = 3,
    ) -> None:
        self._catalog = catalog
        self._knowledge = knowledge
        self._max_rag_results = max_rag_results_per_dish
        self._binder = KnowledgeCandidateBinder()

    async def run(
        self,
        *,
        invocation_id: str,
        dishes: ConfirmedDishSet,
        plan: DishLookupPlan | None = None,
    ) -> NutritionRetrievalResult:
        active_plan = plan or DishLookupPlan(
            dishes=tuple(
                DishLookupInput(dish_ref=item.dish_ref, name=item.name)
                for item in dishes.dishes
            ),
            user_goal_tags=("weight_management",),
            applicability_tags=("adult",),
            rag_queries=tuple(item.name for item in dishes.dishes),
        )
        if {item.dish_ref for item in active_plan.dishes} != {
            item.dish_ref for item in dishes.dishes
        }:
            return NutritionRetrievalResult(
                status=InvocationStatus.FAILED,
                evidence=None,
                tool_call_count=0,
                failure_code="dish_lookup_plan_mismatch",
            )
        try:
            evidence: list[DishEvidence] = []
            any_catalog = False
            rag_statuses: list[KnowledgeCorpusStatus] = []
            for dish in active_plan.dishes:
                item, matched = await self._retrieve_dish(
                    invocation_id=invocation_id,
                    dish=dish,
                    plan=active_plan,
                    rag_statuses=rag_statuses,
                )
                evidence.append(item)
                any_catalog = any_catalog or matched
        except Exception:
            return NutritionRetrievalResult(
                status=InvocationStatus.FAILED,
                evidence=None,
                tool_call_count=0,
                failure_code="nutrition_retrieval_error",
            )

        if any_catalog or KnowledgeCorpusStatus.AVAILABLE in rag_statuses:
            corpus_status = "available"
        elif KnowledgeCorpusStatus.UNAVAILABLE in rag_statuses:
            corpus_status = "unavailable"
        elif KnowledgeCorpusStatus.ERROR in rag_statuses:
            corpus_status = "error"
        else:
            corpus_status = "empty"
        bundle = DishEvidenceBundle(
            dishes=tuple(evidence),
            corpus_status=corpus_status,
            retrieval_receipt_ids=(),
        )
        return NutritionRetrievalResult(
            status=InvocationStatus.SUCCEEDED,
            evidence=bundle,
            tool_call_count=len(active_plan.dishes) * (2 if self._knowledge else 1),
        )

    async def _retrieve_dish(
        self,
        *,
        invocation_id: str,
        dish: DishLookupInput,
        plan: DishLookupPlan,
        rag_statuses: list[KnowledgeCorpusStatus],
    ) -> tuple[DishEvidence, bool]:
        result = await self._catalog.search_published(dish.name)
        matched = result.status in {
            DishCatalogMatchStatus.EXACT,
            DishCatalogMatchStatus.ALIAS,
        }
        traits: tuple[DishTraitEvidence, ...] = ()
        rules: tuple[DishRuleEvidence, ...] = ()
        missing: list[str] = []
        if matched:
            entity = result.candidates[0].entity
            entry = await self._catalog.get_published_entry(entity.id)
            if entry is None or entry.entity.status != DishCatalogStatus.PUBLISHED.value:
                raise ValueError("Published search returned an unavailable dish entity")
            entity_match = DishEntityMatch(
                status=DishEntityMatchStatus(result.status.value),
                query_name=dish.name,
                dish_entity_id=entry.entity.id,
                canonical_name=entry.entity.canonical_name,
                source_version=entry.entity.version,
            )
            traits = tuple(
                DishTraitEvidence(
                    trait_id=item.id,
                    trait=item.trait_key,
                    statement=item.statement,
                    certainty=item.certainty,
                    source_refs=(item.source_ref,),
                )
                for item in entry.traits
            )
            rules = tuple(
                rule
                for item in entry.rules
                if (rule := self._applicable_rule(item, plan)) is not None
            )
            if not rules:
                missing.append("没有适用于当前目标和限制的已审核菜品规则")
        else:
            entity_match = DishEntityMatch(
                status=DishEntityMatchStatus(result.status.value),
                query_name=dish.name,
                candidate_entity_ids=tuple(item.entity.id for item in result.candidates),
            )
            missing.append(
                "菜名匹配到多个条目，需要确认"
                if result.status is DishCatalogMatchStatus.AMBIGUOUS
                else "菜品库中没有已发布条目"
            )

        citations: tuple[KnowledgeCitation, ...] = ()
        rag_evidence: tuple[DishRagEvidence, ...] = ()
        if self._knowledge is not None:
            raw = await self._knowledge.search(
                query=dish.name,
                max_results=self._max_rag_results,
                metadata_filter={"applicability": list(plan.applicability_tags)},
                retrieved_in_invocation_id=invocation_id,
            )
            bound = self._binder.bind_search_result(
                invocation_id=invocation_id,
                result=raw,
                policy=CitationValidationPolicy(
                    required_applicability=plan.applicability_tags,
                ),
            )
            rag_statuses.append(bound.corpus_status)
            citations = bound.citations
            selected = {item.citation_id for item in citations}
            rag_evidence = tuple(
                DishRagEvidence(
                    evidence_id=f"rag-{candidate.candidate_id}",
                    statement=candidate.content,
                    citation_ref=candidate.citation_id,
                )
                for candidate in bound.candidates
                if candidate.citation_id in selected
            )
            if not rag_evidence:
                missing.append("营养资料库没有检索到适用内容")
        else:
            rag_statuses.append(KnowledgeCorpusStatus.UNAVAILABLE)
            missing.append("营养资料库尚未配置")
        return (
            DishEvidence(
                dish_ref=dish.dish_ref,
                entity_match=entity_match,
                traits=traits,
                rules=rules,
                rag_evidence=rag_evidence,
                citations=citations,
                missing_information=tuple(missing),
            ),
            matched,
        )

    @staticmethod
    def _applicable_rule(rule: DishRule, plan: DishLookupPlan) -> DishRuleEvidence | None:
        if rule.applicability and not set(rule.applicability).issubset(
            set(plan.applicability_tags)
        ):
            return None
        user_refs: tuple[str, ...] = ()
        if rule.condition_type == "goal":
            if rule.condition_value not in plan.user_goal_tags:
                return None
        elif rule.condition_type in {"constraint", "medical_boundary"}:
            user_refs = tuple(
                item.evidence_ref
                for item in plan.constraints
                if item.value.casefold() == (rule.condition_value or "").casefold()
            )
            if not user_refs:
                return None
        return DishRuleEvidence(
            rule_id=rule.id,
            condition_type=cast(Any, rule.condition_type),
            effect=cast(DishRuleEffect, rule.effect),
            statement=rule.statement,
            applicability=rule.applicability,
            source_refs=(rule.source_ref,),
            user_constraint_refs=user_refs,
        )


__all__ = ["NutritionKnowledgeSearch", "NutritionRetrievalAgent"]
