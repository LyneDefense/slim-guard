"""Read-only, deterministic tools available to the nutrition specialist."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from slim_guard.agent_models.gateway import ToolDefinition


class NutritionToolResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class NutritionToolFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool = False


class NutritionToolResult(BaseModel):
    """ToolResult-compatible contract without importing the Harness tool graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: NutritionToolResultStatus
    output: dict[str, Any] = Field(default_factory=dict)
    source_ids: tuple[str, ...] = ()
    failure: NutritionToolFailure | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is NutritionToolResultStatus.SUCCEEDED and self.failure is not None:
            raise ValueError("Successful nutrition tool results cannot contain a failure")
        if self.status is NutritionToolResultStatus.FAILED and self.failure is None:
            raise ValueError("Failed nutrition tool results require a failure")
        return self

    @classmethod
    def success(
        cls,
        *,
        output: dict[str, Any],
        source_ids: tuple[str, ...] = (),
    ) -> NutritionToolResult:
        return cls(
            status=NutritionToolResultStatus.SUCCEEDED,
            output=output,
            source_ids=source_ids,
        )

    @classmethod
    def failed(
        cls,
        *,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> NutritionToolResult:
        return cls(
            status=NutritionToolResultStatus.FAILED,
            failure=NutritionToolFailure(
                code=code,
                message=message,
                retryable=retryable,
            ),
        )

    def to_model_content(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class NutritionToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BmiArguments(NutritionToolArguments):
    weight_kg: float = Field(gt=0, le=1_000)
    height_cm: float = Field(gt=0, le=300)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("weight_kg", "height_cm", mode="before")
    @classmethod
    def reject_boolean_number(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Numeric inputs cannot be booleans")
        return value


class WeightMeasurement(NutritionToolArguments):
    weight_kg: float = Field(gt=0, le=1_000)
    measured_at: datetime
    evidence_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("weight_kg", mode="before")
    @classmethod
    def reject_boolean_number(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Weight cannot be a boolean")
        return value

    @field_validator("measured_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Weight timestamps must be timezone-aware")
        return value


class WeightTrendArguments(NutritionToolArguments):
    measurements: tuple[WeightMeasurement, ...] = Field(min_length=1, max_length=366)


class CheckinAdherenceArguments(NutritionToolArguments):
    expected_checkins: tuple[str, ...] = Field(default=(), max_length=512)
    completed_checkins: tuple[str, ...] = Field(default=(), max_length=512)
    expected: dict[str, int] | None = None
    completed: dict[str, int] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        list_shape = bool(self.expected_checkins or self.completed_checkins)
        count_shape = self.expected is not None or self.completed is not None
        if list_shape and count_shape:
            raise ValueError("Use either named check-ins or category counts, not both")
        if not list_shape and not count_shape:
            raise ValueError("Expected and completed check-in data are required")
        if len(self.expected_checkins) != len(set(self.expected_checkins)):
            raise ValueError("Expected check-ins must be unique")
        if len(self.completed_checkins) != len(set(self.completed_checkins)):
            raise ValueError("Completed check-ins must be unique")
        for counts in (self.expected, self.completed):
            if counts is not None and any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in counts.values()
            ):
                raise ValueError("Check-in counts must be non-negative integers")
            if counts is not None and len(counts) > 64:
                raise ValueError("At most 64 check-in categories are allowed")
        return self


class KnowledgeSearchArguments(NutritionToolArguments):
    query: str = Field(min_length=1, max_length=1_000)
    max_results: int = Field(default=5, ge=1, le=20)


class KnowledgeSourceArguments(NutritionToolArguments):
    source_id: str = Field(min_length=1, max_length=128)
    chunk_id: str | None = Field(default=None, min_length=1, max_length=128)


class EmptyKnowledgeSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_status: Literal["empty"] = "empty"
    citations: tuple[dict[str, Any], ...] = ()
    query_summary: str = "No approved nutrition corpus is configured"

    @model_validator(mode="after")
    def reject_fabricated_citations(self) -> Self:
        if self.citations:
            raise ValueError("An empty corpus cannot contain citations")
        return self


class EmptyKnowledgeSourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_status: Literal["empty"] = "empty"
    source_id: str
    chunk_id: str | None = None
    source: None = None


class NutritionKnowledgeRepository(Protocol):
    async def search(self, *, query: str, max_results: int) -> Mapping[str, Any]: ...

    async def get_source(
        self,
        *,
        source_id: str,
        chunk_id: str | None = None,
    ) -> Mapping[str, Any]: ...


class EmptyNutritionKnowledgeRepository:
    """Safe default: explicitly empty, with no synthetic titles or articles."""

    async def search(self, *, query: str, max_results: int) -> Mapping[str, Any]:
        del query, max_results
        return EmptyKnowledgeSearchResult().model_dump(mode="json")

    async def get_source(
        self,
        *,
        source_id: str,
        chunk_id: str | None = None,
    ) -> Mapping[str, Any]:
        return EmptyKnowledgeSourceResult(
            source_id=source_id,
            chunk_id=chunk_id,
        ).model_dump(mode="json")


class NutritionToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    description: str
    version: str = "1"
    arguments_model: type[BaseModel]
    effect_level: Literal["read"] = "read"
    idempotent: Literal[True] = True

    def model_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.arguments_model.model_json_schema(),
            version=self.version,
        )


def calculate_bmi(*, weight_kg: float, height_cm: float) -> dict[str, Any]:
    """Return a deterministic BMI observation, not a diagnosis."""

    weight = Decimal(str(weight_kg))
    height_m = Decimal(str(height_cm)) / Decimal("100")
    bmi = (weight / (height_m * height_m)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if bmi < Decimal("18.5"):
        category = "below_reference_range"
    elif bmi < Decimal("25"):
        category = "reference_range"
    elif bmi < Decimal("30"):
        category = "above_reference_range"
    else:
        category = "well_above_reference_range"
    return {
        "calculation_type": "bmi",
        "value": float(bmi),
        "unit": "kg/m2",
        "category": category,
        "inputs": {"weight_kg": float(weight), "height_cm": float(height_cm)},
        "interpretation_scope": "screening_observation_only",
    }


def calculate_weight_trend(
    *,
    measurements: Sequence[WeightMeasurement | Mapping[str, Any]],
) -> dict[str, Any]:
    """Calculate net and weekly weight change after chronological sorting."""

    points = tuple(
        item if isinstance(item, WeightMeasurement) else WeightMeasurement.model_validate(item)
        for item in measurements
    )
    ordered = sorted(points, key=lambda item: item.measured_at)
    first = ordered[0]
    latest = ordered[-1]
    change = (Decimal(str(latest.weight_kg)) - Decimal(str(first.weight_kg))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    elapsed_days = Decimal(str((latest.measured_at - first.measured_at).total_seconds() / 86_400))
    weekly = (
        (change / elapsed_days * Decimal("7")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if elapsed_days > 0
        else None
    )
    return {
        "calculation_type": "weight_trend",
        "sample_count": len(ordered),
        "first_weight_kg": first.weight_kg,
        "latest_weight_kg": latest.weight_kg,
        "change_kg": float(change),
        "elapsed_days": float(elapsed_days.quantize(Decimal("0.01"))),
        "rate_kg_per_week": float(weekly) if weekly is not None else None,
        "direction": "decreasing" if change < 0 else "increasing" if change > 0 else "stable",
        "status": "calculated"
        if len(ordered) >= 2 and elapsed_days > 0
        else "insufficient_interval",
        "evidence_refs": [item.evidence_id for item in ordered if item.evidence_id is not None],
    }


def compare_checkin_adherence(
    *,
    expected_checkins: Sequence[str] = (),
    completed_checkins: Sequence[str] = (),
    expected: Mapping[str, int] | None = None,
    completed: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Compare known completion facts with an explicit schedule."""

    if expected is not None or completed is not None:
        expected_counts = dict(expected or {})
        completed_counts = dict(completed or {})
        categories = sorted(set(expected_counts) | set(completed_counts))
        by_category = {}
        expected_total = 0
        completed_total = 0
        for category in categories:
            target = expected_counts.get(category, 0)
            observed = completed_counts.get(category, 0)
            credited = min(target, observed)
            expected_total += target
            completed_total += credited
            by_category[category] = {
                "expected_count": target,
                "completed_count": observed,
                "credited_count": credited,
            }
        missed = []
        unexpected = []
    else:
        expected_set = set(expected_checkins)
        completed_set = set(completed_checkins)
        expected_total = len(expected_set)
        completed_total = len(expected_set.intersection(completed_set))
        by_category = {}
        missed = sorted(expected_set.difference(completed_set))
        unexpected = sorted(completed_set.difference(expected_set))
    rate = (
        float(
            (Decimal(completed_total) / Decimal(expected_total) * Decimal("100")).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        )
        if expected_total
        else None
    )
    return {
        "calculation_type": "checkin_adherence",
        "expected_count": expected_total,
        "completed_count": completed_total,
        "completion_rate_percent": rate,
        "status": "calculated" if expected_total else "not_applicable",
        "by_category": by_category,
        "missed_checkins": missed,
        "unexpected_checkins": unexpected,
    }


class NutritionToolRegistry:
    """Closed catalog of nutrition capabilities; every entry is read-only."""

    def __init__(
        self,
        knowledge_repository: NutritionKnowledgeRepository | None = None,
    ) -> None:
        self._knowledge = knowledge_repository or EmptyNutritionKnowledgeRepository()
        tools = (
            NutritionToolDefinition(
                name="calculate_bmi",
                description=(
                    "Calculate BMI deterministically from supplied height and weight evidence."
                ),
                arguments_model=BmiArguments,
            ),
            NutritionToolDefinition(
                name="calculate_weight_trend",
                description=(
                    "Calculate chronological net and weekly weight change from supplied records."
                ),
                arguments_model=WeightTrendArguments,
            ),
            NutritionToolDefinition(
                name="compare_checkin_adherence",
                description=(
                    "Compare supplied check-in completions against an explicit expected schedule."
                ),
                arguments_model=CheckinAdherenceArguments,
            ),
            NutritionToolDefinition(
                name="search_nutrition_knowledge",
                description="Search only the configured approved nutrition knowledge corpus.",
                arguments_model=KnowledgeSearchArguments,
            ),
            NutritionToolDefinition(
                name="get_nutrition_source",
                description=(
                    "Fetch one source from the configured approved nutrition knowledge corpus."
                ),
                arguments_model=KnowledgeSourceArguments,
            ),
        )
        self._ordered = tools
        self._by_name = {tool.name: tool for tool in tools}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self._ordered)

    @property
    def versions(self) -> dict[str, str]:
        return {tool.name: tool.version for tool in self._ordered}

    def resolve(self, name: str) -> NutritionToolDefinition:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"Nutrition tool is not registered: {name}") from None

    def model_definitions(
        self,
        names: Sequence[str] | None = None,
    ) -> tuple[ToolDefinition, ...]:
        selected = self._ordered if names is None else tuple(self.resolve(name) for name in names)
        return tuple(tool.model_definition() for tool in selected)

    async def execute(self, name: str, arguments: Mapping[str, Any]) -> NutritionToolResult:
        try:
            definition = self.resolve(name)
        except KeyError:
            return NutritionToolResult.failed(
                code="unknown_nutrition_tool",
                message=f"Nutrition tool is not registered: {name}",
            )
        try:
            parsed = definition.arguments_model.model_validate(arguments)
        except ValidationError as exc:
            return NutritionToolResult.failed(
                code="invalid_nutrition_tool_arguments",
                message=str(exc),
            )

        if isinstance(parsed, BmiArguments):
            output = calculate_bmi(weight_kg=parsed.weight_kg, height_cm=parsed.height_cm)
            return NutritionToolResult.success(output=output, source_ids=parsed.evidence_refs)
        if isinstance(parsed, WeightTrendArguments):
            output = calculate_weight_trend(measurements=parsed.measurements)
            refs = tuple(
                point.evidence_id for point in parsed.measurements if point.evidence_id is not None
            )
            return NutritionToolResult.success(output=output, source_ids=refs)
        if isinstance(parsed, CheckinAdherenceArguments):
            return NutritionToolResult.success(
                output=compare_checkin_adherence(
                    expected_checkins=parsed.expected_checkins,
                    completed_checkins=parsed.completed_checkins,
                    expected=parsed.expected,
                    completed=parsed.completed,
                )
            )
        if isinstance(parsed, KnowledgeSearchArguments):
            output = dict(
                await self._knowledge.search(
                    query=parsed.query,
                    max_results=parsed.max_results,
                )
            )
            return self._knowledge_result(output)
        if isinstance(parsed, KnowledgeSourceArguments):
            output = dict(
                await self._knowledge.get_source(
                    source_id=parsed.source_id,
                    chunk_id=parsed.chunk_id,
                )
            )
            return self._knowledge_result(output)
        return NutritionToolResult.failed(
            code="unsupported_nutrition_tool",
            message=f"No handler is installed for nutrition tool: {name}",
        )

    @staticmethod
    def _knowledge_result(output: dict[str, Any]) -> NutritionToolResult:
        status = output.get("corpus_status", output.get("status"))
        if status == "empty":
            citations = output.get("citations", ())
            if citations:
                return NutritionToolResult.failed(
                    code="invalid_empty_knowledge_result",
                    message="An empty nutrition corpus cannot return citations",
                )
            output["corpus_status"] = "empty"
        return NutritionToolResult.success(output=output)


__all__ = [
    "BmiArguments",
    "CheckinAdherenceArguments",
    "EmptyNutritionKnowledgeRepository",
    "KnowledgeSearchArguments",
    "KnowledgeSourceArguments",
    "NutritionKnowledgeRepository",
    "NutritionToolDefinition",
    "NutritionToolFailure",
    "NutritionToolRegistry",
    "NutritionToolResult",
    "NutritionToolResultStatus",
    "WeightMeasurement",
    "WeightTrendArguments",
    "calculate_bmi",
    "calculate_weight_trend",
    "compare_checkin_adherence",
]
