"""Runtime contracts for freezing one governed nutrition corpus release."""

from __future__ import annotations

from pydantic import Field

from slim_guard.runtime.contracts import ContractModel


class NutritionRuntimeSnapshot(ContractModel):
    corpus_release_id: str = Field(min_length=1, max_length=128)
    corpus_release_version: str = Field(min_length=1, max_length=128)
    corpus_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieval_profile_id: str = Field(min_length=1, max_length=128)
    embedding_profile_id: str = Field(min_length=1, max_length=128)
    lexical_profile_id: str = Field(min_length=1, max_length=128)
    chunker_profile_id: str = Field(min_length=1, max_length=128)


__all__ = ["NutritionRuntimeSnapshot"]
