from __future__ import annotations

import asyncio
import sys

from slim_guard.config import Settings
from slim_guard.integrations.wecom_kf.client import WeComClient
from slim_guard.integrations.wecom_kf.errors import WeComError


async def _run() -> int:
    settings = Settings()
    if not settings.wecom_corp_id or not settings.wecom_kf_secret:
        print("请先在 .env 中填写 WECOM_CORP_ID 和 WECOM_KF_SECRET。", file=sys.stderr)
        return 2
    client = WeComClient(
        corp_id=settings.wecom_corp_id,
        secret=settings.wecom_kf_secret,
        base_url=settings.wecom_api_base_url,
        timeout_seconds=settings.wecom_http_timeout_seconds,
    )
    try:
        accounts = await client.list_accounts()
    except WeComError as exc:
        print(f"读取客服账号失败：{exc}", file=sys.stderr)
        return 1
    finally:
        await client.close()
    if not accounts:
        print("没有找到客服账号，请先在微信客服后台创建一个客服账号。")
        return 0
    print("客服账号：")
    for account in accounts:
        print(f"- {account.name}: {account.open_kfid}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
