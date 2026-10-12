from __future__ import annotations

from typing import Protocol

from slim_guard.domain.profile.contracts import UserProfileSnapshot


class UserProfileReader(Protocol):
    async def get(self, user_id: str) -> UserProfileSnapshot | None: ...
