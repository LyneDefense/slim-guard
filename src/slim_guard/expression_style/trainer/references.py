"""Reversible model-facing short references; durable identities stay outside prompts."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class BatchReferences:
    references: dict[str, str]

    @classmethod
    def for_ids(cls, identifiers: Sequence[str]) -> "BatchReferences":
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("输入来源 ID 重复，请检查构建快照；未向模型发送该批输入")
        return cls({f"m{i + 1}": identifier for i, identifier in enumerate(identifiers)})

    def resolve(self, reference: str) -> str:
        return self.references[reference]
