from __future__ import annotations

import asyncio
import logging
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
from slim_guard.services.conversation_state import ConversationStateMachine
from slim_guard.services.fixed_reply import AgentReplySyncService
from slim_guard.services.reply_agent import (
    ReplyAgentProtocol,
    StaticReplyAgent,
    ZhipuReplyAgent,
)

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    client: WeComClientProtocol | None = None,
    reply_agent: ReplyAgentProtocol | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    owned_client: WeComClient | None = None
    owned_reply_agent: ZhipuReplyAgent | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal owned_client, owned_reply_agent
        configure_logging(app_settings.log_level)
        database = Database(app_settings.database_url)
        await database.create_schema()
        repository = MessageRepository(database)
        await repository.backfill_users_from_messages()

        active_client = client
        if active_client is None and app_settings.wecom_api_is_configured:
            owned_client = WeComClient(
                corp_id=app_settings.wecom_corp_id,
                secret=app_settings.wecom_kf_secret,
                base_url=app_settings.wecom_api_base_url,
                timeout_seconds=app_settings.wecom_http_timeout_seconds,
            )
            active_client = owned_client

        active_reply_agent = reply_agent
        if active_reply_agent is None and app_settings.zhipu_is_configured:
            owned_reply_agent = ZhipuReplyAgent(
                api_key=app_settings.zhipu_api_key,
                text_model=app_settings.zhipu_text_model,
                vision_model=app_settings.zhipu_vision_model,
                base_url=app_settings.zhipu_base_url,
                timeout_seconds=app_settings.zhipu_http_timeout_seconds,
                max_output_tokens=app_settings.zhipu_max_output_tokens,
                max_reply_chars=app_settings.agent_reply_max_chars,
            )
            active_reply_agent = owned_reply_agent
        if active_reply_agent is None:
            logger.warning("zhipu_not_configured_using_fallback_reply")
            active_reply_agent = StaticReplyAgent(app_settings.agent_fallback_reply_text)

        crypto: WeComCallbackCrypto | None = None
        sync_service: AgentReplySyncService | None = None
        watchdog_stop: asyncio.Event | None = None
        watchdog_task: asyncio.Task[None] | None = None
        if app_settings.wecom_callback_is_configured:
            crypto = WeComCallbackCrypto(
                app_settings.wecom_callback_token,
                app_settings.wecom_callback_aes_key,
                app_settings.wecom_corp_id,
            )
        if app_settings.wecom_is_configured and active_client is not None:
            state_machine = ConversationStateMachine(
                client=active_client,
                repository=repository,
                human_idle_timeout_seconds=app_settings.wecom_human_idle_timeout_seconds,
                watchdog_interval_seconds=(app_settings.wecom_session_watchdog_interval_seconds),
                human_timeout_message=app_settings.wecom_human_timeout_message,
            )
            sync_service = AgentReplySyncService(
                client=active_client,
                repository=repository,
                channel_id="default",
                configured_open_kfid=app_settings.wecom_open_kf_id,
                reply_agent=active_reply_agent,
                fallback_reply_text=app_settings.agent_fallback_reply_text,
                state_machine=state_machine,
                reply_delivery_mode=app_settings.reply_delivery_mode,
                profile_refresh_seconds=(app_settings.wecom_customer_profile_refresh_seconds),
                media_max_bytes=app_settings.wecom_media_max_bytes,
            )
            watchdog_stop = asyncio.Event()
            watchdog_task = asyncio.create_task(
                state_machine.run_watchdog(watchdog_stop),
                name="wecom-human-timeout-watchdog",
            )

        app.state.settings = app_settings
        app.state.database = database
        app.state.wecom_crypto = crypto
        app.state.sync_service = sync_service
        try:
            yield
        finally:
            if watchdog_stop is not None:
                watchdog_stop.set()
            if watchdog_task is not None:
                await watchdog_task
            if owned_client is not None:
                await owned_client.close()
            if owned_reply_agent is not None:
                await owned_reply_agent.close()
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
