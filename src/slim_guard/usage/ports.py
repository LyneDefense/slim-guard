from typing import Any, Protocol


class UsageStore(Protocol):
    async def start(self, values: dict[str, Any]) -> None: ...

    async def finish(self, call_id: str, values: dict[str, Any]) -> None: ...
