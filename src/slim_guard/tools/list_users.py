from __future__ import annotations

import asyncio
import hashlib

from slim_guard.config import Settings
from slim_guard.db.repositories import MessageRepository
from slim_guard.db.session import Database


async def _run() -> int:
    settings = Settings()
    database = Database(settings.database_url)
    try:
        await database.create_schema()
        users = await MessageRepository(database).list_users(limit=1000)
    finally:
        await database.close()
    if not users:
        print("还没有用户。普通微信客户发送第一条消息后会自动创建用户记录。")
        return 0
    print(f"SlimGuard 用户（共 {len(users)} 个）：")
    for user in users:
        external_ref = hashlib.sha256(user.external_userid.encode()).hexdigest()[:12]
        nickname = user.nickname or "（未获取昵称）"
        unionid_status = "有" if user.unionid else "无"
        print(
            f"- {nickname} | user_id={user.id} | external_ref={external_ref} | "
            f"profile={user.profile_status} | unionid={unionid_status} | "
            f"last_seen={user.last_seen_at.isoformat()}"
        )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
