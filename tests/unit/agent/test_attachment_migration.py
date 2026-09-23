import json
from datetime import UTC, datetime

from sqlalchemy import inspect, text

from slim_guard.db.session import Database
from slim_guard.domain.assets import ImageAssetRepository
from slim_guard.domain.assets.migration import retain_chat_attachments


async def test_legacy_attachment_upgrade_preserves_bytes_identity_and_indexes(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'legacy-attachments.sqlite3'}")
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
            await connection.execute(text("INSERT INTO users VALUES ('user-1')"))
            await connection.execute(
                text("""CREATE TABLE image_assets (
                id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL REFERENCES users(id),
                content BLOB NOT NULL, content_sha256 VARCHAR(64) NOT NULL,
                mime_type VARCHAR(32) NOT NULL, size_bytes INTEGER NOT NULL,
                channel_id VARCHAR(64), source_message_id VARCHAR(256),
                created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL,
                UNIQUE(channel_id, source_message_id))""")
            )
            await connection.execute(
                text("CREATE INDEX ix_image_asset_expiry ON image_assets(expires_at)")
            )
            await connection.execute(
                text("""INSERT INTO image_assets VALUES (
                'asset-1', 'user-1', :content, :digest, 'image/png', 5,
                'mobile', 'message-1', '2026-09-01', '2026-09-08')"""),
                {"content": b"image", "digest": "a" * 64},
            )
            await connection.execute(
                text("CREATE TABLE agent_threads (id TEXT PRIMARY KEY, user_id TEXT)")
            )
            await connection.execute(
                text("CREATE TABLE agent_turns (id TEXT PRIMARY KEY, thread_id TEXT)")
            )
            await connection.execute(
                text("""CREATE TABLE agent_items (
                id TEXT PRIMARY KEY, turn_id TEXT, payload_json TEXT, item_type TEXT,
                status TEXT, created_at DATETIME, sequence INTEGER)""")
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_threads VALUES ('thread-1', 'user-1'), ('thread-2', 'other')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_turns VALUES ('turn-1', 'thread-1'), ('turn-2', 'thread-2')"
                )
            )
            for index, description in [(1, "图片里是一台体重秤。"), (2, "另一个用户的描述")]:
                payload = json.dumps(
                    {
                        "execution": {
                            "tool_name": "inspect_image",
                            "tool_call_id": f"tool-{index}",
                            "result": {
                                "status": "succeeded",
                                "output": {
                                    "asset_id": "asset-1",
                                    "description": description,
                                },
                            },
                        }
                    }
                )
                await connection.execute(
                    text("""INSERT INTO agent_items VALUES (
                    :id, :turn, :payload, 'tool_result', 'completed', '2026-09-01', :sequence)
                """),
                    {
                        "id": str(index),
                        "turn": f"turn-{index}",
                        "payload": payload,
                        "sequence": index,
                    },
                )
            await retain_chat_attachments(connection)
            await retain_chat_attachments(connection)  # Safe if invoked again during recovery.
            columns = await connection.run_sync(lambda c: inspect(c).get_columns("image_assets"))
            indexes = await connection.run_sync(lambda c: inspect(c).get_indexes("image_assets"))
            assert next(c for c in columns if c["name"] == "expires_at")["nullable"]
            assert {"source_text", "observation_json"} <= {c["name"] for c in columns}
            assert {"ix_image_asset_expiry", "ix_image_asset_user_created"} <= {
                i["name"] for i in indexes
            }
        assets = ImageAssetRepository(database)
        assert await assets.purge_expired(at=datetime(2030, 1, 1, tzinfo=UTC)) == 0
        stored = await assets.get_for_user(
            "asset-1", user_id="user-1", at=datetime(2030, 1, 1, tzinfo=UTC)
        )
        assert stored is not None
        assert stored.content == b"image"
        assert stored.ref.source_message_id == "message-1"
        assert stored.ref.expires_at is None
        annotations = await assets.annotations(user_id="user-1", asset_ids=("asset-1",))
        assert annotations["asset-1"]["annotation"] == {
            "author": "vision_model",
            "description": "图片里是一台体重秤。",
            "turn_id": "turn-1",
            "tool_call_id": "tool-1",
        }
        assert (
            await assets.get_for_user(
                "asset-1", user_id="other", at=datetime(2030, 1, 1, tzinfo=UTC)
            )
            is None
        )
    finally:
        await database.close()
