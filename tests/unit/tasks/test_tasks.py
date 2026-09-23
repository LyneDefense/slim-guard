from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import (
    ItemStatus,
    ItemType,
    PendingActionStatus,
    PendingActionType,
    TurnTrigger,
)
from slim_guard.harness.initialization import TurnInitializationRequest, TurnInitializer, TurnInput
from slim_guard.harness.pending_actions import PendingActionRepository
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tasks.contracts import TaskConflict, TaskError, TaskWrite
from slim_guard.tasks.repository import ConversationTaskRepository
from slim_guard.tasks.tools import GetTaskArguments, TaskToolHandlers
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResultStatus
from tests.unit.test_working_memory import manifest

NOW = datetime(2026, 9, 23, 8, tzinfo=UTC)


@pytest.fixture
async def database(tmp_path: Path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'tasks.sqlite'}")
    await db.create_schema()
    async with db.session() as session, session.begin():
        session.add_all(
            [SlimGuardUser(id=u, first_seen_at=NOW, last_seen_at=NOW) for u in ("user-1", "user-2")]
        )
    await AgentVersionRepository(db).register(manifest())
    yield db
    await db.close()


async def source(database: Database, text: str | None, *, user_id: str = "user-1") -> TaskWrite:
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id=user_id,
            agent_version_id=manifest().version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(
                TurnInput.user_message(text=text)
                if text is not None
                else TurnInput.image_attachment(asset_id="image-only", mime_type="image/png"),
            ),
        )
    )
    assert initialized.source_item_id
    return TaskWrite(
        user_id=user_id,
        thread_id=initialized.thread.id,
        source_turn_id=initialized.turn.id,
        source_item_id=initialized.source_item_id,
        operation_id=initialized.turn.id,
        objective="处理用户请求",
        unresolved=("缺少一些信息",),
        evidence_excerpt=(text or "")[:512],
        interpretation="需要补充信息",
    )


async def test_multiple_tasks_do_not_replace_each_other_and_are_isolated(database: Database):
    repo = ConversationTaskRepository(database, clock=lambda: NOW)
    first = await repo.write(await source(database, "明天再聊我的午餐"))
    second = await repo.write(await source(database, "另外帮我看看运动计划"))
    tasks = await repo.list_for_user("user-1", at=NOW)
    assert {t.id for t in tasks} == {first.id, second.id}
    assert await repo.list_for_user("user-2", at=NOW) == ()
    page = await repo.list_for_user("user-1", at=NOW, limit=1, offset=1)
    assert len(page) == 1
    with pytest.raises(TaskError):
        await repo.get(first.id, user_id="user-2", thread_id=first.thread_id)


async def test_multiturn_answers_preserve_prior_evidence_and_original_attachment(
    database: Database,
):
    repo = ConversationTaskRepository(database, clock=lambda: NOW)
    original = await source(database, "这是午餐")
    await HarnessStateRepository(database).append_item(
        turn_id=original.source_turn_id,
        item_type=ItemType.IMAGE_ATTACHMENT,
        status=ItemStatus.COMPLETED,
        payload={"asset_id": "original-image"},
    )
    first = await repo.write(
        original.model_copy(update={"unresolved": ("蛋怎么做的", "深色菜是什么")})
    )
    answer1 = await source(database, "荷包蛋")
    partial = await repo.write(
        answer1.model_copy(
            update={
                "task_id": first.id,
                "expected_revision": 1,
                "unresolved": ("深色菜是什么",),
                "interpretation": "已确认荷包蛋",
            }
        )
    )
    answer2 = await source(database, "烧茄子")
    complete = await repo.write(
        answer2.model_copy(
            update={
                "task_id": first.id,
                "expected_revision": 2,
                "unresolved": (),
                "status": "resolved",
                "interpretation": "已确认烧茄子，不再追问菜品",
            }
        )
    )
    assert partial.revision == 2
    assert complete.revision == 3
    assert complete.attachment_ids == ("original-image",)
    assert [e.excerpt for e in complete.evidence] == ["这是午餐", "荷包蛋", "烧茄子"]
    assert complete.source_item_ids == (
        original.source_item_id,
        answer1.source_item_id,
        answer2.source_item_id,
    )
    assert await repo.list_for_user("user-1", at=NOW) == ()
    # Loading task evidence does not depend on the recent-dialogue window.
    handlers = TaskToolHandlers(repo, PendingActionRepository(database), clock=lambda: NOW)
    result = await handlers.get(
        ToolContext(
            user_id="user-1",
            thread_id=original.thread_id,
            turn_id=answer2.source_turn_id,
            tool_call_id="get-task",
            agent_version_id=manifest().version_id,
            execution_mode=ToolExecutionMode.EVALUATION,
        ),
        GetTaskArguments(task_id=first.id),
    )
    assert result.status is ToolResultStatus.SUCCEEDED
    assert result.output["task"]["attachment_ids"] == ["original-image"]
    assert result.output["evidence_page"][1]["excerpt"] == "荷包蛋"


async def test_replay_revision_conflict_cancel_and_expiry(database: Database):
    repo = ConversationTaskRepository(database, clock=lambda: NOW)
    cmd = await source(database, "之后再继续")
    task = await repo.write(cmd)
    assert await repo.write(cmd) == task
    with pytest.raises(TaskConflict):
        await repo.write(cmd.model_copy(update={"objective": "different"}))
    answer = await source(database, "不用了")
    cancelled = await repo.write(
        answer.model_copy(
            update={
                "task_id": task.id,
                "expected_revision": 1,
                "status": "cancelled",
            }
        )
    )
    assert cancelled.status == "cancelled"
    with pytest.raises(TaskConflict):
        await repo.write(
            answer.model_copy(
                update={
                    "operation_id": "different",
                    "task_id": task.id,
                    "expected_revision": 1,
                }
            )
        )
    # A replay returns the original snapshot, but cannot overwrite the cancellation.
    assert await repo.write(cmd) == task
    assert (
        await repo.get(task.id, user_id=task.user_id, thread_id=task.thread_id)
    ).status == "cancelled"
    expiring = await repo.write(await source(database, "新事项"))
    assert await repo.list_for_user("user-1", at=NOW + timedelta(days=15)) == ()
    assert await repo.expire_due(at=NOW + timedelta(days=15)) == 1
    assert (
        await repo.get(expiring.id, user_id="user-1", thread_id=task.thread_id)
    ).status == "expired"


async def test_stale_partial_update_cannot_erase_newer_answer(database: Database):
    repo = ConversationTaskRepository(database, clock=lambda: NOW)
    task = await repo.write(await source(database, "需要确认两件事"))
    answer = await source(database, "第一件已经说明了")
    update = answer.model_copy(update={"task_id": task.id, "expected_revision": 1})
    latest = await repo.write(update)
    with pytest.raises(TaskConflict):
        await repo.write(update.model_copy(update={"operation_id": "stale"}))
    assert await repo.get(task.id, user_id="user-1", thread_id=task.thread_id) == latest


@pytest.mark.parametrize("corruption", ["quote", "owner", "assistant", "thread"])
async def test_forged_sources_rejected(database: Database, corruption: str):
    repo = ConversationTaskRepository(database, clock=lambda: NOW)
    cmd = await source(database, "这是原话")
    if corruption == "quote":
        cmd = cmd.model_copy(update={"evidence_excerpt": "用户没有说的内容"})
    elif corruption == "owner":
        foreign = await source(database, "foreign", user_id="user-2")
        cmd = cmd.model_copy(update={"source_item_ids": (foreign.source_item_id,)})
    elif corruption == "thread":
        cmd = cmd.model_copy(update={"thread_id": "not-this-thread"})
    else:
        item = await HarnessStateRepository(database).append_item(
            turn_id=cmd.source_turn_id,
            item_type=ItemType.AGENT_MESSAGE,
            status=ItemStatus.COMPLETED,
            payload={"text": "助手说的"},
        )
        cmd = cmd.model_copy(update={"source_item_ids": (item.id,)})
    with pytest.raises(TaskError):
        await repo.write(cmd)


async def test_link_and_task_resolution_never_approve_an_operation(database: Database):
    repo = ConversationTaskRepository(database, clock=lambda: NOW)
    cmd = await source(database, "请处理这个请求")
    task = await repo.write(cmd)
    actions = PendingActionRepository(database)
    created = await actions.create(
        thread_id=cmd.thread_id,
        turn_id=cmd.source_turn_id,
        source_item_id=cmd.source_item_id,
        execution_key="authorized-write",
        tool_call_id="delete",
        tool_name="delete_record",
        tool_version="v1",
        canonical_arguments={"record_id": "record-1"},
        execution_mode=ToolExecutionMode.EVALUATION,
        isolated_write_environment=True,
        action_type=PendingActionType.USER_CONFIRMATION,
        reason="sensitive",
        expires_at=NOW + timedelta(minutes=15),
    )
    linked = await actions.attach_task(action_id=created.action.id, task=task)
    assert linked.task_id == task.id
    assert (await actions.attach_task(action_id=linked.id, task=task)) == linked
    resolution = await source(database, "信息都补充了")
    await repo.write(
        resolution.model_copy(
            update={
                "task_id": task.id,
                "expected_revision": 1,
                "status": "resolved",
                "unresolved": (),
            }
        )
    )
    action = await actions.get(linked.id)
    assert action and action.status is PendingActionStatus.PENDING
    assert action.canonical_arguments == {"record_id": "record-1"}
    foreign_task = await repo.write(await source(database, "其他用户请求", user_id="user-2"))
    with pytest.raises(LookupError):
        await actions.attach_task(action_id=linked.id, task=foreign_task)


async def test_image_only_input_is_a_valid_task_source(database: Database):
    repo = ConversationTaskRepository(database, clock=lambda: NOW)
    cmd = await source(database, None)
    task = await repo.write(cmd)
    assert task.attachment_ids == ("image-only",)
    assert task.evidence[0].excerpt == ""


async def test_old_attachment_cannot_substitute_for_missing_current_evidence(database: Database):
    repo = ConversationTaskRepository(database, clock=lambda: NOW)
    old = await source(database, "先前图片")
    await HarnessStateRepository(database).append_item(
        turn_id=old.source_turn_id,
        item_type=ItemType.IMAGE_ATTACHMENT,
        status=ItemStatus.COMPLETED,
        payload={"asset_id": "old-image"},
    )
    empty = await source(database, "当前消息")
    invalid = await HarnessStateRepository(database).append_item(
        turn_id=empty.source_turn_id,
        item_type=ItemType.USER_MESSAGE,
        status=ItemStatus.COMPLETED,
        payload={"text": ""},
    )
    with pytest.raises(TaskError):
        await repo.write(
            empty.model_copy(
                update={
                    "source_item_id": invalid.id,
                    "evidence_excerpt": "",
                    "source_item_ids": (old.source_item_id,),
                }
            )
        )


def test_resolved_contract_requires_no_remaining_items():
    with pytest.raises(ValidationError):
        TaskWrite(
            user_id="u",
            thread_id="t",
            source_turn_id="turn",
            source_item_id="i",
            operation_id="op",
            task_id="task",
            expected_revision=1,
            objective="test",
            unresolved=("still unfinished",),
            status="resolved",
            evidence_excerpt="done",
            interpretation="done",
        )
