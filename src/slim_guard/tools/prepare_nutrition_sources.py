"""Acquire official nutrition sources and prepare a review-only RAG draft.

The generated directory belongs under ``data/`` and is intentionally ignored by Git.
This command never imports, approves, or publishes knowledge in the database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator
from pypdf import PdfReader


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$", max_length=128)
    version: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    publisher: str = Field(min_length=1, max_length=256)
    published_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    source_page_url: HttpUrl
    download_url: HttpUrl
    archive_filename: str = Field(min_length=5, max_length=255)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_bytes: int = Field(gt=0)
    rag_pages: tuple[int, ...] = Field(default=())
    rag_start_marker: str | None = Field(default=None, min_length=1, max_length=1_000)
    rag_end_marker: str | None = Field(default=None, min_length=1, max_length=1_000)
    tags: tuple[str, ...] = Field(min_length=1, max_length=64)
    applicability: tuple[str, ...] = Field(min_length=1, max_length=32)
    rights_status: str = Field(pattern=r"^(pending_human_review|approved)$")
    rights_note: str = Field(min_length=1, max_length=2_000)
    scope_note: str = Field(min_length=1, max_length=2_000)

    @field_validator("archive_filename")
    @classmethod
    def validate_archive_filename(cls, value: str) -> str:
        if Path(value).name != value or not value.lower().endswith(".pdf"):
            raise ValueError("archive_filename must be a plain PDF filename")
        return value

    @field_validator("rag_pages")
    @classmethod
    def validate_pages(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(page < 1 for page in value):
            raise ValueError("rag_pages uses one-based positive PDF page numbers")
        if len(value) != len(set(value)) or tuple(sorted(value)) != value:
            raise ValueError("rag_pages must be sorted and unique")
        return value

    @field_validator("tags", "applicability")
    @classmethod
    def validate_labels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not label.strip() or len(label) > 128 for label in value):
            raise ValueError("source labels must contain 1 to 128 characters")
        if len(value) != len(set(value)):
            raise ValueError("source labels must be unique")
        return value

    @model_validator(mode="after")
    def validate_urls(self) -> SourceSpec:
        for value in (self.source_page_url, self.download_url):
            if urlparse(str(value)).scheme != "https":
                raise ValueError("nutrition source URLs must use HTTPS")
        return self

    @model_validator(mode="after")
    def validate_rag_selection(self) -> SourceSpec:
        if not self.rag_pages and (
            self.rag_start_marker is not None or self.rag_end_marker is not None
        ):
            raise ValueError("RAG markers require at least one selected page")
        return self


class SourceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    dataset_version: str = Field(min_length=1, max_length=128)
    sources: tuple[SourceSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_sources(self) -> SourceCatalog:
        identities = tuple((source.source_key, source.version) for source in self.sources)
        filenames = tuple(source.archive_filename for source in self.sources)
        if len(identities) != len(set(identities)):
            raise ValueError("source_key and version pairs must be unique")
        if len(filenames) != len(set(filenames)):
            raise ValueError("archive filenames must be unique")
        return self


@dataclass(frozen=True, slots=True)
class AcquisitionRecord:
    source_key: str
    version: str
    archive_filename: str
    source_page_url: str
    download_url: str
    content_type: str
    byte_count: int
    sha256: str
    acquired_at: str
    reused_local_archive: bool
    rag_pages: tuple[int, ...]
    rag_document: str | None
    rights_status: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download pinned nutrition PDFs and build a human-review RAG draft.",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("resources/nutrition/source-catalog.v1.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/nutrition-knowledge/v1"),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use existing archives and fail instead of accessing the network.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Download again even when the pinned local archive is valid.",
    )
    return parser


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary_path = Path(handle.name)
        handle.write(content)
    temporary_path.replace(path)


def _write_text(path: Path, content: str) -> None:
    _write_bytes(path, content.encode("utf-8"))


def _validate_pdf(content: bytes, source: SourceSpec) -> None:
    if not content.startswith(b"%PDF-"):
        raise ValueError(f"{source.source_key} did not return a PDF")
    actual_hash = _sha256_bytes(content)
    if actual_hash != source.expected_sha256:
        raise ValueError(
            f"{source.source_key} SHA-256 mismatch: "
            f"expected {source.expected_sha256}, got {actual_hash}"
        )
    if len(content) != source.expected_bytes:
        raise ValueError(
            f"{source.source_key} byte count mismatch: "
            f"expected {source.expected_bytes}, got {len(content)}"
        )


def _read_or_download(
    source: SourceSpec,
    archive_path: Path,
    *,
    offline: bool,
    refresh: bool,
    client: httpx.Client,
) -> tuple[bytes, str, bool]:
    if archive_path.exists() and not refresh:
        content = archive_path.read_bytes()
        try:
            _validate_pdf(content, source)
        except ValueError:
            if offline:
                raise
        else:
            return content, "application/pdf", True
    if offline:
        raise FileNotFoundError(f"Pinned archive is missing or invalid: {archive_path}")

    response = client.get(str(source.download_url))
    response.raise_for_status()
    content = response.content
    _validate_pdf(content, source)
    _write_bytes(archive_path, content)
    content_type = response.headers.get("content-type", "application/pdf").split(";", 1)[0]
    return content, content_type, False


_CJK_SPACE = re.compile(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([，。；：！？、）】》])")
_SPACE_AFTER_OPENING = re.compile(r"([（【《])\s+")


def normalize_pdf_text(value: str) -> str:
    """Remove extraction artifacts without paraphrasing the official source."""

    value = value.replace("\r\n", "\n").replace("\r", "\n")
    fragments: list[str] = []
    for raw_line in value.splitlines():
        line = " ".join(raw_line.split())
        line = _CJK_SPACE.sub("", line)
        line = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", line)
        line = _SPACE_AFTER_OPENING.sub(r"\1", line)
        if line:
            fragments.append(line)
    combined = ""
    for fragment in fragments:
        if combined and combined[-1].isascii() and combined[-1].isalnum():
            if fragment[0].isascii() and fragment[0].isalnum():
                combined += " "
        combined += fragment
    combined = _CJK_SPACE.sub("", combined)
    combined = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", combined)
    combined = _SPACE_AFTER_OPENING.sub(r"\1", combined)
    return re.sub(r"(?<=[。！？；])(?=[^）】》])", "\n", combined).strip()


def extract_selected_pages(path: Path, source: SourceSpec) -> str:
    reader = PdfReader(path)
    invalid = [page for page in source.rag_pages if page > len(reader.pages)]
    if invalid:
        raise ValueError(
            f"{source.source_key} requested pages {invalid}, but PDF has {len(reader.pages)} pages"
        )
    extracted_pages: list[tuple[int, str]] = []
    for page_number in source.rag_pages:
        raw_text = reader.pages[page_number - 1].extract_text() or ""
        raw_lines = raw_text.lstrip().splitlines()
        if raw_lines and raw_lines[0].strip() == str(page_number):
            raw_text = "\n".join(raw_lines[1:])
        page_text = normalize_pdf_text(raw_text)
        if not page_text:
            raise ValueError(f"{source.source_key} PDF page {page_number} contains no text")
        extracted_pages.append((page_number, page_text))

    if source.rag_start_marker is not None:
        for index, (page_number, page_text) in enumerate(extracted_pages):
            marker_offset = page_text.find(source.rag_start_marker)
            if marker_offset >= 0:
                extracted_pages = [
                    (page_number, page_text[marker_offset:]),
                    *extracted_pages[index + 1 :],
                ]
                break
        else:
            raise ValueError(f"{source.source_key} RAG start marker was not found")
    if source.rag_end_marker is not None:
        for index in range(len(extracted_pages) - 1, -1, -1):
            page_number, page_text = extracted_pages[index]
            marker_offset = page_text.rfind(source.rag_end_marker)
            if marker_offset >= 0:
                extracted_pages = [
                    *extracted_pages[:index],
                    (
                        page_number,
                        page_text[: marker_offset + len(source.rag_end_marker)],
                    ),
                ]
                break
        else:
            raise ValueError(f"{source.source_key} RAG end marker was not found")

    sections: list[str] = [
        f"# {source.title}",
        "",
        f"来源：{source.publisher}；版本：{source.version}。",
        "以下为指定 PDF 页面的机械文本提取，未作营养学改写，发布前必须与原 PDF 逐页复核。",
    ]
    for page_number, page_text in extracted_pages:
        sections.extend(("", f"## PDF 第 {page_number} 页", "", page_text))
    return "\n".join(sections).strip() + "\n"


def _manifest_document(
    source: SourceSpec,
    *,
    document_path: Path,
    archive_sha256: str,
    acquired_at: str,
) -> dict[str, Any]:
    return {
        "source_key": source.source_key,
        "version": source.version,
        "title": source.title,
        "publisher": source.publisher,
        "published_at": source.published_at,
        "source_url": str(source.source_page_url),
        "content_path": f"./{document_path.as_posix()}",
        "language": "zh-CN",
        "tags": list(source.tags),
        "applicability": list(source.applicability),
        "metadata": {
            "archive_sha256": archive_sha256,
            "download_url": str(source.download_url),
            "extracted_pdf_pages": list(source.rag_pages),
            "extraction_method": "pypdf-mechanical-v1",
            "extracted_at": acquired_at,
            "license_reviewed": source.rights_status == "approved",
            "publish_eligible": source.rights_status == "approved",
            "rights_note": source.rights_note,
            "scope_note": source.scope_note,
        },
    }


def _review_packet(catalog: SourceCatalog, records: list[AcquisitionRecord]) -> str:
    by_key = {record.source_key: record for record in records}
    lines = [
        "# 营养资料发布前人工复核",
        "",
        f"数据集：`{catalog.dataset_version}`",
        "",
        "> 当前清单仅可导入 draft。逐项复核完成并更新来源目录中的 rights_status 后，",
        "> 重新生成清单，才允许进入 approve/publish 流程。",
        "",
    ]
    for source in catalog.sources:
        record = by_key[source.source_key]
        lines.extend(
            (
                f"## {source.title}",
                "",
                f"- Source key：`{source.source_key}`",
                f"- 版本：`{source.version}`",
                f"- 原始文件 SHA-256：`{record.sha256}`",
                f"- RAG 页码：`{', '.join(map(str, source.rag_pages)) or '不进入首批 RAG'}`",
                f"- 授权状态：`{source.rights_status}`",
                f"- 使用范围：{source.scope_note}",
                "- [ ] 原始 URL、发布机构、日期和版本正确",
                "- [ ] 抽取文字已和指定 PDF 页面逐段核对",
                "- [ ] 适用人群与禁用边界正确",
                "- [ ] 已确认允许在本产品的使用方式",
                "- [ ] 同意作为 published RAG（归档资料不勾选）",
                "- 评审人：",
                "- 评审日期：",
                "- 备注：",
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def prepare_sources(
    *,
    catalog_path: Path,
    output_dir: Path,
    offline: bool,
    refresh: bool,
    acquired_at: datetime | None = None,
) -> dict[str, Any]:
    catalog = SourceCatalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))
    destination = output_dir.expanduser().resolve()
    raw_dir = destination / "raw"
    documents_dir = destination / "documents"
    metadata_dir = destination / "metadata"
    raw_dir.mkdir(parents=True, exist_ok=True)
    documents_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (acquired_at or datetime.now(UTC)).astimezone(UTC).isoformat()

    records: list[AcquisitionRecord] = []
    documents: list[dict[str, Any]] = []
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(120.0, connect=20.0),
        headers={"User-Agent": "SlimGuard nutrition source archiver/1.0"},
    ) as client:
        for source in catalog.sources:
            archive_path = raw_dir / source.archive_filename
            content, content_type, reused = _read_or_download(
                source,
                archive_path,
                offline=offline,
                refresh=refresh,
                client=client,
            )
            archive_sha256 = _sha256_bytes(content)
            relative_document: Path | None = None
            if source.rag_pages:
                relative_document = Path("documents") / f"{source.source_key}.md"
                extracted = extract_selected_pages(archive_path, source)
                _write_text(destination / relative_document, extracted)
                documents.append(
                    _manifest_document(
                        source,
                        document_path=relative_document,
                        archive_sha256=archive_sha256,
                        acquired_at=timestamp,
                    )
                )
            records.append(
                AcquisitionRecord(
                    source_key=source.source_key,
                    version=source.version,
                    archive_filename=source.archive_filename,
                    source_page_url=str(source.source_page_url),
                    download_url=str(source.download_url),
                    content_type=content_type,
                    byte_count=len(content),
                    sha256=archive_sha256,
                    acquired_at=timestamp,
                    reused_local_archive=reused,
                    rag_pages=source.rag_pages,
                    rag_document=str(relative_document) if relative_document else None,
                    rights_status=source.rights_status,
                )
            )

    all_publish_eligible = all(
        source.rights_status == "approved" for source in catalog.sources if source.rag_pages
    )
    manifest = {
        "dataset_version": catalog.dataset_version,
        "generated_at": timestamp,
        "publication_gate": {
            "all_documents_publish_eligible": all_publish_eligible,
            "required_action": (
                "none"
                if all_publish_eligible
                else "Complete REVIEW.md; keep every imported source in draft."
            ),
        },
        "documents": documents,
    }
    report = {
        "schema_version": 1,
        "dataset_version": catalog.dataset_version,
        "generated_at": timestamp,
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "sources": [asdict(record) for record in records],
    }
    _write_text(destination / "knowledge-manifest.draft.json", _canonical_json(manifest))
    _write_text(metadata_dir / "acquisition-report.json", _canonical_json(report))
    _write_text(destination / "REVIEW.md", _review_packet(catalog, records))
    return {
        "dataset_version": catalog.dataset_version,
        "archive_count": len(records),
        "draft_document_count": len(documents),
        "all_documents_publish_eligible": all_publish_eligible,
        "output_dir": str(destination),
    }


def main() -> None:
    arguments = _parser().parse_args()
    result = prepare_sources(
        catalog_path=arguments.catalog.expanduser().resolve(),
        output_dir=arguments.output_dir,
        offline=arguments.offline,
        refresh=arguments.refresh,
    )
    print(_canonical_json(result), end="")


if __name__ == "__main__":
    main()
