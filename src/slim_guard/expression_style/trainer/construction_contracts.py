"""Model-facing construction drafts; provenance is assigned only by the application."""

from typing import Annotated

from pydantic import Field, StringConstraints

from slim_guard.expression_style.package import Artifact

Reference = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class RuleDraft(Artifact):
    rule_id: Reference
    text: str = Field(min_length=1, max_length=500)
    boundary: str = Field(min_length=1, max_length=500)
    evidence_refs: tuple[Reference, ...] = Field(min_length=1)


class GuideDraft(Artifact):
    summary: str = Field(min_length=1, max_length=1000)
    rules: tuple[RuleDraft, ...] = Field(default=(), max_length=32)
    prohibited_phrases: tuple[str, ...] = Field(default=(), max_length=32)


class ExampleSelection(Artifact):
    example_ids: tuple[Reference, ...]
    explanation: str = Field(min_length=1, max_length=2000)
