"""Project user-authored task evidence without promoting model interpretations."""

from collections.abc import Iterator, Mapping
from typing import Any


def task_user_quotes(working: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    tasks = working.get("conversation_tasks", ())
    if not isinstance(tasks, (list, tuple)):
        return
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        evidence = task.get("evidence", ())
        for item in evidence if isinstance(evidence, (list, tuple)) else ():
            if not isinstance(item, Mapping):
                continue
            ref, quote = item.get("source_item_id"), item.get("excerpt")
            if isinstance(ref, str) and ref and isinstance(quote, str) and quote.strip():
                yield ref, quote
