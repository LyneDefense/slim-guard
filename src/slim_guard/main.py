from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request, Response

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
from slim_guard.api.admin_routes import router as admin_router
from slim_guard.api.mobile_routes import router as mobile_router
from slim_guard.api.routes import router
from slim_guard.config import Settings
from slim_guard.db.repositories import MessageRepository
from slim_guard.db.session import Database
from slim_guard.domain.assets.repository import ImageAssetRepository
from slim_guard.domain.routine.jobs import RoutineJobPlanner, RoutineJobRepository
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.domain.routine.status import DailyCheckinStatusRepository
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.integrations.wecom_kf.client import WeComClient, WeComClientProtocol
from slim_guard.integrations.wecom_kf.crypto import WeComCallbackCrypto
from slim_guard.memory.engine import Mem0HttpMemoryEngine, MemoryEngine
from slim_guard.memory.index_sync import MemoryIndexSyncRepository, MemoryIndexSyncService
from slim_guard.memory.lifecycle import MemoryLifecycleRepository
from slim_guard.mobile.auth import (
    MobileAuthService,
    NullMobileOtpSender,
    WebhookMobileOtpSender,
)
from slim_guard.mobile.platform import MobilePlatformService
from slim_guard.mobile.service import MobileApplicationService
from slim_guard.observability.logging import configure_logging
from slim_guard.observability.tracing import InteractionTraceRepository
from slim_guard.services.conversation_state import ConversationStateMachine
from slim_guard.services.fixed_reply import AgentReplySyncService
from slim_guard.services.harness_reply_agent import HarnessReplyAgent
from slim_guard.services.maintenance import AssetMaintenanceService
from slim_guard.services.memory_maintenance import MemoryMaintenanceService
from slim_guard.services.proactive_delivery import (
    ProactiveDeliveryPolicy,
    ProactiveDeliveryRepository,
)
from slim_guard.services.reply_agent import (
    SLIM_GUARD_INSTRUCTIONS,
    SLIM_GUARD_PROMPT_VERSION,
    ReplyAgentProtocol,
    StaticReplyAgent,
    ZhipuReplyAgent,
)
from slim_guard.services.routine_scheduler import RoutineSchedulerService

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    client: WeComClientProtocol | None = None,
    reply_agent: ReplyAgentProtocol | None = None,
    model_gateway: ModelGateway | None = None,
    vision_gateway: VisionModelGateway | None = None,
    memory_engine: MemoryEngine | None = None,
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
        memory_preload_max_facts=app_settings.memory_preload_max_facts,
        memory_health_review_days=app_settings.memory_health_review_days,
        memory_recent_turn_count=app_settings.memory_recent_turn_count,
        memory_recent_dialogue_max_chars=(
            app_settings.memory_recent_dialogue_max_chars
        ),
        memory_recent_image_count=app_settings.memory_recent_image_count,
        memory_handoff_ttl_days=app_settings.memory_handoff_ttl_days,
        memory_ingestion_history_count=app_settings.memory_ingestion_history_count,
        memory_ingestion_history_max_chars=(
            app_settings.memory_ingestion_history_max_chars
        ),
        memory_recall_search_limit=app_settings.memory_recall_search_limit,
        memory_recall_max_selected=app_settings.memory_recall_max_selected,
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
    owned_memory_engine: Mem0HttpMemoryEngine | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal owned_client, owned_memory_engine, owned_model_gateway
        nonlocal owned_reply_agent, owned_vision_gateway
        configure_logging(app_settings.log_level)
        database = Database(app_settings.database_url)
        await database.create_schema()
        await AgentVersionRepository(database).register(agent_manifest)
        repository = MessageRepository(database)
        traces = InteractionTraceRepository(database)
        await repository.backfill_users_from_messages()
        backfilled_trace_count = await traces.backfill_existing()
        if backfilled_trace_count:
            logger.info(
                "interaction_traces_backfilled",
                extra={"trace_count": backfilled_trace_count},
            )

        active_memory_engine = memory_engine
        if active_memory_engine is None and app_settings.memory_semantic_enabled:
            owned_memory_engine = Mem0HttpMemoryEngine(
                base_url=app_settings.mem0_base_url,
                api_key=app_settings.mem0_api_key,
                namespace=app_settings.mem0_namespace,
                timeout_seconds=app_settings.mem0_http_timeout_seconds,
            )
            active_memory_engine = owned_memory_engine

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
                    memory_ingestion_model=(
                        active_model if app_settings.memory_ingestion_enabled else None
                    ),
                    memory_recall_model=(
                        active_model if app_settings.memory_recall_enabled else None
                    ),
                    memory_engine=active_memory_engine,
                    vision=active_vision,
                    definition=runtime_definition,
                    manifest=agent_manifest,
                )
                active_reply_agent = HarnessReplyAgent(
                    runtime=active_runtime,
                    max_reply_chars=app_settings.agent_reply_max_chars,
                    traces=traces,
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

        mobile_auth: MobileAuthService | None = None
        mobile_service: MobileApplicationService | None = None
        mobile_platform: MobilePlatformService | None = None
        if app_settings.mobile_is_configured:
            otp_sender = (
                WebhookMobileOtpSender(
                    url=app_settings.mobile_sms_webhook_url,
                    token=app_settings.mobile_sms_webhook_token,
                    timeout_seconds=app_settings.wecom_http_timeout_seconds,
                )
                if app_settings.mobile_sms_webhook_url
                else NullMobileOtpSender()
            )
            mobile_auth = MobileAuthService(
                database=database,
                secret=app_settings.mobile_auth_secret,
                sender=otp_sender,
                access_ttl=timedelta(
                    minutes=app_settings.mobile_access_token_ttl_minutes
                ),
                refresh_ttl=timedelta(
                    days=app_settings.mobile_refresh_token_ttl_days
                ),
                otp_ttl=timedelta(seconds=app_settings.mobile_otp_ttl_seconds),
                resend_after=timedelta(
                    seconds=app_settings.mobile_otp_resend_seconds
                ),
                hourly_limit=app_settings.mobile_otp_hourly_limit,
                test_accounts_enabled=app_settings.mobile_test_accounts_enabled,
                test_account_password=app_settings.mobile_test_account_password,
            )
            await mobile_auth.ensure_test_accounts(now=datetime.now(UTC))
            mobile_service = MobileApplicationService(
                database=database,
                runtime=active_runtime,
                traces=traces,
                max_image_bytes=app_settings.wecom_media_max_bytes,
            )
            mobile_platform = MobilePlatformService(
                database=database,
                secret=app_settings.mobile_auth_secret,
                binding_ttl=timedelta(
                    minutes=app_settings.mobile_wecom_binding_ttl_minutes
                ),
                memory_engine=active_memory_engine,
            )

        crypto: WeComCallbackCrypto | None = None
        sync_service: AgentReplySyncService | None = None
        watchdog_stop: asyncio.Event | None = None
        watchdog_task: asyncio.Task[None] | None = None
        routine_stop: asyncio.Event | None = None
        routine_task: asyncio.Task[None] | None = None
        maintenance_stop: asyncio.Event | None = None
        maintenance_task: asyncio.Task[None] | None = None
        memory_maintenance_stop: asyncio.Event | None = None
        memory_maintenance_task: asyncio.Task[None] | None = None
        outbox_stop: asyncio.Event | None = None
        outbox_task: asyncio.Task[None] | None = None
        memory_index_stop: asyncio.Event | None = None
        memory_index_task: asyncio.Task[None] | None = None
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
                outbox_recovery_interval_seconds=(
                    app_settings.wecom_outbox_recovery_interval_seconds
                ),
                outbox_send_stale_seconds=app_settings.wecom_outbox_send_stale_seconds,
                traces=traces,
                mobile_platform=mobile_platform,
            )
            watchdog_stop = asyncio.Event()
            watchdog_task = asyncio.create_task(
                state_machine.run_watchdog(watchdog_stop),
                name="wecom-human-timeout-watchdog",
            )
            outbox_stop = asyncio.Event()
            outbox_task = asyncio.create_task(
                sync_service.run_outbox_recovery(outbox_stop),
                name="wecom-outbox-recovery",
            )
            if app_settings.routine_scheduler_enabled and active_runtime is not None:
                preferences = RoutinePreferenceRepository(database)
                jobs = RoutineJobRepository(database)
                deliveries = ProactiveDeliveryRepository(database)
                routine_scheduler = RoutineSchedulerService(
                    planner=RoutineJobPlanner(preferences=preferences, jobs=jobs),
                    jobs=jobs,
                    preferences=preferences,
                    checkins=DailyCheckinStatusRepository(database),
                    policy=ProactiveDeliveryPolicy(
                        repository=deliveries,
                        open_kfid=app_settings.wecom_open_kf_id,
                        active_window=timedelta(
                            hours=app_settings.wecom_proactive_active_window_hours
                        ),
                        max_messages_per_window=(
                            app_settings.wecom_proactive_max_messages
                        ),
                    ),
                    deliveries=deliveries,
                    runtime=active_runtime,
                    client=active_client,
                    conversation_control=state_machine,
                    interval_seconds=app_settings.routine_scheduler_interval_seconds,
                    job_lease=timedelta(
                        seconds=app_settings.routine_job_lease_seconds
                    ),
                    send_retry_after=timedelta(
                        seconds=app_settings.routine_send_retry_seconds
                    ),
                    max_lateness=timedelta(
                        seconds=app_settings.routine_max_lateness_seconds
                    ),
                    agent_timeout=timedelta(
                        seconds=app_settings.routine_agent_timeout_seconds
                    ),
                    max_attempts=app_settings.routine_max_attempts,
                    max_message_chars=app_settings.agent_reply_max_chars,
                    traces=traces,
                )
                routine_stop = asyncio.Event()
                routine_task = asyncio.create_task(
                    routine_scheduler.run_forever(routine_stop),
                    name="slim-guard-routine-scheduler",
                )
        if active_runtime is not None:
            maintenance = AssetMaintenanceService(
                assets=ImageAssetRepository(database),
                interval_seconds=app_settings.asset_maintenance_interval_seconds,
            )
            maintenance_stop = asyncio.Event()
            maintenance_task = asyncio.create_task(
                maintenance.run_forever(maintenance_stop),
                name="slim-guard-asset-maintenance",
            )
        memory_maintenance = MemoryMaintenanceService(
            lifecycle=MemoryLifecycleRepository(database),
            transcript_retention=timedelta(
                days=app_settings.agent_transcript_body_retention_days
            ),
            revoked_value_retention=timedelta(
                days=app_settings.memory_revoked_value_retention_days
            ),
            interval_seconds=app_settings.memory_maintenance_interval_seconds,
        )
        memory_maintenance_stop = asyncio.Event()
        memory_maintenance_task = asyncio.create_task(
            memory_maintenance.run_forever(memory_maintenance_stop),
            name="slim-guard-memory-maintenance",
        )
        if active_memory_engine is not None:
            memory_index_repository = MemoryIndexSyncRepository(database)
            backfilled_memory_count = await memory_index_repository.enqueue_active_backfill()
            if backfilled_memory_count:
                logger.info(
                    "memory_index_backfill_queued",
                    extra={"memory_count": backfilled_memory_count},
                )
            memory_index_sync = MemoryIndexSyncService(
                repository=memory_index_repository,
                engine=active_memory_engine,
                interval_seconds=app_settings.memory_index_sync_interval_seconds,
                batch_size=app_settings.memory_index_sync_batch_size,
                max_attempts=app_settings.memory_index_sync_max_attempts,
            )
            memory_index_stop = asyncio.Event()
            memory_index_task = asyncio.create_task(
                memory_index_sync.run_forever(memory_index_stop),
                name="slim-guard-memory-index-sync",
            )

        app.state.settings = app_settings
        app.state.database = database
        app.state.traces = traces
        app.state.agent_runtime = active_runtime
        app.state.memory_engine = active_memory_engine
        app.state.mobile_auth = mobile_auth
        app.state.mobile_service = mobile_service
        app.state.mobile_platform = mobile_platform
        app.state.wecom_crypto = crypto
        app.state.sync_service = sync_service
        try:
            yield
        finally:
            if watchdog_stop is not None:
                watchdog_stop.set()
            if routine_stop is not None:
                routine_stop.set()
            if maintenance_stop is not None:
                maintenance_stop.set()
            if memory_maintenance_stop is not None:
                memory_maintenance_stop.set()
            if outbox_stop is not None:
                outbox_stop.set()
            if memory_index_stop is not None:
                memory_index_stop.set()
            if watchdog_task is not None:
                await watchdog_task
            if routine_task is not None:
                await routine_task
            if maintenance_task is not None:
                await maintenance_task
            if memory_maintenance_task is not None:
                await memory_maintenance_task
            if outbox_task is not None:
                await outbox_task
            if memory_index_task is not None:
                await memory_index_task
            if owned_client is not None:
                await owned_client.close()
            if owned_reply_agent is not None:
                await owned_reply_agent.close()
            if owned_model_gateway is not None:
                await owned_model_gateway.close()
            if owned_vision_gateway is not None:
                await owned_vision_gateway.close()
            if owned_memory_engine is not None:
                await owned_memory_engine.close()
            await database.close()

    application = FastAPI(
        title="SlimGuard",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if app_settings.app_env == "production" else "/docs",
        redoc_url=None,
    )

    @application.middleware("http")
    async def disable_sensitive_response_cache(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        if request.url.path.startswith(("/api/admin", "/api/mobile")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    application.include_router(router)
    application.include_router(admin_router)
    application.include_router(mobile_router)
    application.state.agent_manifest = agent_manifest
    application.state.agent_runtime = None
    application.state.agent_runtime_mode = app_settings.agent_runtime_mode
    application.state.mobile_auth = None
    application.state.mobile_service = None
    application.state.mobile_platform = None
    return application


app = create_app()
