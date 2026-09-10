from __future__ import annotations

from slim_guard.tools.prepare_nutrition_sources import SourceCatalog, normalize_pdf_text


def test_normalize_pdf_text_removes_cjk_extraction_spacing_without_paraphrasing() -> None:
    assert normalize_pdf_text("减 重 期 间 饮 食 要 清 淡 ，\r\n少 油 少 盐。") == (
        "减重期间饮食要清淡，少油少盐。"
    )


def test_source_catalog_rejects_duplicate_identity() -> None:
    source = {
        "source_key": "official-guide",
        "version": "2026",
        "title": "指南",
        "publisher": "发布机构",
        "published_at": "2026-01-01",
        "source_page_url": "https://example.org/notice",
        "download_url": "https://example.org/guide.pdf",
        "archive_filename": "guide.pdf",
        "expected_sha256": "a" * 64,
        "expected_bytes": 100,
        "rag_pages": [1],
        "tags": ["nutrition"],
        "applicability": ["adult"],
        "rights_status": "pending_human_review",
        "rights_note": "等待授权复核",
        "scope_note": "仅用于测试",
    }

    try:
        SourceCatalog.model_validate(
            {
                "schema_version": 1,
                "dataset_version": "test",
                "sources": [source, {**source, "archive_filename": "guide-copy.pdf"}],
            }
        )
    except ValueError as error:
        assert "source_key and version pairs must be unique" in str(error)
    else:
        raise AssertionError("duplicate source identity should fail")
