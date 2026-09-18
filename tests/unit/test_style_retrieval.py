"""Synthetic expression examples; no provider calls or intent labels."""

import pytest

from slim_guard.agent_models.embeddings import EmbeddingBatch
from slim_guard.agents.style.contracts import StyleExample
from slim_guard.agents.style.retrieval import similar_examples


def example(identifier="one", **updates):
    return StyleExample(
        example_id=identifier,
        style_profile_version="test-v1",
        original_response=updates.pop("original_response", "我叫 SlimGuard，你可以这样称呼我。"),
        text="叫我 SlimGuard 就行。",
        **updates,
    )


class Embeddings:
    def __init__(self, vector=(1.0, 0.0), model="test-embed", fails=False):
        self.vector, self.model, self.fails = vector, model, fails
        self.calls = []

    async def embed(self, texts):
        self.calls.append(texts)
        if self.fails:
            raise RuntimeError("TEST unavailable")
        return EmbeddingBatch(
            vectors=(self.vector,), model=self.model, request_id=None, prompt_tokens=0, latency_ms=0
        )


async def test_no_match_uses_guide_only_and_empty_corpus_skips_provider():
    gateway = Embeddings()
    assert await similar_examples("早餐蒸蛋", (example(),), gateway) == ()
    assert await similar_examples("怎么称呼", (), gateway) == ()
    assert gateway.calls == []


async def test_lexical_cap_and_frozen_version_metadata_never_enter_prompt():
    corpus = tuple(
        example(str(i), embedding=(1.0, 0.0), embedding_model="test-embed") for i in range(5)
    )
    selected = await similar_examples("我叫 SlimGuard，你可以这样称呼我。", corpus)
    assert len(selected) == 3
    assert "embedding" not in selected[0].model_dump()
    assert "embedding_model" not in selected[0].model_dump()


@pytest.mark.parametrize(
    "model,vector,expected",
    [
        ("test-embed", (1.0, 0.0), True),
        ("test-embed", (0.0, 1.0), False),
        ("different-model", (1.0, 0.0), False),
        ("test-embed", (1.0, 0.0, 0.0), False),
    ],
)
async def test_dense_match_requires_model_dimensions_and_threshold(model, vector, expected):
    corpus = (example(embedding=(1.0, 0.0), embedding_model="test-embed"),)
    result = await similar_examples("我是这个应用里的助理。", corpus, Embeddings(vector, model))
    assert bool(result) is expected


async def test_provider_failure_keeps_lexical_path_not_unrelated_examples():
    corpus = (example(embedding=(1.0, 0.0), embedding_model="test-embed"),)
    gateway = Embeddings(fails=True)
    assert await similar_examples(corpus[0].original_response, corpus, gateway) == corpus
    assert await similar_examples("今天称重", corpus, gateway) == ()
