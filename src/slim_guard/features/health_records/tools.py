"""Agent adapters for authoritative record operations, not business workflow routing."""

import json
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from slim_guard.features.health_records.body_fat.values import basis_points
from slim_guard.features.health_records.commands import (
    CreateRecord,
    RecordWriteContext,
    UpdateRecord,
)
from slim_guard.features.health_records.content import (
    BodyFatContent,
    ExerciseContent,
    MealContent,
    MealItem,
    WeightContent,
)
from slim_guard.features.health_records.provenance import FieldProvenance, RecognitionIssue
from slim_guard.features.health_records.references import (
    RecordConflict,
    RecordError,
    RecordReference,
)
from slim_guard.features.health_records.service import RecordService
from slim_guard.features.health_records.tool_arguments import (
    CreateArguments,
    CreateBodyFatArguments,
    CreateExerciseArguments,
    CreateMealArguments,
    CreateWeightArguments,
    GetArguments,
    SearchArguments,
    UpdateArguments,
    UpdateBodyFatArguments,
    UpdateExerciseArguments,
    UpdateMealArguments,
    UpdateStatusArguments,
    UpdateWeightArguments,
)
from slim_guard.features.health_records.views import RecordContent
from slim_guard.features.health_records.weight.values import weight_grams
from slim_guard.tools.contracts import (
    ToolContext,
    ToolEffectLevel,
    ToolEffectStatus,
    ToolMutationKind,
    ToolResult,
)
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

_CREATE = {
    "record_meal": CreateMealArguments,
    "record_exercise": CreateExerciseArguments,
    "record_weight": CreateWeightArguments,
    "record_body_fat": CreateBodyFatArguments,
}
_UPDATE = {
    "update_meal_record": UpdateMealArguments,
    "update_exercise_record": UpdateExerciseArguments,
    "update_weight_record": UpdateWeightArguments,
    "update_body_fat_record": UpdateBodyFatArguments,
    "update_record_status": UpdateStatusArguments,
}
_READ = {"search_health_records": SearchArguments, "get_health_record": GetArguments}


class RecordTools:
    def __init__(self, records: RecordService) -> None:
        self.records = records

    async def lookup(self, context: ToolContext) -> ToolResult | None:
        receipt = await self.records.receipt(
            context.user_id, context.execution_idempotency_key or ""
        )
        return (
            ToolResult.success(
                output=receipt.model_dump(mode="json"), source_ids=(receipt.record.ref.record_id,)
            )
            if receipt
            else None
        )

    async def execute(self, context: ToolContext, arguments: Any) -> ToolResult:
        try:
            if isinstance(arguments, SearchArguments):
                page = await self.records.search(context.user_id, arguments.query)
                return ToolResult.success(
                    output=page.model_dump(mode="json"),
                    source_ids=tuple(r.ref.record_id for r in page.records),
                )
            if isinstance(arguments, GetArguments):
                record = await self.records.get(
                    context.user_id, arguments.kind, arguments.record_id
                )
                return ToolResult.success(
                    output={"record": record.model_dump(mode="json")},
                    source_ids=(record.ref.record_id,),
                )
            if not context.execution_idempotency_key:
                raise RecordError("Missing trusted operation identity")
            write_context = RecordWriteContext(
                user_id=context.user_id,
                thread_id=context.thread_id,
                turn_id=context.turn_id,
                source_item_id=context.source_item_id,
                tool_call_id=context.tool_call_id,
                operation_id=context.execution_idempotency_key,
            )
            command: CreateRecord | UpdateRecord
            if isinstance(arguments, UpdateArguments):
                kind = (
                    arguments.record_kind
                    if isinstance(arguments, UpdateStatusArguments)
                    else arguments.patch.kind
                )
                command = UpdateRecord(
                    target=RecordReference(
                        kind=kind,
                        record_id=arguments.record_id,
                        revision=arguments.expected_revision,
                    ),
                    patch=arguments.patch,
                    complete_tasks=tuple(arguments.complete_tasks),
                )
            else:
                command = self._create(context, arguments)
            command = self._bind_sources(command, context)
            receipt = await self.records.write(write_context, command)
            return ToolResult.success(
                output=receipt.model_dump(mode="json"), source_ids=(receipt.record.ref.record_id,)
            )
        except (RecordError, ValidationError, ValueError) as exc:
            return ToolResult.failed(
                code="record_conflict" if isinstance(exc, RecordConflict) else "record_rejected",
                message=str(exc)[:1024],
                effect_status=ToolEffectStatus.NOT_APPLIED,
            )

    @staticmethod
    def _bind_sources(
        command: CreateRecord | UpdateRecord, context: ToolContext
    ) -> CreateRecord | UpdateRecord:
        def bind(value: Any) -> Any:
            if isinstance(value, dict):
                result = {key: bind(child) for key, child in value.items()}
                if "source_kind" in result and "source_refs" in result:
                    refs = result["source_refs"]
                    if "current-user-message" in refs:
                        if context.source_item_id is None:
                            raise RecordError("No current user message is available")
                        result["source_refs"] = [
                            context.source_item_id if ref == "current-user-message" else ref
                            for ref in refs
                        ]
                return result
            if isinstance(value, list):
                return [bind(child) for child in value]
            return value

        return type(command).model_validate_json(json.dumps(bind(command.model_dump(mode="json"))))

    @staticmethod
    def _create(context: ToolContext, arguments: CreateArguments) -> CreateRecord:
        content: RecordContent
        provenance: list[FieldProvenance] = []
        issues: list[RecognitionIssue] = []

        def field(name: str, evidence: Any = None) -> None:
            provenance.append(
                FieldProvenance(
                    **(evidence or arguments.evidence).model_dump(),
                    field_ref=name,
                    value_revision=1,
                )
            )

        if isinstance(arguments, CreateMealArguments):
            foods = []
            for index, food in enumerate(arguments.foods):
                identity = str(
                    uuid5(NAMESPACE_URL, f"{context.execution_idempotency_key}/food/{index}")
                )
                foods.append(MealItem(item_id=identity, name=food.name, portion=food.portion))
                field(f"foods/{identity}/name", food.evidence)
                if food.portion is not None:
                    field(f"foods/{identity}/portion", food.evidence)
                if food.concern:
                    if (food.evidence or arguments.evidence).source_kind != "image_recognized":
                        raise RecordError("Recognition concern requires image evidence")
                    issues.append(
                        RecognitionIssue(
                            issue_id=str(uuid5(NAMESPACE_URL, f"{identity}/name/issue")),
                            field_ref=f"foods/{identity}/name",
                            value_revision=1,
                            **food.concern.model_dump(),
                        )
                    )
            field("meal_type", arguments.meal_type_evidence)
            field("note")
            content = MealContent(
                meal_type=arguments.meal_type,
                foods=tuple(foods),
                note=arguments.note,
                photo_asset_ids=tuple(
                    dict.fromkeys(
                        (
                            *arguments.photo_asset_ids,
                            *(
                                ref
                                for entry in provenance
                                if entry.source_kind == "image_recognized"
                                for ref in entry.source_refs
                            ),
                        )
                    )
                ),
            )
        elif isinstance(arguments, CreateExerciseArguments):
            values = arguments.model_dump(exclude={"occurrence", "evidence"})
            content = ExerciseContent(**values)
            for name, value in values.items():
                if value is not None:
                    field(name)
        elif isinstance(arguments, CreateWeightArguments):
            value = Decimal(str(arguments.value))
            content = WeightContent(
                weight_grams=weight_grams(value, arguments.unit),
                original_value=value,
                original_unit=arguments.unit,
                condition=arguments.condition,
            )
            field("measurement")
            field("condition")
        elif isinstance(arguments, CreateBodyFatArguments):
            value = Decimal(str(arguments.value))
            content = BodyFatContent(basis_points=basis_points(value), original_value=value)
            field("measurement")
        else:
            raise RecordError("Unknown record command")
        if arguments.occurrence.precision != "unknown":
            field("occurrence")
        return CreateRecord(
            content=content,
            occurrence=arguments.occurrence,
            field_provenance=tuple(provenance),
            recognition_issues=tuple(issues),
        )


def health_record_tool_definitions() -> tuple[RegisteredTool, ...]:
    return tuple(
        RegisteredTool(
            name=name,
            version="record-v5",
            arguments_model=model,
            description=(
                "Read authoritative health records with IDs, revisions, evidence and paginated "
                "history. Choose queries using user meaning; dates refer to occurrence unless "
                "source dates are supplied."
                if name in _READ
                else "Create a health event from user evidence or image recognition, including "
                "uncertain foods. Do not ask for routine confirmation. Keep concerns on fields. "
                "Infer meal type from full context, not upload counts or food category; "
                "unknown is allowed. "
                "For supplements or corrections use the existing record's update tool instead. "
                "occurrence must reflect actual time precision, never invent a timestamp."
                if name in _CREATE
                else "Apply requested typed changes to an existing record at its current revision. "
                "Rename/reject/acknowledge/remove differ; corrections clear obsolete issues. "
                "Use complete_tasks only for associated requests actually solved by this write. "
                "A conflict is not applied: reload and reconsider. Recover with the original ID."
            ),
            effect_level=ToolEffectLevel.READ
            if name in _READ
            else ToolEffectLevel.REVERSIBLE_WRITE,
            mutation_kind=(
                ToolMutationKind.NONE if name in _READ else ToolMutationKind.HEALTH_RECORD
            ),
            idempotent=True,
            timeout_seconds=10,
        )
        for name, model in {**_CREATE, **_UPDATE, **_READ}.items()
    )


def health_record_tool_executors(records: RecordService) -> dict[str, ToolExecutor[Any]]:
    handler = RecordTools(records)
    return {
        name: ToolExecutor(
            arguments_model=model,
            handler=handler.execute,
            lookup_result=handler.lookup if name not in _READ else None,
        )
        for name, model in {**_CREATE, **_UPDATE, **_READ}.items()
    }
