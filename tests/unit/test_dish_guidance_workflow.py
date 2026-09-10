from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelRequest, ModelResponse
from slim_guard.agents.contracts import TurnDirective
from slim_guard.agents.dish_recognition import (
    ConfirmedDish,
    ConfirmedDishSet,
    DishConfirmationSource,
)
from slim_guard.agents.nutrition_retrieval import NutritionRetrievalAgent
from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.dish_knowledge import (
    DishCatalogDocument,
    DishCatalogRepository,
    DishCatalogService,
    DishRuleDocument,
)
from slim_guard.harness.pending_actions import PendingActionRepository
from slim_guard.harness.trace import NullHarnessRunRecorder
from slim_guard.nutrition_knowledge import (
    KnowledgeDocument,
    NutritionKnowledgeRepository,
    NutritionKnowledgeService,
)
from slim_guard.orchestration.coordinator import AgentWorkflowCoordinator, ShadowWorkflowRequest
from slim_guard.orchestration.repository import OrchestrationRepository

NOW = datetime(2026, 9, 10, tzinfo=UTC)


class DishDirectiveGateway:
    def __init__(
        self,
        *,
        dish_names: tuple[str, ...] = (),
        resolves_pending: bool = False,
    ) -> None:
        self.dish_names = dish_names
        self.resolves_pending = resolves_pending

    async def complete(self, request: ModelRequest) -> ModelResponse:
        directive = TurnDirective(
            response_path="dish_guidance",
            interaction_kind="question",
            user_need_summary="判断菜品是否适合减脂期",
            response_brief="逐道菜判断并给出调整建议",
            professional_question="这些菜减脂期怎么安排？",
            dish_names=self.dish_names,
            resolves_pending_dish_confirmation=self.resolves_pending,
            voice_act="explain",
        )
        return ModelResponse(
            message=ModelMessage(
                role=MessageRole.ASSISTANT,
                content=json.dumps(directive.model_dump(mode="json"), ensure_ascii=False),
            )
        )

    async def close(self) -> None:
        return None


async def _catalog(tmp_path) -> tuple[Database, DishCatalogRepository]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'workflow.sqlite3'}")
    await database.create_schema()
    repository = DishCatalogRepository(database)
    service = DishCatalogService(repository)
    imported = await service.import_documents(
        (
            DishCatalogDocument(
                entity_key="tomato_egg",
                version="1",
                canonical_name="番茄炒蛋",
                source_refs=("source-dish",),
                rules=(
                    DishRuleDocument(
                        rule_key="weight.adjust",
                        condition_type="goal",
                        condition_value="weight_management",
                        effect="adjust",
                        statement="烹调时少放油，并避免额外加糖。",
                        applicability=("adult",),
                        source_ref="source-guideline",
                        version="1",
                    ),
                ),
            ),
        ),
        imported_by="admin",
    )
    entity_id = imported.entries[0].entry.entity.id
    await service.approve(entity_id, reviewer="reviewer")
    await service.publish(entity_id, reviewer="publisher")
    return database, repository


async def test_text_dish_runs_retrieval_guidance_and_neutral_rendering(tmp_path) -> None:
    database, catalog = await _catalog(tmp_path)
    coordinator = AgentWorkflowCoordinator(
        model=DishDirectiveGateway(dish_names=("番茄炒蛋",)),
        recorder=NullHarnessRunRecorder(),
        model_name="test-model",
        graph_version="typed-supervisor-v1",
        meal_guidance_enabled=True,
        nutrition_retrieval_agent=NutritionRetrievalAgent(catalog=catalog),
        style_enabled=False,
        clock=lambda: NOW,
    )
    try:
        result = await coordinator.run_shadow(
            ShadowWorkflowRequest(
                trace_id="trace-1",
                turn_id="turn-1",
                user_request="番茄炒蛋减脂期能吃吗？",
                context=(ModelMessage(role=MessageRole.USER, content="番茄炒蛋减脂期能吃吗？"),),
                current_items=(),
                deadline_at=NOW + timedelta(seconds=30),
            )
        )
        assert result.status.value == "succeeded", result.failure_code
        assert result.shadow_candidate is not None
        assert "番茄炒蛋" in result.shadow_candidate
        assert "建议调整" in result.shadow_candidate
        assert "少放油" in result.shadow_candidate
        assert "nutrition_retrieval_running" in result.actual_nodes
        assert "expert_running" in result.actual_nodes
        assert {item.artifact_type for item in result.artifacts}.issuperset(
            {"confirmed_dish_set", "dish_evidence_bundle", "diet_guidance_assessment"}
        )
    finally:
        await database.close()


async def test_retrieval_binds_only_published_rag_evidence_to_current_invocation(
    tmp_path,
) -> None:
    database, catalog = await _catalog(tmp_path)
    knowledge = NutritionKnowledgeService(NutritionKnowledgeRepository(database))
    imported = await knowledge.import_documents(
        (
            KnowledgeDocument(
                source_key="adult-weight-guidance",
                version="1",
                title="测试用成人膳食资料",
                publisher="测试权威来源",
                published_at=date(2026, 1, 1),
                source_url="https://example.org/adult-weight-guidance",
                content="番茄炒蛋可通过少油烹调，并与其他蔬菜搭配。",
                tags=("nutrition", "weight_management"),
                applicability=("adult",),
            ),
        ),
        imported_by="test-importer",
        created_at=NOW,
    )
    source_id = imported.documents[0].source.id
    await knowledge.approve_source(source_id, reviewer="test-reviewer")
    await knowledge.publish_source(source_id, reviewer="test-publisher")
    agent = NutritionRetrievalAgent(catalog=catalog, knowledge=knowledge)
    try:
        result = await agent.run(
            invocation_id="retrieval-current-turn",
            dishes=ConfirmedDishSet(
                dishes=(
                    ConfirmedDish(
                        dish_ref="dish-text-1",
                        name="番茄炒蛋",
                        source=DishConfirmationSource.USER_TEXT,
                    ),
                )
            ),
        )
        assert result.status.value == "succeeded"
        assert result.evidence is not None
        dish = result.evidence.dishes[0]
        assert dish.rag_evidence
        assert dish.citations
        assert dish.rag_evidence[0].citation_ref == dish.citations[0].citation_id
        assert dish.citations[0].retrieved_in_invocation_id == "retrieval-current-turn"
        assert dish.citations[0].applicability == ("adult",)
    finally:
        await database.close()


async def test_workflow_applies_only_active_structured_user_constraints(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'constraint-workflow.sqlite3'}")
    await database.create_schema()
    catalog = DishCatalogRepository(database)
    service = DishCatalogService(catalog)
    imported = await service.import_documents(
        (
            DishCatalogDocument(
                entity_key="peanut_spinach",
                version="1",
                canonical_name="花生拌菠菜",
                source_refs=("dish-source",),
                rules=(
                    DishRuleDocument(
                        rule_key="weight.adjust",
                        condition_type="goal",
                        condition_value="weight_management",
                        effect="adjust",
                        statement="酱汁分开放，按需要少量加入。",
                        applicability=("adult",),
                        source_ref="guideline-source",
                        version="1",
                    ),
                    DishRuleDocument(
                        rule_key="peanut.constraint",
                        condition_type="constraint",
                        condition_value="花生",
                        effect="avoid",
                        statement="按用户已经明确说明的花生限制，应避免这道菜。",
                        applicability=("adult",),
                        source_ref="constraint-source",
                        version="1",
                    ),
                ),
            ),
        ),
        imported_by="admin",
    )
    entity_id = imported.entries[0].entry.entity.id
    await service.approve(entity_id, reviewer="reviewer")
    await service.publish(entity_id, reviewer="publisher")
    coordinator = AgentWorkflowCoordinator(
        model=DishDirectiveGateway(dish_names=("花生拌菠菜",)),
        recorder=NullHarnessRunRecorder(),
        model_name="test-model",
        graph_version="typed-supervisor-v1",
        meal_guidance_enabled=True,
        nutrition_retrieval_agent=NutritionRetrievalAgent(catalog=catalog),
        style_enabled=False,
        clock=lambda: NOW,
    )
    active_memory = {
        "memory_id": "memory-peanut-constraint",
        "kind": "constraint",
        "key": "constraint.dietary",
        "value": {"subject": "花生", "statement": "我不吃花生"},
        "stale": False,
    }
    try:
        active = await coordinator.run_shadow(
            ShadowWorkflowRequest(
                trace_id="trace-constraint-active",
                turn_id="turn-constraint-active",
                user_request="花生拌菠菜减脂期能吃吗？",
                context=(ModelMessage(role=MessageRole.USER, content="花生拌菠菜减脂期能吃吗？"),),
                current_items=(),
                authoritative_context={"profile_memory": [active_memory]},
                deadline_at=NOW + timedelta(seconds=30),
            )
        )
        active_guidance = next(
            item for item in active.artifacts if item.artifact_type == "diet_guidance_assessment"
        )
        assert active_guidance.payload["dishes"][0]["suitability"] == "avoid"
        assert active_guidance.payload["dishes"][0]["user_constraint_refs"] == [
            "memory-peanut-constraint"
        ]

        stale = await coordinator.run_shadow(
            ShadowWorkflowRequest(
                trace_id="trace-constraint-stale",
                turn_id="turn-constraint-stale",
                user_request="花生拌菠菜减脂期能吃吗？",
                context=(ModelMessage(role=MessageRole.USER, content="花生拌菠菜减脂期能吃吗？"),),
                current_items=(),
                authoritative_context={"profile_memory": [{**active_memory, "stale": True}]},
                deadline_at=NOW + timedelta(seconds=30),
            )
        )
        stale_guidance = next(
            item for item in stale.artifacts if item.artifact_type == "diet_guidance_assessment"
        )
        assert stale_guidance.payload["dishes"][0]["suitability"] == ("suitable_with_adjustment")
        assert stale_guidance.payload["dishes"][0]["user_constraint_refs"] == []
    finally:
        await database.close()


async def test_uncertain_visual_dish_stops_before_retrieval_and_asks(tmp_path) -> None:
    database, catalog = await _catalog(tmp_path)
    coordinator = AgentWorkflowCoordinator(
        model=DishDirectiveGateway(),
        recorder=NullHarnessRunRecorder(),
        model_name="test-model",
        graph_version="typed-supervisor-v1",
        meal_guidance_enabled=True,
        nutrition_retrieval_agent=NutritionRetrievalAgent(catalog=catalog),
        style_enabled=False,
        clock=lambda: NOW,
    )
    recognition = {
        "schema_version": "1",
        "asset_id": "asset-1",
        "model": "vision-model",
        "prompt_version": "dish-recognition-zh-v1",
        "policy_version": "dish-confirmation-policy-v1",
        "image_kind": "meal",
        "quality_flags": [],
        "dishes": [
            {
                "dish_ref": "dish-1",
                "candidates": [
                    {"label": "麻婆豆腐", "confidence": 0.74},
                    {"label": "家常豆腐", "confidence": 0.69},
                ],
                "visible_ingredients": ["豆腐"],
                "preparation_candidates": [],
                "uncertainty_reasons": ["两种做法外观相似"],
                "requires_confirmation": True,
            }
        ],
        "suggested_question": "这盘豆腐是麻辣的，还是普通家常做法？",
        "overall_requires_confirmation": True,
        "provider_request_id": "vision-1",
    }
    try:
        result = await coordinator.run_shadow(
            ShadowWorkflowRequest(
                trace_id="trace-2",
                turn_id="turn-2",
                user_request="这些菜能吃吗？",
                context=(ModelMessage(role=MessageRole.USER, content="这些菜能吃吗？"),),
                current_items=(
                    {
                        "id": "receipt-1",
                        "item_type": "tool_result",
                        "payload": {
                            "tool_name": "inspect_image",
                            "status": "succeeded",
                            "output": {"dish_recognition": recognition},
                        },
                    },
                ),
                deadline_at=NOW + timedelta(seconds=30),
            )
        )
        assert result.shadow_candidate is not None
        assert "麻辣" in result.shadow_candidate
        assert "dish_confirmation_pending" in result.actual_nodes
        assert "nutrition_retrieval_running" not in result.actual_nodes
    finally:
        await database.close()


async def test_live_workflow_persists_and_resolves_cross_turn_dish_confirmation(
    tmp_path,
) -> None:
    database, catalog = await _catalog(tmp_path)
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                AgentVersionRecord(
                    id="version-1",
                    manifest_json="{}",
                    code_revision="test",
                    created_at=NOW,
                ),
                AgentThreadRecord(
                    id="thread-1",
                    user_id="user-1",
                    created_at=NOW,
                    last_active_at=NOW,
                ),
            )
        )
    async with database.session() as session, session.begin():
        session.add_all(
            (
                AgentTurnRecord(
                    id="turn-live-1",
                    thread_id="thread-1",
                    agent_version_id="version-1",
                    trigger_type="user_message",
                    status="running",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                AgentTurnRecord(
                    id="turn-live-2",
                    thread_id="thread-1",
                    agent_version_id="version-1",
                    trigger_type="user_message",
                    status="running",
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
    async with database.session() as session, session.begin():
        session.add_all(
            (
                AgentItemRecord(
                    id="item-live-1",
                    thread_id="thread-1",
                    turn_id="turn-live-1",
                    sequence=1,
                    item_type="user_message",
                    status="completed",
                    payload_json='{"text":"这是什么菜？"}',
                    created_at=NOW,
                ),
                AgentItemRecord(
                    id="item-live-2",
                    thread_id="thread-1",
                    turn_id="turn-live-2",
                    sequence=1,
                    item_type="user_message",
                    status="completed",
                    payload_json='{"text":"是番茄炒蛋"}',
                    created_at=NOW,
                ),
            )
        )
    pending = PendingActionRepository(database)
    persistence = OrchestrationRepository(database)
    first = AgentWorkflowCoordinator(
        model=DishDirectiveGateway(),
        recorder=NullHarnessRunRecorder(),
        model_name="test-model",
        graph_version="typed-supervisor-v1",
        meal_guidance_enabled=True,
        nutrition_retrieval_agent=NutritionRetrievalAgent(catalog=catalog),
        pending_dish_confirmations=pending,
        persistence=persistence,
        style_enabled=False,
        clock=lambda: NOW,
    )
    recognition = {
        "schema_version": "1",
        "asset_id": "asset-live",
        "model": "vision-model",
        "prompt_version": "dish-recognition-zh-v1",
        "policy_version": "dish-confirmation-policy-v1",
        "image_kind": "meal",
        "quality_flags": [],
        "dishes": [
            {
                "dish_ref": "dish-1",
                "candidates": [
                    {"label": "番茄炒蛋", "confidence": 0.74},
                    {"label": "番茄豆腐", "confidence": 0.65},
                ],
                "visible_ingredients": ["番茄"],
                "preparation_candidates": [],
                "uncertainty_reasons": ["主体食材被遮挡"],
                "requires_confirmation": True,
            }
        ],
        "suggested_question": "这是番茄炒蛋还是番茄豆腐？",
        "overall_requires_confirmation": True,
        "provider_request_id": "vision-live",
    }
    try:
        await first.run_shadow(
            ShadowWorkflowRequest(
                trace_id="trace-live-1",
                user_id="user-1",
                thread_id="thread-1",
                turn_id="turn-live-1",
                user_request="这是什么菜，能吃吗？",
                context=(ModelMessage(role=MessageRole.USER, content="这是什么菜，能吃吗？"),),
                current_items=(
                    {"id": "item-live-1", "item_type": "user_message", "payload": {}},
                    {
                        "id": "receipt-live",
                        "item_type": "tool_result",
                        "payload": {
                            "tool_name": "inspect_image",
                            "status": "succeeded",
                            "output": {"dish_recognition": recognition},
                        },
                    },
                ),
                mode="on",
                deadline_at=NOW + timedelta(seconds=30),
            )
        )
        open_actions = await pending.list_open_for_user(user_id="user-1", at=NOW)
        assert [item.tool_name for item in open_actions] == ["confirm_dishes"]

        second = AgentWorkflowCoordinator(
            model=DishDirectiveGateway(
                dish_names=("番茄炒蛋",),
                resolves_pending=True,
            ),
            recorder=NullHarnessRunRecorder(),
            model_name="test-model",
            graph_version="typed-supervisor-v1",
            meal_guidance_enabled=True,
            nutrition_retrieval_agent=NutritionRetrievalAgent(catalog=catalog),
            pending_dish_confirmations=pending,
            persistence=persistence,
            style_enabled=False,
            clock=lambda: NOW + timedelta(minutes=1),
        )
        result = await second.run_shadow(
            ShadowWorkflowRequest(
                trace_id="trace-live-2",
                user_id="user-1",
                thread_id="thread-1",
                turn_id="turn-live-2",
                user_request="是番茄炒蛋",
                context=(ModelMessage(role=MessageRole.USER, content="是番茄炒蛋"),),
                current_items=({"id": "item-live-2", "item_type": "user_message", "payload": {}},),
                mode="on",
                deadline_at=NOW + timedelta(minutes=2),
            )
        )
        assert result.shadow_candidate is not None
        assert "番茄炒蛋" in result.shadow_candidate
        assert "nutrition_retrieval_running" in result.actual_nodes
        assert (
            await pending.list_open_for_user(
                user_id="user-1",
                at=NOW + timedelta(minutes=1),
            )
            == []
        )
        confirmation = next(
            item for item in result.artifacts if item.artifact_type == "confirmed_dish_set"
        )
        assert confirmation.producer_role.value == "user_dish_confirmation"
        assert confirmation.payload["dishes"][0]["user_evidence_ref"] == "item-live-2"
    finally:
        await database.close()
