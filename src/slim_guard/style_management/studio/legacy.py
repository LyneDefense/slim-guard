"""Lossless import of complete human examples; incomplete feedback stays readable."""

from collections.abc import Mapping, Sequence
from typing import Any

from slim_guard.expression_style.persona import DialogueExample, PersonaContent


def legacy_example(row: Mapping[str, Any]) -> tuple[DialogueExample | None, str | None]:
    if row.get("source", "manual") != "manual":
        return None, "旧流程生成的素材仅作历史读取，需人工认可后再添加"
    if not row.get("user_input") or not row.get("desired_response"):
        return None, "旧素材没有完整的用户输入和认可回答，请整理为对话示例"
    try:
        return DialogueExample.model_validate(
            {
                "id": row["id"],
                "messages": [
                    *(row.get("history") or []),
                    {"role": "user", "content": row["user_input"]},
                    {"role": "assistant", "content": row["desired_response"]},
                ],
            }
        ), None
    except ValueError:
        return None, "旧素材的对话顺序、轮数或长度不符合完整对话要求，请手动整理"


def merge_legacy_examples(
    content: PersonaContent, rows: Sequence[Mapping[str, Any]]
) -> PersonaContent:
    examples = list(content.examples)
    positions = {item.id: index for index, item in enumerate(examples)}
    for row in rows:
        example, _ = legacy_example(row)
        if example:
            if example.id in positions:
                examples[positions[example.id]] = example
            elif len(examples) < 64:
                positions[example.id] = len(examples)
                examples.append(example)
    return content.model_copy(update={"examples": tuple(examples)})
