"""Causal attribution supplied by application code, never by model output."""

from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from functools import wraps
from inspect import signature
from typing import Any, ParamSpec, TypeVar


@dataclass(frozen=True)
class UsageContext:
    user_id: str | None = None
    request_id: str | None = None
    turn_id: str | None = None
    thread_id: str | None = None
    agent_role: str | None = None
    invocation_id: str | None = None
    parent_invocation_id: str | None = None
    tool_call_id: str | None = None
    job_id: str | None = None
    component: str = "unattributed"
    category: str = "online"
    execution_kind: str = "service"


_EMPTY = UsageContext()
CURRENT: ContextVar[UsageContext] = ContextVar("model_usage_context", default=_EMPTY)


@contextmanager
def usage_scope(**fields: Any) -> Iterator[UsageContext]:
    context = replace(CURRENT.get(), **fields)
    token = CURRENT.set(context)
    try:
        yield context
    finally:
        CURRENT.reset(token)


P = ParamSpec("P")
R = TypeVar("R")


def usage_operation(
    component: str,
    *,
    source: str | None = None,
    kind: str = "service",
    agent: str | None = None,
    category: str | None = None,
) -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:
    """Bind a trusted invocation/tool/job envelope for the duration of an operation."""

    def decorate(fn: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        sig = signature(fn)

        @wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            arguments = sig.bind(*args, **kwargs).arguments
            envelope = arguments.get(source) if source else None
            selected_component = component
            if (
                component == "agent_generation"
                and CURRENT.get().component == "style_internal_review"
            ):
                selected_component = "style_internal_review"
            fields: dict[str, Any] = {"component": selected_component, "execution_kind": kind}
            for key in ("user_id", "turn_id", "thread_id", "parent_invocation_id", "tool_call_id"):
                value = getattr(envelope, key, None)
                if value is not None:
                    fields[key] = value
            invocation_id = (
                getattr(envelope, "invocation_id", None)
                or getattr(envelope, "agent_invocation_id", None)
                or arguments.get("invocation_id")
            )
            if invocation_id:
                fields["invocation_id"] = invocation_id
                if (
                    invocation_id != CURRENT.get().invocation_id
                    and "parent_invocation_id" not in fields
                ):
                    fields["parent_invocation_id"] = CURRENT.get().invocation_id
            role = getattr(envelope, "agent_role", None) or agent
            if role:
                fields["agent_role"] = str(role)
            if category:
                fields["category"] = category
            if source == "job" and envelope is not None:
                fields["user_id"] = getattr(envelope, "user_id", None)
                fields["turn_id"] = getattr(envelope, "turn_id", None)
                fields.update(
                    job_id=envelope.id,
                    agent_role=None,
                    invocation_id=None,
                    parent_invocation_id=None,
                    tool_call_id=None,
                    request_id=None,
                    thread_id=None,
                )
            with usage_scope(**fields):
                return await fn(*args, **kwargs)

        return wrapped

    return decorate


def context_fields() -> dict[str, Any]:
    return asdict(CURRENT.get())
