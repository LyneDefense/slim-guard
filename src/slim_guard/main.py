from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from slim_guard.agent.composition import (
    AgentRuntimeDefinition,
    build_agent_manifest,
    build_agent_runtime,
)
from slim_guard.agent.runtime import AgentRuntime
from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agent_models.vision import VisionModelGateway
from slim_guard.agent_models.zhipu import ZhipuModelGateway
from slim_guard.agent_models.zhipu_vision import ZhipuVisionModelGateway
from slim_guard.api.routes import router
from slim_guard.config import Settings
from slim_guard.db.repositories import MessageRepository
from slim_guard.db.session import Database
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.integrations.wecom_kf.client import WeComClient, WeComClientProtocol
from slim_guard.integrations.wecom_kf.crypto import WeComCallbackCrypto
from slim_guard.observability.logging import configure_logging
from slim_guard.services.conversation_state import ConversationStateMachine
from slim_guard.services.fixed_reply import AgentReplySyncService
from slim_guard.services.harness_reply_agent import HarnessReplyAgent
from slim_guard.services.reply_agent import (
    SLIM_GUARD_INSTRUCTIONS,
    SLIM_GUARD_PROMPT_VERSION,
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
    model_gateway: ModelGateway | None = None,
    vision_gateway: VisionModelGateway | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    if app_settings.agent_runtime_mode == "shadow":
        raise ValueError(
            f"AGENT_RUNTIME_MODE={app_settings.agent_runtime_mode!r} is not implemented yet"
        )
    model_parameters = {
        "thinking": {"type": "disabled"},
        "do_sample": False,
        "max_output_tokens": app_settings.zhipu_max_output_tokens,
        "max_reply_chars": app_settings.agent_reply_max_chars,
    }
    runtime_definition = AgentRuntimeDefinition(
        model_provider="zhipu",
        text_model=app_settings.zhipu_text_model,
        vision_model=app_settings.zhipu_vision_model,
        model_parameters=model_parameters,
        code_revision=app_settings.agent_code_revision,
        image_retention_seconds=app_settings.agent_image_retention_seconds,
        vision_max_output_tokens=app_settings.zhipu_max_output_tokens,
    )
    agent_manifest = (
        build_agent_manifest(runtime_definition)
        if app_settings.agent_runtime_mode == "harness"
        else AgentManifest.build(
            model_provider="zhipu",
            text_model=app_settings.zhipu_text_model,
            vision_model=app_settings.zhipu_vision_model,
            model_parameters=model_parameters,
            system_prompt_version=SLIM_GUARD_PROMPT_VERSION,
            system_prompt=SLIM_GUARD_INSTRUCTIONS,
            context_policy_version="legacy-single-turn-v1",
            memory_policy_version="none-v1",
            compaction_policy_version="none-v1",
            safety_policy_version="legacy-prompt-v1",
            code_revision=app_settings.agent_code_revision,
        )
    )
    owned_client: WeComClient | None = None
    owned_reply_agent: ZhipuReplyAgent | None = None
    owned_model_gateway: ZhipuModelGateway | None = None
    owned_vision_gateway: ZhipuVisionModelGateway | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal owned_client, owned_model_gateway, owned_reply_agent, owned_vision_gateway
        configure_logging(app_settings.log_level)
        database = Database(app_settings.database_url)
        await database.create_schema()
        await AgentVersionRepository(database).register(agent_manifest)
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

        active_runtime: AgentRuntime | None = None
        active_reply_agent = reply_agent
        if active_reply_agent is None and app_settings.agent_runtime_mode == "harness":
            active_model = model_gateway
            if active_model is None and app_settings.zhipu_is_configured:
                owned_model_gateway = ZhipuModelGateway(
                    api_key=app_settings.zhipu_api_key,
                    base_url=app_settings.zhipu_base_url,
                    timeout_seconds=app_settings.zhipu_http_timeout_seconds,
                    thinking_enabled=False,
                )
                active_model = owned_model_gateway
            active_vision = vision_gateway
            if active_vision is None and app_settings.zhipu_is_configured:
                owned_vision_gateway = ZhipuVisionModelGateway(
                    api_key=app_settings.zhipu_api_key,
                    base_url=app_settings.zhipu_base_url,
                    timeout_seconds=app_settings.zhipu_http_timeout_seconds,
                )
                active_vision = owned_vision_gateway
            if active_model is not None:
                active_runtime = build_agent_runtime(
                    database=database,
                    model=active_model,
                    vision=active_vision,
                    definition=runtime_definition,
                    manifest=agent_manifest,
                )
                active_reply_agent = HarnessReplyAgent(
                    runtime=active_runtime,
                    max_reply_chars=app_settings.agent_reply_max_chars,
                )
        elif active_reply_agent is None and app_settings.zhipu_is_configured:
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
        app.state.agent_runtime = active_runtime
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
            if owned_model_gateway is not None:
                await owned_model_gateway.close()
            if owned_vision_gateway is not None:
                await owned_vision_gateway.close()
            await database.close()

    application = FastAPI(
        title="SlimGuard",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if app_settings.app_env == "production" else "/docs",
        redoc_url=None,
    )
    application.include_router(router)
    application.state.agent_manifest = agent_manifest
    application.state.agent_runtime = None
    application.state.agent_runtime_mode = app_settings.agent_runtime_mode
    return application


app = create_app()
