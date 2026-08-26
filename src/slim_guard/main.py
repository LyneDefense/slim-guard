from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from slim_guard.api.routes import router
from slim_guard.config import Settings
from slim_guard.db.repositories import MessageRepository
from slim_guard.db.session import Database
from slim_guard.integrations.wecom_kf.client import WeComClient, WeComClientProtocol
from slim_guard.integrations.wecom_kf.crypto import WeComCallbackCrypto
from slim_guard.observability.logging import configure_logging
from slim_guard.services.fixed_reply import FixedReplySyncService


def create_app(
    settings: Settings | None = None,
    *,
    client: WeComClientProtocol | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    owned_client: WeComClient | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal owned_client
        configure_logging(app_settings.log_level)
        database = Database(app_settings.database_url)
        await database.create_schema()

        active_client = client
        if active_client is None and app_settings.wecom_api_is_configured:
            owned_client = WeComClient(
                corp_id=app_settings.wecom_corp_id,
                secret=app_settings.wecom_kf_secret,
                base_url=app_settings.wecom_api_base_url,
                timeout_seconds=app_settings.wecom_http_timeout_seconds,
            )
            active_client = owned_client

        crypto: WeComCallbackCrypto | None = None
        sync_service: FixedReplySyncService | None = None
        if app_settings.wecom_callback_is_configured:
            crypto = WeComCallbackCrypto(
                app_settings.wecom_callback_token,
                app_settings.wecom_callback_aes_key,
                app_settings.wecom_corp_id,
            )
        if app_settings.wecom_is_configured and active_client is not None:
            sync_service = FixedReplySyncService(
                client=active_client,
                repository=MessageRepository(database),
                channel_id="default",
                configured_open_kfid=app_settings.wecom_open_kf_id,
                fixed_reply_text=app_settings.fixed_reply_text,
            )

        app.state.settings = app_settings
        app.state.database = database
        app.state.wecom_crypto = crypto
        app.state.sync_service = sync_service
        try:
            yield
        finally:
            if owned_client is not None:
                await owned_client.close()
            await database.close()

    application = FastAPI(
        title="SlimGuard",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if app_settings.app_env == "production" else "/docs",
        redoc_url=None,
    )
    application.include_router(router)
    return application


app = create_app()
