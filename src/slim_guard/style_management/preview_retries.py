"""Queue a frozen failed-preview retry under the existing fenced build lifecycle."""

from typing import Any

from sqlalchemy import select

from slim_guard.db.models import new_uuid, utc_now
from slim_guard.db.session import Database
from slim_guard.expression_style.package import StylePackage, content_hash
from slim_guard.expression_style.review.policy import review_signature
from slim_guard.expression_style.trainer.contracts import WORKFLOW_VERSION, BuildExecutionConfig
from slim_guard.group_chat import ROLE_POLICY_VERSION

from .models import BuildRun, ReviewCase, Style, Version
from .preview_state import case_fingerprint, output_revision
from .views import build_summary


class PreviewRetryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def start(
        self,
        style_id: str,
        version_id: str,
        actor: str,
        *,
        model: str,
        request_key: str | None = None,
        config: BuildExecutionConfig | None = None,
    ) -> dict[str, Any]:
        key = content_hash(
            {
                "kind": "preview_retry",
                "style_id": style_id,
                "version_id": version_id,
                "key": request_key or new_uuid(),
            }
        )
        async with self.db.session() as session, session.begin():
            style = await session.scalar(
                select(Style).where(Style.id == style_id).with_for_update()
            )
            version = await session.scalar(
                select(Version).where(Version.id == version_id).with_for_update()
            )
            if style is None or version is None or version.style_id != style_id:
                raise LookupError("风格版本不存在")
            previous = await session.scalar(select(BuildRun).where(BuildRun.request_key == key))
            if previous is not None:
                return build_summary(previous)
            if version.status != "ready_for_review":
                raise ValueError("只能重试待评审版本；已发布版本不可修改")
            if await session.scalar(
                select(BuildRun.id).where(
                    BuildRun.style_id == style_id,
                    BuildRun.status.in_(["queued", "running"]),
                )
            ):
                raise ValueError("该风格已有构建或重试正在进行")
            package = StylePackage.model_validate(version.package)
            if package.style_id != style_id or package.version_id != version_id:
                raise ValueError("风格包归属错误")
            if (
                version.report.get("schema_version") != "2"
                or version.report.get("role_policy_version") != ROLE_POLICY_VERSION
                or version.report.get("workflow_version") != WORKFLOW_VERSION
            ):
                raise ValueError(
                    "此版本未按当前医生职责与直接对话流程试答，请重新构建；已有反馈保留"
                )
            if version.report.get("package_hash") != package.package_hash:
                raise ValueError("风格包已变化，请重新构建并评审")
            cases = list(
                await session.scalars(
                    select(ReviewCase)
                    .where(
                        ReviewCase.version_id == version_id,
                    )
                    .order_by(ReviewCase.id)
                )
            )
            if version.report.get("preview_metrics", {}).get("total") != len(cases):
                raise ValueError("试答清单不完整，请重新构建")
            signature = review_signature(model)
            if any(c.automated.get("package_hash") != package.package_hash for c in cases):
                raise ValueError("试答与风格包不对应，请重新构建")
            if any(
                c.automated.get("passed")
                and (
                    c.automated.get("execution", {}).get("review") != signature
                    or c.automated.get("execution", {}).get("model") != model
                )
                for c in cases
            ):
                raise ValueError("模型或检查策略已变化，不能混用旧检查结果；请重新构建全套试答")
            targets = [
                c for c in cases if not c.automated.get("passed") or c.automated.get("fallback")
            ]
            if not targets:
                raise ValueError("没有需要重试的失败用例")
            inputs = [
                {
                    "case_id": c.id,
                    "test_case": c.test_case,
                    "automated": c.automated,
                    "doctor_response": c.doctor_response,
                    "review": c.review,
                    "output_revision": output_revision(c),
                    "fingerprint": case_fingerprint(c),
                }
                for c in targets
            ]
            run_id = new_uuid()
            message = f"已冻结 {len(inputs)} 条失败用例；其他回答与评审保持不变"
            snapshot = {
                "workflow_version": WORKFLOW_VERSION,
                "job_kind": "preview_retry",
                "style_id": style_id,
                "retry_version_id": version_id,
                "target_count": len(inputs),
                "retry_package": package.model_dump(mode="json"),
                "retry_cases": inputs,
                "retry_version_report": version.report,
                "model": model,
                "review_signature": signature,
                "execution": (config or BuildExecutionConfig()).model_dump(mode="json"),
                "materials": [],
            }
            row = BuildRun(
                id=run_id,
                style_id=style_id,
                actor=actor,
                request_key=key,
                name=f"失败用例重试-{utc_now():%Y%m%d%H%M%S}-{run_id[:6]}",
                snapshot=snapshot,
                activity={"state": "queued", "message": message},
                progress_at=utc_now(),
                events=[
                    {
                        "sequence": 1,
                        "stage": "freeze",
                        "state": "completed",
                        "message": message,
                        "time": utc_now().isoformat(),
                    }
                ],
            )
            session.add(row)
            await session.flush()
            return build_summary(row)
