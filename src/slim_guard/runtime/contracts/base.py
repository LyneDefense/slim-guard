"""Base validation rules for data crossing a runtime boundary."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Reject undeclared fields and keep boundary data immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


ContractT = TypeVar("ContractT", bound=ContractModel)


def validate_contract(model: type[ContractT], payload: dict[str, Any]) -> ContractT:
    """Validate an untrusted payload against one explicit boundary contract."""

    return model.model_validate(payload)


__all__ = ["ContractModel", "validate_contract"]
