from __future__ import annotations

import hashlib

DEFAULT_EMBEDDING_PROFILE_ID = "00000000-0000-4000-8000-000000000101"
DEFAULT_LEXICAL_PROFILE_ID = "00000000-0000-4000-8000-000000000102"
LEGACY_RETRIEVAL_PROFILE_ID = "00000000-0000-4000-8000-000000000103"
ANSWERABILITY_V1_RETRIEVAL_PROFILE_ID = "00000000-0000-4000-8000-000000000104"
DEFAULT_RETRIEVAL_PROFILE_ID = "00000000-0000-4000-8000-000000000105"

DEFAULT_EMBEDDING_PROFILE_KEY = "zhipu-embedding-3-1024-v1"
DEFAULT_LEXICAL_PROFILE_KEY = "jieba-nutrition-zh-cn-v1"
LEGACY_RETRIEVAL_PROFILE_KEY = "nutrition-hybrid-rag-v1"
ANSWERABILITY_V1_RETRIEVAL_PROFILE_KEY = "nutrition-hybrid-rag-answerability-v2"
DEFAULT_RETRIEVAL_PROFILE_KEY = "nutrition-hybrid-rag-answerability-v3"

CHUNKER_PROFILE_KEY = "nutrition-parent-child-zh-cn-v1"
LEGACY_QUERY_PLAN_VERSION = "nutrition-retrieval-query-v2"
ANSWERABILITY_V1_QUERY_PLAN_VERSION = "nutrition-retrieval-query-v3-answerability"
QUERY_PLAN_VERSION = "nutrition-retrieval-query-v4-answerability"
ANSWERABILITY_MODE_V1 = "direct_support_v1"
ANSWERABILITY_MODE = "direct_support_v2"

# This list is intentionally frozen with the lexical profile. Changing it creates
# a new profile and requires rebuilding lexical terms for candidate releases.
NUTRITION_LEXICON_V1: tuple[str, ...] = (
    "BMI",
    "低血糖",
    "体重管理",
    "全谷物",
    "减脂",
    "含糖饮料",
    "坚果",
    "复合调味料",
    "少油",
    "少盐",
    "少糖",
    "植物油",
    "水果",
    "烹饪方式",
    "膳食指南",
    "膳食纤维",
    "营养标签",
    "蛋白质",
    "蔬菜",
    "豆制品",
    "超重",
    "食物多样",
    "食物过敏",
    "餐次搭配",
    "高盐",
    "高油",
    "高糖",
)

NUTRITION_LEXICON_SHA256 = hashlib.sha256(
    "\n".join(NUTRITION_LEXICON_V1).encode("utf-8")
).hexdigest()

__all__ = [
    "ANSWERABILITY_MODE",
    "ANSWERABILITY_MODE_V1",
    "ANSWERABILITY_V1_QUERY_PLAN_VERSION",
    "ANSWERABILITY_V1_RETRIEVAL_PROFILE_ID",
    "ANSWERABILITY_V1_RETRIEVAL_PROFILE_KEY",
    "CHUNKER_PROFILE_KEY",
    "DEFAULT_EMBEDDING_PROFILE_ID",
    "DEFAULT_EMBEDDING_PROFILE_KEY",
    "DEFAULT_LEXICAL_PROFILE_ID",
    "DEFAULT_LEXICAL_PROFILE_KEY",
    "DEFAULT_RETRIEVAL_PROFILE_ID",
    "DEFAULT_RETRIEVAL_PROFILE_KEY",
    "LEGACY_QUERY_PLAN_VERSION",
    "LEGACY_RETRIEVAL_PROFILE_ID",
    "LEGACY_RETRIEVAL_PROFILE_KEY",
    "NUTRITION_LEXICON_SHA256",
    "NUTRITION_LEXICON_V1",
    "QUERY_PLAN_VERSION",
]
