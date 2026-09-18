"""Thin lease/heartbeat adapter for the independent trainer."""

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from time import monotonic

from pydantic import ValidationError
from sqlalchemy import or_, select

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.db.models import new_uuid, utc_now
from slim_guard.db.session import Database
from slim_guard.expression_style.review.policy import review_signature
from slim_guard.expression_style.trainer.client import TrainingClient, TrainingGateway
from slim_guard.expression_style.trainer.contracts import BuildBudget
from slim_guard.expression_style.trainer.ports import BuildInterrupted
from slim_guard.expression_style.trainer.service import StyleTrainer
from slim_guard.expression_style.trainer.structured_output import StructuredOutputError

from .job_store import JobStore
from .models import BuildRun

logger = logging.getLogger(__name__)


class StyleWorker:
    def __init__(
        self, database: Database, gateway: ModelGateway, model: str, poll_seconds: float = 2
    ) -> None:
        self.db, self.gateway, self.model, self.poll_seconds = (
            database,
            gateway,
            model,
            poll_seconds,
        )

    async def run_once(self) -> bool:
        async with self.db.session() as s, s.begin():
            row = await s.scalar(
                select(BuildRun)
                .where(
                    or_(
                        BuildRun.status == "queued",
                        (BuildRun.status == "running") & (BuildRun.lease_until < utc_now()),
                    )
                )
                .order_by(BuildRun.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return False
            token = new_uuid()
            row.status, row.worker_token = "running", token
            row.lease_until = utc_now() + timedelta(seconds=90)
            row.heartbeat_at = utc_now()
            row.started_at = row.started_at or utc_now()
            snapshot, run_id = row.snapshot, row.id
            elapsed_before = float(row.usage.get("seconds", 0))
        store = JobStore(self.db, run_id, token)
        started = monotonic()
        try:
            if snapshot["model"] != self.model or snapshot["review_signature"] != review_signature(
                self.model
            ):
                raise ValueError("执行模型或审查版本已变，请新建构建；旧任务不会悄悄切换配置")
            budget = BuildBudget.model_validate(snapshot["budget"])
            remaining = budget.max_seconds - elapsed_before
            if remaining <= 0:
                raise ValueError("构建运行时限已耗尽；恢复不会重置预算")
            client = TrainingClient(TrainingGateway(self.gateway, store, budget), self.model)

            async def heartbeat() -> None:
                while True:
                    await store.heartbeat(elapsed_before + monotonic() - started)
                    await asyncio.sleep(15)

            work = asyncio.create_task(StyleTrainer(client).run(snapshot, run_id))
            pulse = asyncio.create_task(heartbeat())
            try:
                async with asyncio.timeout(remaining):
                    done, _ = await asyncio.wait((work, pulse), return_when=asyncio.FIRST_COMPLETED)
                    if pulse in done:
                        await pulse  # Lost ownership; do not persist stale output.
                    result = await work
                    await store.heartbeat(elapsed_before + monotonic() - started)
                    await store.finish(result)
            finally:
                for task in (work, pulse):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(work, pulse, return_exceptions=True)
        except BuildInterrupted:
            logger.info("style_build_interrupted", extra={"run_id": run_id})
        except Exception as exc:
            with suppress(BuildInterrupted):
                await store.heartbeat(elapsed_before + monotonic() - started)
            if isinstance(exc, StructuredOutputError):
                error = str(exc)
            elif isinstance(exc, ValidationError):
                error = "模型结构化产物校验失败，请查看已保存阶段结果后重试"
            elif type(exc) is ValueError:
                error = str(exc)[:1500]
            elif isinstance(exc, TimeoutError):
                error = "模型请求或构建总时限超时；已保存结果可恢复"
            else:
                error = f"{type(exc).__name__}：构建未完成，请检查模型服务或配置"
            await store.fail(error)
            logger.warning(
                "style_build_failed", extra={"run_id": run_id, "failure_type": type(exc).__name__}
            )
        return True

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                if await self.run_once():
                    continue
            except Exception:
                logger.exception("style_worker_failed")
            try:
                await asyncio.wait_for(stop.wait(), self.poll_seconds)
            except TimeoutError:
                pass
