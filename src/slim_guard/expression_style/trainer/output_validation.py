"""Input-bound output contracts checked before accepting or caching model results."""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class OutputValidation(Generic[T]):
    version: str
    references: dict[str, str]
    check: Callable[[T], list[dict[str, Any]]]

    def descriptor(self) -> dict[str, Any]:
        return {"version": self.version, "references": self.references}


def reference_coverage(
    expected: Sequence[str], actual: Sequence[str], *, field: str
) -> list[dict[str, Any]]:
    """Exact identity/coverage checks; never guess which source an unknown ID meant."""
    counts = Counter(actual)
    errors: list[dict[str, Any]] = []
    missing = [identifier for identifier in expected if identifier not in counts]
    if missing:
        errors.append(
            {
                "path": "items",
                "type": "missing_references",
                "message": "缺少条目：" + ", ".join(missing),
            }
        )
    for index, identifier in enumerate(actual):
        if identifier not in expected:
            errors.append(
                {
                    "path": f"items.{index}.{field}",
                    "type": "unknown_reference",
                    "message": "编号不在本批输入中："
                    + identifier
                    + "；允许值："
                    + ", ".join(expected),
                }
            )
        elif counts[identifier] > 1:
            errors.append(
                {
                    "path": f"items.{index}.{field}",
                    "type": "duplicate_reference",
                    "message": "编号重复：" + identifier + "；每个输入编号必须恰好返回一次",
                }
            )
    return errors
