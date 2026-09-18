from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import mimetypes
import socket
from collections.abc import Mapping
from datetime import date
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from slim_guard.agent_models.embeddings import EmbeddingError, EmbeddingGateway
from slim_guard.nutrition_knowledge import (
    KnowledgeDocument,
    NutritionKnowledgeRepository,
    NutritionKnowledgeService,
)
from slim_guard.nutrition_rag.gateways import (
    NutritionModelGatewayError,
)
from slim_guard.nutrition_rag.processing import (
    ChineseNutritionLexicalAnalyzer,
    NutritionDocumentNeedsOcr,
    NutritionDocumentParser,
    NutritionDocumentProcessingError,
    ParentChildNutritionChunker,
)
from slim_guard.nutrition_rag.profiles import DEFAULT_EMBEDDING_PROFILE_ID
from slim_guard.nutrition_rag.repository import (
    NutritionAsset,
    NutritionJob,
    NutritionRagConflict,
    NutritionRagRepository,
)
from slim_guard.nutrition_rag.storage import (
    NutritionObjectIntegrityError,
    NutritionObjectStore,
    NutritionObjectStoreError,
)

logger = logging.getLogger(__name__)


class NutritionEvaluationRunner(Protocol):
    async def execute(
        self,
        job: NutritionJob,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> None: ...

    async def fail(self, job: NutritionJob) -> None: ...


class NutritionRemoteFetchError(RuntimeError):
    pass


class NutritionRemoteDocumentFetcher:
    """Bounded HTTP downloader with redirect-by-redirect SSRF checks."""

    def __init__(
        self,
        *,
        max_bytes: int,
        timeout_seconds: float = 30,
        max_redirects: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": "SlimGuard-NutritionImporter/1.0"},
        )

    async def fetch(self, url: str) -> tuple[bytes, str, str, str]:
        current = url.strip()
        for _ in range(self.max_redirects + 1):
            await self._require_public_http_url(current)
            try:
                async with self._client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise NutritionRemoteFetchError("remote_redirect_missing_location")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    if length and length.isdigit() and int(length) > self.max_bytes:
                        raise NutritionRemoteFetchError("remote_document_too_large")
                    content = bytearray()
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        content.extend(chunk)
                        if len(content) > self.max_bytes:
                            raise NutritionRemoteFetchError("remote_document_too_large")
                    if not content:
                        raise NutritionRemoteFetchError("remote_document_empty")
                    media_type = response.headers.get("content-type", "").split(";", 1)[0]
                    filename = PurePosixPath(urlsplit(str(response.url)).path).name
                    filename = filename[:512] or "remote-document.txt"
                    if not media_type:
                        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    return bytes(content), media_type, filename, str(response.url)
            except NutritionRemoteFetchError:
                raise
            except httpx.HTTPError as error:
                raise NutritionRemoteFetchError("remote_download_failed") from error
        raise NutritionRemoteFetchError("remote_redirect_limit")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    async def _require_public_http_url(url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise NutritionRemoteFetchError("remote_url_invalid")
        if parsed.username or parsed.password or parsed.fragment:
            raise NutritionRemoteFetchError("remote_url_invalid")
        if parsed.port not in {None, 80, 443}:
            raise NutritionRemoteFetchError("remote_url_port_blocked")
        try:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo,
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as error:
            raise NutritionRemoteFetchError("remote_url_dns_failed") from error
        if not addresses:
            raise NutritionRemoteFetchError("remote_url_dns_failed")
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise NutritionRemoteFetchError("remote_url_private_address")


class NutritionKnowledgeIngestionService:
    """Turns immutable COS assets into governed source and vector index records."""

    def __init__(
        self,
        *,
        repository: NutritionRagRepository,
        object_store: NutritionObjectStore,
        embedding_gateway: EmbeddingGateway,
        max_asset_bytes: int,
        legacy_repository: NutritionKnowledgeRepository,
        parser: NutritionDocumentParser | None = None,
        chunker: ParentChildNutritionChunker | None = None,
        analyzer: ChineseNutritionLexicalAnalyzer | None = None,
        remote_fetcher: NutritionRemoteDocumentFetcher | None = None,
    ) -> None:
        self.repository = repository
        self.object_store = object_store
        self.embedding_gateway = embedding_gateway
        self.max_asset_bytes = max_asset_bytes
        self.legacy = NutritionKnowledgeService(legacy_repository)
        self.parser = parser or NutritionDocumentParser()
        self.chunker = chunker or ParentChildNutritionChunker()
        self.analyzer = analyzer or ChineseNutritionLexicalAnalyzer()
        self.remote_fetcher = remote_fetcher

    async def execute_ingest(
        self, job: NutritionJob, *, worker_id: str, lease_seconds: int
    ) -> None:
        data = job.input
        asset = await self._resolve_asset(
            job, data, worker_id=worker_id, lease_seconds=lease_seconds
        )
        await self.repository.update_job_progress(
            job.id,
            worker_id=worker_id,
            stage="downloading",
            message="正在从 COS 读取原始资料",
            completed_items=1,
            total_items=5,
            lease_seconds=lease_seconds,
        )
        content = await self.object_store.get(key=asset.storage_key, max_bytes=self.max_asset_bytes)
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise NutritionObjectIntegrityError("Downloaded COS asset hash mismatch")
        await self.repository.update_job_progress(
            job.id,
            worker_id=worker_id,
            stage="parsing",
            message="正在解析正文和章节",
            completed_items=2,
            total_items=5,
            lease_seconds=lease_seconds,
        )
        document = self.parser.parse(
            content=content,
            media_type=asset.media_type,
            filename=asset.original_filename,
        )
        knowledge_document = KnowledgeDocument(
            source_key=_required(data, "source_key"),
            version=_required(data, "version"),
            title=_required(data, "title"),
            publisher=_required(data, "publisher"),
            content=document.content,
            published_at=_optional_date(data.get("published_at")),
            source_url=data.get("source_url") or asset.source_url,
            language=str(data.get("language") or "zh-CN"),
            tags=_string_tuple(data.get("tags")),
            applicability=_string_tuple(data.get("applicability")),
            metadata={"asset_sha256": asset.sha256, "asset_id": asset.id},
        )
        imported = await self.legacy.import_documents(
            (knowledge_document,), imported_by=job.created_by
        )
        source = imported.documents[0].source
        chunks = self.chunker.split(
            source_content_sha256=source.content_sha256,
            title=source.title,
            sections=document.sections,
            analyzer=self.analyzer,
        )
        child_count = await self.repository.persist_processed_source(
            source_id=source.id,
            asset_id=asset.id,
            document=document,
            chunks=chunks,
            tags=knowledge_document.tags,
            applicability=knowledge_document.applicability,
        )
        await self.repository.update_job_progress(
            job.id,
            worker_id=worker_id,
            stage="embedding",
            message=f"已切分 {child_count} 个检索片段，正在生成向量",
            completed_items=3,
            total_items=5,
            lease_seconds=lease_seconds,
        )
        embedded = 0
        while True:
            batch = await self.repository.list_pending_embeddings(source_id=source.id, limit=64)
            if not batch:
                break
            response = await self.embedding_gateway.embed(
                tuple(item.embedding_text for item in batch)
            )
            await self.repository.save_embeddings(
                items=batch,
                vectors=response.vectors,
                embedding_profile_id=DEFAULT_EMBEDDING_PROFILE_ID,
                dimensions=len(response.vectors[0]),
                provider_request_id=response.request_id,
                prompt_tokens=response.prompt_tokens,
                latency_ms=response.latency_ms,
            )
            embedded += len(batch)
            await self.repository.update_job_progress(
                job.id,
                worker_id=worker_id,
                stage="embedding",
                message=f"已生成 {embedded}/{child_count} 个向量",
                completed_items=3,
                total_items=5,
                lease_seconds=lease_seconds,
            )
        await self.repository.update_job_progress(
            job.id,
            worker_id=worker_id,
            stage="review_ready",
            message="解析和索引已完成，等待人工审核",
            completed_items=4,
            total_items=5,
            lease_seconds=lease_seconds,
        )
        await self.repository.complete_job(
            job.id,
            worker_id=worker_id,
            output={
                "asset_id": asset.id,
                "source_id": source.id,
                "source_sha256": source.content_sha256,
                "retrieval_chunk_count": child_count,
                "embedding_count": embedded,
            },
        )

    async def _resolve_asset(
        self,
        job: NutritionJob,
        data: Mapping[str, Any],
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> NutritionAsset:
        asset_id = data.get("asset_id")
        if isinstance(asset_id, str):
            asset = await self.repository.get_asset(asset_id)
            if asset is None:
                raise NutritionRagConflict("Queued nutrition asset no longer exists")
            return asset
        url = data.get("remote_url")
        if not isinstance(url, str) or self.remote_fetcher is None:
            raise NutritionRagConflict("Ingest job has no usable source asset")
        await self.repository.update_job_progress(
            job.id,
            worker_id=worker_id,
            stage="fetching_url",
            message="正在安全下载远程资料",
            completed_items=0,
            total_items=5,
            lease_seconds=lease_seconds,
        )
        content, media_type, filename, resolved_url = await self.remote_fetcher.fetch(url)
        digest = hashlib.sha256(content).hexdigest()
        key = self.object_store.object_key(sha256=digest, filename=filename)
        stored = await self.object_store.put(
            key=key,
            content=content,
            sha256=digest,
            media_type=media_type,
        )
        return await self.repository.create_asset(
            stored=stored,
            original_filename=filename,
            source_method="url",
            source_url=resolved_url,
            created_by=job.created_by,
        )


class NutritionKnowledgeWorker:
    def __init__(
        self,
        *,
        ingestion: NutritionKnowledgeIngestionService,
        worker_id: str,
        poll_seconds: float,
        lease_seconds: int,
        evaluation: NutritionEvaluationRunner | None = None,
    ) -> None:
        self.ingestion = ingestion
        self.repository = ingestion.repository
        self.worker_id = worker_id
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.evaluation = evaluation

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            processed = await self.run_once()
            if processed:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def run_once(self) -> bool:
        job = await self.repository.claim_job(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds
        )
        if job is None:
            return False
        try:
            if job.job_type == "evaluate" and self.evaluation is not None:
                await self.evaluation.execute(
                    job,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
            elif job.job_type != "ingest":
                raise NutritionRagConflict("This worker does not support the queued job type")
            else:
                await self.ingestion.execute_ingest(
                    job, worker_id=self.worker_id, lease_seconds=self.lease_seconds
                )
        except (
            NutritionModelGatewayError,
            EmbeddingError,
            NutritionObjectStoreError,
            NutritionRemoteFetchError,
            httpx.HTTPError,
        ) as error:
            await self.repository.fail_job(
                job.id,
                worker_id=self.worker_id,
                error_code=type(error).__name__,
                safe_message="外部服务暂时不可用，任务将按策略重试",
                retryable=True,
            )
        except NutritionDocumentNeedsOcr as error:
            await self.repository.fail_job(
                job.id,
                worker_id=self.worker_id,
                error_code=type(error).__name__,
                safe_message="该 PDF 没有足够的可搜索文字，需要先进行 OCR",
                retryable=False,
            )
        except (NutritionDocumentProcessingError, ValueError, NutritionRagConflict) as error:
            await self.repository.fail_job(
                job.id,
                worker_id=self.worker_id,
                error_code=type(error).__name__,
                safe_message=str(error)[:1000] or "资料处理失败",
                retryable=False,
            )
        except Exception as error:
            logger.exception("nutrition_knowledge_job_failed", extra={"job_id": job.id})
            await self.repository.fail_job(
                job.id,
                worker_id=self.worker_id,
                error_code=type(error).__name__,
                safe_message="资料处理发生未预期错误",
                retryable=False,
            )
        latest = await self.repository.get_job(job.id)
        if (
            job.job_type == "evaluate"
            and self.evaluation is not None
            and latest is not None
            and latest.status == "failed"
        ):
            await self.evaluation.fail(job)
        return True


def _required(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("Nutrition labels must be a string list")
    return tuple(item.strip() for item in value if item.strip())


def _optional_date(value: object) -> date | None:
    if value in {None, ""}:
        return None
    if not isinstance(value, str):
        raise ValueError("published_at must be an ISO date")
    return date.fromisoformat(value)


__all__ = [
    "NutritionKnowledgeIngestionService",
    "NutritionKnowledgeWorker",
    "NutritionRemoteDocumentFetcher",
    "NutritionRemoteFetchError",
]
