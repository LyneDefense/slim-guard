"""Lease-fenced implementation of the trainer's persistence port."""

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from slim_guard.db.models import utc_now
from slim_guard.db.session import Database
from slim_guard.expression_style.package import StylePackage
from slim_guard.expression_style.trainer.ports import BuildInterrupted

from .models import BuildRun, Example, ReviewCase, Version


class JobStore:
    def __init__(self, db: Database, run_id: str, token: str) -> None:
        self.db, self.run_id, self.token = db, run_id, token

    def owned(self) -> Any:
        return (
            select(BuildRun)
            .where(
                BuildRun.id == self.run_id,
                BuildRun.worker_token == self.token,
                BuildRun.status == "running",
            )
            .with_for_update()
        )

    async def load(self, key: str) -> Any:
        async with self.db.session() as s:
            row = await s.scalar(self.owned())
            if row is None:
                raise BuildInterrupted("任务已取消或被接管")
            return row.checkpoints.get(key)

    async def save(self, key: str, value: Any, *, stage: str, message: str) -> None:
        async with self.db.session() as s, s.begin():
            row = await s.scalar(self.owned())
            if row is None:
                raise BuildInterrupted("任务已取消或被接管")
            row.checkpoints = {**row.checkpoints, key: value}
            row.progress_at = utc_now()
            self.add_event(row, stage, message, state="running", artifact_key=key)

    @staticmethod
    def add_event(row: BuildRun, stage: str, message: str, **details: Any) -> None:
        now = utc_now()
        row.stage = stage
        event = {
            "sequence": len(row.events) + 1,
            "time": now.isoformat(),
            "stage": stage,
            "message": message,
            **details,
        }
        row.activity = event
        row.events = [*row.events, event]
        row.heartbeat_at, row.lease_until = now, now + timedelta(seconds=90)

    async def event(self, stage: str, message: str, **details: Any) -> None:
        async with self.db.session() as s, s.begin():
            row = await s.scalar(self.owned())
            if row is None:
                raise BuildInterrupted("任务已取消或被接管")
            self.add_event(row, stage, message, **details)

    async def reserve_call(self, *, max_calls: int, max_tokens: int) -> dict[str, int]:
        async with self.db.session() as s, s.begin():
            row = await s.scalar(self.owned())
            if row is None:
                raise BuildInterrupted("任务已取消或被接管")
            usage = {
                "calls": int(row.usage.get("calls", 0)),
                "tokens": int(row.usage.get("tokens", 0)),
            }
            if usage["calls"] >= max_calls or usage["tokens"] >= max_tokens:
                raise ValueError("构建预算耗尽，请调整预算后新建任务；恢复不会重置预算")
            usage["calls"] += 1
            row.usage = {**row.usage, **usage}
            return usage

    async def record_tokens(self, tokens: int) -> None:
        async with self.db.session() as s, s.begin():
            row = await s.scalar(self.owned())
            if row is None:
                raise BuildInterrupted("任务已取消或被接管")
            row.usage = {**row.usage, "tokens": row.usage.get("tokens", 0) + tokens}

    async def heartbeat(self, seconds: float) -> None:
        async with self.db.session() as s, s.begin():
            row = await s.scalar(self.owned())
            if row is None:
                raise BuildInterrupted("任务已取消或被接管")
            row.heartbeat_at, row.lease_until = utc_now(), utc_now() + timedelta(seconds=90)
            row.usage = {**row.usage, "seconds": round(seconds, 2)}

    async def finish(self, result: dict[str, Any]) -> None:
        package = StylePackage.model_validate(result["package"])
        async with self.db.session() as s, s.begin():
            row = await s.scalar(self.owned())
            if row is None:
                raise BuildInterrupted("任务已取消或被接管")
            if package.version_id != row.id or package.style_id != row.style_id:
                raise ValueError("候选包归属错误")
            version = Version(
                id=row.id,
                style_id=row.style_id,
                build_run_id=row.id,
                name=row.name,
                package=result["package"],
                report=result["report"],
                actor=row.actor,
            )
            s.add(version)
            await s.flush()
            for item in result["cases"]:
                case = item["case"]
                s.add(
                    ReviewCase(
                        version_id=version.id,
                        test_case=case,
                        user_input=case["user_input"],
                        original_response=case["source_text"],
                        doctor_response=item["candidate"]["text"],
                        baseline_response=item["baseline"]["text"],
                        automated={
                            **item["candidate"],
                            "comparison": {
                                "preference": item["preference"],
                                "reason": item["reason"],
                            },
                        },
                    )
                )
            material_results = {m["example_id"]: m for m in result["materials"]}
            for material in row.snapshot["materials"]:
                await s.execute(
                    update(Example)
                    .where(
                        Example.id == material["id"],
                        Example.style_id == row.style_id,
                        Example.revision == material["revision"],
                    )
                    .values(
                        processed_revision=material["revision"],
                        last_run_id=row.id,
                        last_result=material_results[material["id"]],
                    )
                )
            row.report = result["report"]
            row.checkpoints = {**row.checkpoints, "materials": result["materials"]}
            row.progress_at = utc_now()
            self.add_event(row, "complete", result["report"]["conclusion"], state="completed")
            row.status, row.finished_at = "completed", utc_now()
            row.worker_token, row.lease_until = None, None

    async def fail(self, error: str) -> None:
        async with self.db.session() as s, s.begin():
            row = await s.scalar(self.owned())
            if row is None:
                return
            self.add_event(row, row.stage, error, state="failed")
            row.status, row.error, row.finished_at = "failed", error, utc_now()
            row.worker_token, row.lease_until = None, None
