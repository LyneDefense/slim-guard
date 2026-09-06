"""Role-scoped compiler for nutrition specialist context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel

from slim_guard.agents.nutrition.contracts import (
    CalculationInput,
    CalculationObservation,
    EvidencePacketInput,
    KnowledgeInput,
    KnowledgeRetrieval,
    NutritionContext,
    NutritionEvidence,
)

_MISSING = object()


class NutritionContextCompiler:
    """Drops packet metadata and retains only evidence needed for assessment."""

    def compile(
        self,
        packet: EvidencePacketInput,
        *,
        calculation_observations: Sequence[CalculationInput] = (),
        knowledge: KnowledgeInput | None = None,
    ) -> NutritionContext:
        return NutritionContext(
            turn_id=self._text(self._read(packet, "turn_id"), "turn_id"),
            user_request=self._text(
                self._read(packet, "user_request"),
                "user_request",
            ),
            professional_question=self._text(
                self._read(packet, "professional_question"),
                "professional_question",
            ),
            evidence=tuple(
                self._evidence(item)
                for item in self._sequence(self._read(packet, "items"), "items")
            ),
            missing_information=tuple(
                self._text(item, "missing_information")
                for item in self._sequence(
                    self._read(packet, "missing_information", default=()),
                    "missing_information",
                )
            ),
            calculation_observations=tuple(
                self._calculation(item) for item in calculation_observations
            ),
            knowledge=self._knowledge(knowledge),
        )

    def _evidence(self, item: object) -> NutritionEvidence:
        if isinstance(item, NutritionEvidence):
            return item
        evidence_id = self._read_alias(item, "evidence_id", "id")
        source_type = self._read_alias(item, "source_type", "kind", "type")
        uncertainty = self._read(item, "uncertainty", default=None)
        if isinstance(uncertainty, bool):
            uncertainty = "uncertain visual observation" if uncertainty else None
        return NutritionEvidence(
            evidence_id=self._text(evidence_id, "evidence_id"),
            source_type=self._text(source_type, "source_type"),
            authority=self._enum_value(self._read(item, "authority")),
            content=self._content(self._read(item, "content")),
            confidence=self._optional_enum_value(
                self._read(item, "confidence", default=None)
            ),
            uncertainty=(
                self._text(uncertainty, "uncertainty") if uncertainty is not None else None
            ),
        )

    def _calculation(self, item: CalculationInput) -> CalculationObservation:
        if isinstance(item, CalculationObservation):
            return item
        raw = dict(item)
        value = raw.get("value")
        if value is None:
            for field in ("bmi", "net_change_kg", "completion_rate_percent"):
                if field in raw:
                    value = raw[field]
                    break
        if value is not None and (
            not isinstance(value, (int, float, str)) or isinstance(value, bool)
        ):
            raise ValueError("Calculation observation value must be a number or string")
        explicit_details = raw.get("details", raw.get("result"))
        if explicit_details is None:
            envelope_fields = {
                "observation_id",
                "evidence_id",
                "id",
                "calculation_type",
                "tool_name",
                "type",
                "value",
                "unit",
                "inputs",
            }
            details = {
                key: item_value
                for key, item_value in raw.items()
                if key not in envelope_fields
            }
        else:
            details = self._mapping(explicit_details)
        return CalculationObservation(
            observation_id=self._text(
                self._read_alias(item, "observation_id", "evidence_id", "id"),
                "observation_id",
            ),
            calculation_type=self._text(
                self._read_alias(item, "calculation_type", "tool_name", "type"),
                "calculation_type",
            ),
            value=value,
            unit=self._optional_text(self._read(item, "unit", default=None), "unit"),
            inputs=self._mapping(self._read(item, "inputs", default={})),
            details=details,
        )

    @staticmethod
    def _knowledge(value: KnowledgeInput | None) -> KnowledgeRetrieval:
        if value is None:
            return KnowledgeRetrieval.empty()
        if isinstance(value, KnowledgeRetrieval):
            return value
        raw = dict(value)
        if "corpus_status" not in raw and "status" in raw:
            raw["corpus_status"] = raw.pop("status")
        return KnowledgeRetrieval.model_validate(raw)

    @staticmethod
    def _read(value: object, field: str, *, default: object = _MISSING) -> object:
        if isinstance(value, Mapping):
            if field in value:
                return value[field]
        elif hasattr(value, field):
            return getattr(value, field)
        if default is not _MISSING:
            return default
        raise ValueError(f"Evidence packet is missing {field}")

    @classmethod
    def _read_alias(cls, value: object, *fields: str) -> object:
        for field in fields:
            try:
                return cls._read(value, field)
            except ValueError:
                continue
        raise ValueError(f"Evidence item is missing one of: {', '.join(fields)}")

    @staticmethod
    def _sequence(value: object, field: str) -> Sequence[object]:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return value
        raise ValueError(f"Evidence packet field {field} must be a sequence")

    @staticmethod
    def _text(value: object, field: str) -> str:
        if isinstance(value, Enum):
            value = value.value
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Evidence field {field} must be a non-blank string")
        return value.strip()

    @classmethod
    def _optional_text(cls, value: object, field: str) -> str | None:
        if value is None:
            return None
        return cls._text(value, field)

    @staticmethod
    def _enum_value(value: object) -> Any:
        return value.value if isinstance(value, Enum) else value

    @classmethod
    def _optional_enum_value(cls, value: object) -> Any:
        return None if value is None else cls._enum_value(value)

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Mapping):
            return dict(value)
        raise ValueError("Evidence content must be a mapping")

    @classmethod
    def _content(cls, value: object) -> dict[str, Any]:
        if isinstance(value, str):
            return {"text": value}
        return cls._mapping(value)


__all__ = ["NutritionContextCompiler"]
