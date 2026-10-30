"""Mobile feature composition and owned worker lifecycle."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from slim_guard.agent.runtime import AgentRuntime
from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.app.daily_insights import build_daily_insights
from slim_guard.config import Settings
from slim_guard.db.session import Database
from slim_guard.features.daily_insights.service import DailyInsightsService
from slim_guard.memory.engine import MemoryEngine
from slim_guard.mobile.auth import MobileAuthService, NullMobileOtpSender, WebhookMobileOtpSender
from slim_guard.mobile.platform import MobilePlatformService
from slim_guard.mobile.service import MobileApplicationService
from slim_guard.observability.tracing import InteractionTraceRepository


@dataclass
class MobileComponents:
    auth: MobileAuthService | None = None
    service: MobileApplicationService | None = None
    platform: MobilePlatformService | None = None
    insights: DailyInsightsService | None = None
    stop: asyncio.Event | None = None
    task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.insights is not None and self.insights.generator is not None:
            self.stop = asyncio.Event()
            self.task = asyncio.create_task(
                self.insights.run_forever(self.stop), name="daily-insights"
            )

    async def close(self) -> None:
        if self.stop is not None:
            self.stop.set()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


async def build_mobile_components(
    settings: Settings,
    database: Database,
    runtime: AgentRuntime | None,
    traces: InteractionTraceRepository,
    memory_engine: MemoryEngine | None,
    model: ModelGateway | None,
) -> MobileComponents:
    if not settings.mobile_is_configured:
        return MobileComponents()
    sender = (
        WebhookMobileOtpSender(
            url=settings.mobile_sms_webhook_url,
            token=settings.mobile_sms_webhook_token,
            timeout_seconds=settings.wecom_http_timeout_seconds,
        )
        if settings.mobile_sms_webhook_url
        else NullMobileOtpSender()
    )
    auth = MobileAuthService(
        database=database,
        secret=settings.mobile_auth_secret,
        sender=sender,
        access_ttl=timedelta(minutes=settings.mobile_access_token_ttl_minutes),
        refresh_ttl=timedelta(days=settings.mobile_refresh_token_ttl_days),
        otp_ttl=timedelta(seconds=settings.mobile_otp_ttl_seconds),
        resend_after=timedelta(seconds=settings.mobile_otp_resend_seconds),
        hourly_limit=settings.mobile_otp_hourly_limit,
        test_accounts_enabled=settings.mobile_test_accounts_enabled,
        test_account_password=settings.mobile_test_account_password,
    )
    await auth.ensure_test_accounts(now=datetime.now(UTC))
    service = MobileApplicationService(
        database=database,
        runtime=runtime,
        traces=traces,
        max_image_bytes=settings.wecom_media_max_bytes,
    )
    platform = MobilePlatformService(
        database=database,
        secret=settings.mobile_auth_secret,
        binding_ttl=timedelta(minutes=settings.mobile_wecom_binding_ttl_minutes),
        memory_engine=memory_engine,
    )
    return MobileComponents(
        auth=auth,
        service=service,
        platform=platform,
        insights=build_daily_insights(database, service.records, model, settings.zhipu_text_model),
    )
