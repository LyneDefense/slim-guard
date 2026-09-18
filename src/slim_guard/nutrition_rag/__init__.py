"""Production nutrition knowledge ingestion, retrieval, and release management."""

from slim_guard.nutrition_rag.contracts import NutritionRuntimeSnapshot
from slim_guard.nutrition_rag.gateways import (
    RerankGateway,
    RerankResult,
    ZhipuRerankGateway,
)
from slim_guard.nutrition_rag.profiles import (
    DEFAULT_EMBEDDING_PROFILE_ID,
    DEFAULT_EMBEDDING_PROFILE_KEY,
    DEFAULT_LEXICAL_PROFILE_ID,
    DEFAULT_LEXICAL_PROFILE_KEY,
    DEFAULT_RETRIEVAL_PROFILE_ID,
    DEFAULT_RETRIEVAL_PROFILE_KEY,
    NUTRITION_LEXICON_SHA256,
)

__all__ = [
    "DEFAULT_EMBEDDING_PROFILE_ID",
    "DEFAULT_EMBEDDING_PROFILE_KEY",
    "DEFAULT_LEXICAL_PROFILE_ID",
    "DEFAULT_LEXICAL_PROFILE_KEY",
    "DEFAULT_RETRIEVAL_PROFILE_ID",
    "DEFAULT_RETRIEVAL_PROFILE_KEY",
    "NUTRITION_LEXICON_SHA256",
    "NutritionRuntimeSnapshot",
    "RerankGateway",
    "RerankResult",
    "ZhipuRerankGateway",
]
