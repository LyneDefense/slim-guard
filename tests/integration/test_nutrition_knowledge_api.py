from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from slim_guard.config import Settings
from slim_guard.main import create_app
from slim_guard.nutrition_knowledge import NutritionKnowledgeRepository
from slim_guard.nutrition_rag.answerability import (
    AnswerabilityDocument,
    AnswerabilityResult,
)
from slim_guard.nutrition_rag.gateways import (
    EmbeddingBatch,
    RerankItem,
    RerankResult,
)
from slim_guard.nutrition_rag.ingestion import (
    NutritionKnowledgeIngestionService,
    NutritionKnowledgeWorker,
)
from slim_guard.nutrition_rag.retrieval import HybridNutritionRagService
from slim_guard.nutrition_rag.storage import InMemoryNutritionObjectStore


class ApiEmbeddingGateway:
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=tuple((0.01,) * 1024 for _ in texts),
            model="test-embedding",
            request_id="embedding-api-test",
            prompt_tokens=len(texts),
            latency_ms=1,
        )

    async def close(self) -> None:
        return None


class ApiRerankGateway:
    async def rerank(self, *, query: str, documents: Sequence[str], top_n: int) -> RerankResult:
        return RerankResult(
            items=tuple(
                RerankItem(index=index, score=0.9) for index in range(min(len(documents), top_n))
            ),
            model="test-rerank",
            request_id="rerank-api-test",
            prompt_tokens=len(documents),
            latency_ms=1,
        )

    async def close(self) -> None:
        return None


class ApiAnswerabilityGateway:
    async def assess(
        self, *, query: str, documents: Sequence[AnswerabilityDocument]
    ) -> AnswerabilityResult:
        del query
        return AnswerabilityResult(
            outcome="supported",
            supported_document_indices=tuple(range(len(documents))),
            reason_code="directly_supported",
            model="test-answerability",
            request_id="answerability-api-test",
            input_tokens=len(documents),
            output_tokens=1,
        )


@pytest.mark.parametrize("import_method", ["pasted_text", "multipart"])
async def test_admin_can_import_review_build_and_inspect_hybrid_rag(
    tmp_path: Path, import_method: str
) -> None:
    app = create_app(
        Settings(
            app_env="test",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'nutrition-api.sqlite3'}",
            admin_username="admin",
            admin_password="test-password",
            style_iteration_worker_enabled=False,
            routine_scheduler_enabled=False,
            _env_file=None,
        )
    )
    async with app.router.lifespan_context(app):
        store = InMemoryNutritionObjectStore()
        app.state.nutrition_object_store = store
        embedding = ApiEmbeddingGateway()
        app.state.nutrition_knowledge = HybridNutritionRagService(
            repository=app.state.nutrition_rag,
            embedding_gateway=embedding,
            rerank_gateway=ApiRerankGateway(),
            answerability_gateway=ApiAnswerabilityGateway(),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
            login = await client.post(
                "/api/admin/auth/login",
                json={"username": "admin", "password": "test-password"},
            )
            assert login.status_code == 200
            no_csrf = await client.post(
                "/api/admin/nutrition-knowledge/sources/imports",
                json={"method": "pasted_text"},
            )
            assert no_csrf.status_code == 403

            content = (
                "# 减重餐食\n\n减重期间可以吃家常菜，建议搭配蔬菜、全谷物和蛋白质，"
                "并根据菜品特点控制烹饪用油和盐。"
            )
            metadata = {
                "source_key": "meal-guidance",
                "version": "v1",
                "title": "减重餐食搭配",
                "publisher": "测试机构",
                "tags": ["减重", "餐食搭配"],
                "applicability": ["adult", "china"],
            }
            if import_method == "multipart":
                created = await client.post(
                    "/api/admin/nutrition-knowledge/sources/imports",
                    headers={"X-SlimGuard-CSRF": "1"},
                    data={"metadata": json.dumps(metadata, ensure_ascii=False)},
                    files={"file": ("meal.md", content.encode(), "text/markdown")},
                )
            else:
                created = await client.post(
                    "/api/admin/nutrition-knowledge/sources/imports",
                    headers={"X-SlimGuard-CSRF": "1"},
                    json=metadata
                    | {"method": "pasted_text", "content": content, "filename": "meal.md"},
                )
            assert created.status_code == 202, created.text
            job_id = created.json()["job"]["id"]
            queued = await client.get(f"/api/admin/nutrition-knowledge/jobs/{job_id}")
            assert queued.json()["job"]["status"] == "queued"

            worker = NutritionKnowledgeWorker(
                ingestion=NutritionKnowledgeIngestionService(
                    repository=app.state.nutrition_rag,
                    object_store=store,
                    embedding_gateway=embedding,
                    max_asset_bytes=1024 * 1024,
                    legacy_repository=NutritionKnowledgeRepository(app.state.database),
                ),
                worker_id="api-test-worker",
                poll_seconds=0.01,
                lease_seconds=60,
            )
            assert await worker.run_once() is True
            completed = await client.get(f"/api/admin/nutrition-knowledge/jobs/{job_id}")
            source_id = completed.json()["job"]["output"]["source_id"]
            assert completed.json()["job"]["status"] == "succeeded"

            for review_type in ("content", "applicability", "rights"):
                reviewed = await client.post(
                    f"/api/admin/nutrition-knowledge/sources/{source_id}/reviews",
                    headers={"X-SlimGuard-CSRF": "1"},
                    json={
                        "review_type": review_type,
                        "decision": "approve",
                        "attestations": {"confirmed": True},
                    },
                )
                assert reviewed.status_code == 200, reviewed.text
            assert reviewed.json()["source"]["status"] == "approved"

            release_response = await client.post(
                "/api/admin/nutrition-knowledge/releases",
                headers={
                    "X-SlimGuard-CSRF": "1",
                    "Idempotency-Key": "api-test-release-v1",
                },
                json={"version": "nutrition-api-v1", "source_ids": [source_id]},
            )
            assert release_response.status_code == 200, release_response.text
            release = release_response.json()["release"]
            assert release["status"] == "evaluating"

            lab = await client.post(
                "/api/admin/nutrition-knowledge/retrieval-lab/runs",
                headers={"X-SlimGuard-CSRF": "1"},
                json={
                    "query": "家常菜怎么搭配",
                    "release_id": release["id"],
                    "metadata_filter": {"applicability": ["adult", "china"]},
                },
            )
            assert lab.status_code == 200, lab.text
            run_id = lab.json()["retrieval_run_id"]
            inspected = await client.get(
                f"/api/admin/nutrition-knowledge/retrieval-lab/runs/{run_id}"
            )
            assert inspected.status_code == 200
            assert inspected.json()["candidates"][0]["dense_rank"] == 1
            assert inspected.json()["candidates"][0]["selection_status"] == "adopted"

            dataset = await client.post(
                "/api/admin/nutrition-knowledge/evaluation-datasets",
                headers={"X-SlimGuard-CSRF": "1"},
                json={
                    "version": "draft-small-v1",
                    "cases": [
                        {
                            "case_key": "meal-1",
                            "query_plan_input": {"query": "家常菜"},
                            "expected_source_keys": ["meal-guidance"],
                            "expected_chunk_concepts": ["搭配"],
                            "forbidden_source_keys": [],
                            "expected_outcome": "evidence",
                        }
                    ],
                },
            )
            assert dataset.status_code == 200
            assert dataset.json()["dataset"]["status"] == "draft"

            appended = await client.post(
                f"/api/admin/nutrition-knowledge/retrieval-lab/runs/{run_id}/evaluation-cases",
                headers={"X-SlimGuard-CSRF": "1"},
                json={
                    "base_dataset_id": dataset.json()["dataset"]["id"],
                    "dataset_version": "draft-small-v2",
                    "case_key": "meal-lab-regression",
                    "expected_source_keys": ["meal-guidance"],
                    "expected_chunk_concepts": ["家常菜", "搭配"],
                    "forbidden_source_keys": [],
                    "expected_outcome": "evidence",
                },
            )
            assert appended.status_code == 200, appended.text
            assert len(appended.json()["dataset"]["cases"]) == 2

            dashboard = await client.get("/api/admin/nutrition-knowledge/dashboard")
            assert dashboard.status_code == 200
            assert dashboard.json()["sources"]["approved"] == 1
