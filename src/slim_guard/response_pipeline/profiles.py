"""Resolve one immutable Style Profile snapshot for a response Turn."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from slim_guard.agent_models.embeddings import EmbeddingGateway
from slim_guard.agents.style import SLIMGUARD_DEFAULT_V1, StyleProfileRepository
from slim_guard.agents.style.contracts import StyleProfileSnapshot
from slim_guard.agents.style.retrieval import similar_examples

logger = logging.getLogger(__name__)


class ActiveStyleVersionResolver(Protocol):
    async def resolve(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ResolvedStyleProfile:
    snapshot: StyleProfileSnapshot
    requested_version: str
    source: str
    fallback_reason: str | None = None


class StyleProfileResolver:
    def __init__(
        self,
        *,
        profiles: StyleProfileRepository | None,
        active_version: ActiveStyleVersionResolver | None,
        default_version: str,
        embedding: EmbeddingGateway | None = None,
    ) -> None:
        self._profiles = profiles
        self._active_version = active_version
        self._default_version = default_version
        self._embedding = embedding

    async def resolve(self, neutral_text: str = "") -> ResolvedStyleProfile:
        requested = self._default_version
        source = "default"
        if self._active_version is not None:
            try:
                requested = await self._active_version.resolve()
                source = "runtime_active"
            except Exception as error:
                logger.warning(
                    "active_style_version_resolution_failed",
                    extra={"failure_type": type(error).__name__},
                )
        failure = "profile_not_published"
        try:
            if self._profiles is not None:
                snapshot = await self._profiles.get_runtime_snapshot(requested)
                if snapshot is not None and snapshot.profile.version == requested:
                    examples = await similar_examples(
                        neutral_text, snapshot.examples, self._embedding
                    )
                    snapshot = snapshot.model_copy(update={"examples": examples})
                    return ResolvedStyleProfile(snapshot, requested, source)
                if snapshot is not None:
                    failure = "profile_version_mismatch"
            elif requested == SLIMGUARD_DEFAULT_V1.version:
                return ResolvedStyleProfile(
                    StyleProfileSnapshot(profile=SLIMGUARD_DEFAULT_V1),
                    requested,
                    source,
                )
        except Exception as error:
            failure = "profile_resolution_failed"
            logger.warning(
                "style_profile_resolution_failed",
                extra={"failure_type": type(error).__name__},
            )
        return ResolvedStyleProfile(
            StyleProfileSnapshot(profile=SLIMGUARD_DEFAULT_V1),
            requested,
            "fallback",
            failure,
        )


__all__ = [
    "ActiveStyleVersionResolver",
    "ResolvedStyleProfile",
    "StyleProfileResolver",
]
