import pytest
from sqlalchemy import inspect, text

from slim_guard.db.session import Database
from slim_guard.style_management.contracts import ExampleInput, ReviewInput
from slim_guard.style_management.models import ReviewCase, Version
from slim_guard.style_management.repository import Repository
from slim_guard.style_management.runtime import RuntimeStyles


@pytest.fixture
async def db(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'styles.db'}")
    await db.migrate()
    yield db
    await db.close()


async def test_fresh_schema_only_has_new_style_tables_and_default(db):
    async with db.engine.connect() as c:
        tables = await c.run_sync(lambda conn: inspect(conn).get_table_names())
    assert not any(t.startswith("style_") for t in tables)
    assert "expression_examples" in tables
    assert await db.migrate() == ()
    rows = await Repository(db).styles()
    assert rows[0]["name"] == "医生风格"
    runtime = RuntimeStyles(db)
    snapshot = await runtime.get_runtime_snapshot(await runtime.resolve())
    assert snapshot.profile.version == "doctor_builtin_v1"
    assert snapshot.examples == ()


async def test_library_freeze_review_publish_activation_and_no_cross_style(db):
    repo = Repository(db)
    first = await repo.append(
        "doctor",
        ExampleInput(
            user_input="怎么称呼你",
            original_response="你可以叫我 SlimGuard。",
            desired_response="叫我 SlimGuard 就行。",
        ),
        "admin",
    )
    build = await repo.build("doctor", "admin")
    await repo.append(
        "doctor",
        ExampleInput(user_input="谢谢", original_response="不用谢。", desired_response="不客气。"),
        "admin",
    )
    assert len(build["snapshot"]["examples"]) == 1
    with pytest.raises(ValueError):
        await repo.build("doctor", "admin")
    with pytest.raises(ValueError):
        await repo.action("doctor", build["id"], "publish")
    async with db.session() as s, s.begin():
        version = await s.get(Version, build["id"])
        version.status = "ready_for_review"
        version.guide = {"summary": "简洁", "rules": [{"text": "简洁自然", "confidence": "stable"}]}
        case = ReviewCase(
            version_id=version.id,
            example_id=first["id"],
            user_input=first["user_input"],
            original_response=first["original_response"],
            doctor_response="叫我 SlimGuard 就行。",
            desired_response=first["desired_response"],
            automated={"passed": True},
        )
        s.add(case)
        await s.flush()
        cid = case.id
    with pytest.raises(LookupError):
        await repo.cases("wrong-style", build["id"])
    with pytest.raises(ValueError):
        ReviewInput(style_match=4, fidelity=4, appropriateness=4, decision="reject")
    await repo.review(
        "doctor",
        cid,
        ReviewInput(style_match=4, fidelity=4, appropriateness=4, decision="accept"),
        "admin",
    )
    await repo.action("doctor", build["id"], "publish")
    await repo.action("doctor", build["id"], "activate")
    assert await RuntimeStyles(db).resolve() == build["id"]
    with pytest.raises(ValueError):
        await repo.review(
            "doctor",
            cid,
            ReviewInput(style_match=4, fidelity=4, appropriateness=4, decision="accept"),
            "admin",
        )
    assert (await repo.examples("doctor", q="SlimGuard"))["total"] == 2
    assert (await repo.examples("doctor", limit=1, offset=1))["total"] == 3


async def test_reset_is_scoped_and_repeatable(db):
    from slim_guard.style_management.migration import reset_style_schema

    async with db.engine.begin() as c:
        await c.execute(text("CREATE TABLE style_ab_evaluation_cases (id TEXT)"))
        await c.execute(text("CREATE TABLE unrelated_user_data (id TEXT)"))
        await c.execute(text("INSERT INTO unrelated_user_data VALUES ('keep')"))
        await reset_style_schema(c)
        await reset_style_schema(c)
        assert await c.scalar(text("SELECT id FROM unrelated_user_data")) == "keep"
        tables = await c.run_sync(lambda conn: inspect(conn).get_table_names())
        assert "style_ab_evaluation_cases" not in tables


async def test_build_worker_generates_real_case_with_semantic_judgment(db):
    import json

    from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
    from slim_guard.style_management.worker import StyleWorker

    repo = Repository(db)
    example = await repo.append(
        "doctor",
        ExampleInput(
            user_input="怎么称呼你？",
            original_response="叫我 SlimGuard。",
            desired_response="叫我 SlimGuard 就行。",
        ),
        "tester",
    )
    version = await repo.build("doctor", "tester")
    requests = []

    class Gateway:
        async def complete(self, request):
            requests.append(request)
            if request.output_schema_name == "AnalysisBatch":
                result = {
                    "items": [
                        {
                            "example_id": example["id"],
                            "category": "expression",
                            "reason": "只调整句尾",
                            "expression_rule": "自然简短",
                        }
                    ]
                }
            elif request.output_schema_name == "Guide":
                result = {
                    "summary": "自然简短",
                    "rules": [
                        {
                            "text": "自然简短",
                            "confidence": "stable",
                            "evidence_ids": [example["id"]],
                        }
                    ],
                }
            elif request.output_schema_name == "StyledResponse":
                result = {
                    "text": "叫我 SlimGuard 就行。",
                    "style_profile_version": version["id"],
                    "used_block_ids": ["neutral"],
                }
            else:
                result = {"passed": True, "issues": []}
            return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(result)))

    assert await StyleWorker(db, Gateway(), "test-model").run_once()
    result = next(v for v in await repo.versions("doctor") if v["id"] == version["id"])
    assert result["status"] == "ready_for_review", result["error"]
    assert result["guide"]["rules"][0]["confidence"] == "candidate"
    cases = await repo.cases("doctor", version["id"])
    assert cases["items"][0]["doctor_response"] == "叫我 SlimGuard 就行。"
    assert cases["items"][0]["automated"]["passed"]
    assert len(requests) == 4


@pytest.mark.parametrize("repair_passes", [True, False])
async def test_style_agent_semantic_drift_retry_contains_reasons(db, repair_passes):
    import json

    from slim_guard.agent_models.fake import ScriptedModelGateway
    from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
    from slim_guard.style_management.runtime import evaluate_example

    def response(value):
        return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))

    wrong = {
        "text": "不错，要保持。",
        "style_profile_version": "version-test",
        "used_block_ids": ["neutral"],
    }
    right = {**wrong, "text": "叫我 SlimGuard 就行。"}
    gateway = ScriptedModelGateway(
        [
            response(wrong),
            response({"passed": False, "issues": ["删掉了产品名称，回答变成鼓励"]}),
            response(right),
            response({"passed": repair_passes, "issues": [] if repair_passes else ["语义不确定"]}),
        ]
    )
    result = await evaluate_example(
        gateway,
        "test",
        "version-test",
        {"summary": "简洁", "rules": [{"text": "简洁", "confidence": "stable"}]},
        {"original_response": "叫我 SlimGuard。"},
    )
    assert result["automated"]["passed"] is repair_passes
    assert len(result["automated"]["checks"]) == 2
    assert result["automated"]["checks"][0]["issues"] == ["删掉了产品名称，回答变成鼓励"]
    assert result["text"] == ("叫我 SlimGuard 就行。" if repair_passes else "叫我 SlimGuard。")
    assert "删掉了产品名称" in gateway.requests[2].messages[-1].content


async def test_semantic_schema_echo_is_rejected_then_retried(db):
    import json

    from slim_guard.agent_models.fake import ScriptedModelGateway
    from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
    from slim_guard.agents.style.agent import SemanticCheck
    from slim_guard.style_management.runtime import evaluate_example

    def response(value):
        return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))

    rewritten = {
        "text": "叫我 SlimGuard 就行。",
        "style_profile_version": "version-test",
        "used_block_ids": ["neutral"],
    }
    # Captured provider failure: a verdict mixed with the schema is not a valid verdict.
    echoed_schema = {**SemanticCheck.model_json_schema(), "passed": True, "issues": []}
    gateway = ScriptedModelGateway(
        [
            response(rewritten),
            response(echoed_schema),
            response(rewritten),
            response({"passed": True, "issues": []}),
        ]
    )
    result = await evaluate_example(
        gateway, "test", "version-test", {}, {"original_response": "你可以叫我 SlimGuard。"}
    )
    assert result["automated"]["passed"]
    assert result["automated"]["model_calls"] == 4
    assert result["automated"]["checks"][0]["passed"] is False
    assert result["automated"]["checks"][1]["passed"] is True
    prompt = gateway.requests[1].messages[0].content
    assert "返回判决实例" in prompt
    assert '{"passed":true,"issues":[]}' in prompt
    assert '"properties"' not in prompt
