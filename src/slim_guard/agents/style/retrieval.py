"""Hybrid expression retrieval. Labels and user intent never select examples."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Sequence

from slim_guard.agent_models.embeddings import EmbeddingGateway
from slim_guard.agents.style.contracts import StyleExample

logger = logging.getLogger(__name__)


def _tokens(text: str) -> set[str]:
    value = re.sub(r"\s+", "", text.lower())
    return {value[i : i + 2] for i in range(len(value) - 1)}


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator if denominator else 0


async def similar_examples(
    text: str,
    examples: tuple[StyleExample, ...],
    embedding: EmbeddingGateway | None = None,
) -> tuple[StyleExample, ...]:
    """At most three reliable matches; failure/no match means guide-only, not random shots."""
    if not text.strip() or not examples:
        return ()
    vector: tuple[float, ...] = ()
    model = ""
    if embedding is not None and any(e.embedding for e in examples):
        try:
            async with asyncio.timeout(4):
                batch = await embedding.embed((text[:12000],))
            vector, model = batch.vectors[0], batch.model
        except Exception as exc:
            logger.warning(
                "style_embedding_unavailable", extra={"failure_type": type(exc).__name__}
            )
    query = _tokens(text)
    ranked: list[tuple[float, StyleExample]] = []
    for example in examples:
        source = _tokens(example.original_response)
        lexical = len(query & source) / max(1, len(query | source))
        dense = _cosine(vector, example.embedding) if model == example.embedding_model else 0
        # Conservative thresholds: topic proximity alone must not force a shot.
        if lexical >= 0.2 or dense >= 0.82:
            ranked.append((0.45 * lexical + 0.55 * max(0, dense), example))
    return tuple(e for _, e in sorted(ranked, key=lambda pair: (-pair[0], pair[1].example_id))[:3])
