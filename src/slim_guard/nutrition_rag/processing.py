from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

import jieba  # type: ignore[import-untyped]
from pypdf import PdfReader

from slim_guard.nutrition_rag.profiles import (
    CHUNKER_PROFILE_KEY,
    NUTRITION_LEXICON_SHA256,
    NUTRITION_LEXICON_V1,
)


class NutritionDocumentProcessingError(RuntimeError):
    pass


class NutritionDocumentNeedsOcr(NutritionDocumentProcessingError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedSection:
    ordinal: int
    heading_path: tuple[str, ...]
    page_from: int | None
    page_to: int | None
    content: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ParsedNutritionDocument:
    media_type: str
    content: str
    content_sha256: str
    sections: tuple[ParsedSection, ...]


@dataclass(frozen=True, slots=True)
class NutritionChunkDraftV2:
    id: str
    section_ordinal: int
    parent_chunk_id: str | None
    chunk_kind: str
    ordinal: int
    page_from: int | None
    page_to: int | None
    content: str
    content_sha256: str
    lexical_text: str
    lexical_terms: str
    token_count: int
    char_count: int
    embedding_text: str


class ChineseNutritionLexicalAnalyzer:
    profile_key = "jieba-nutrition-zh-cn-v1"
    dictionary_sha256 = NUTRITION_LEXICON_SHA256

    def __init__(self) -> None:
        tokenizer = jieba.Tokenizer()
        for term in NUTRITION_LEXICON_V1:
            tokenizer.add_word(term, freq=2_000_000)
        self._tokenizer = tokenizer

    def analyze(self, text: str) -> tuple[str, str]:
        normalized = normalize_text(text).casefold()
        terms: list[str] = []
        for raw in self._tokenizer.cut(normalized, cut_all=False, HMM=False):
            term = raw.strip()
            if _is_search_term(term):
                terms.append(term)
        for sequence in re.findall(r"[\u3400-\u9fff]{2,}", normalized):
            if len(sequence) <= 24:
                terms.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
        deduplicated = tuple(dict.fromkeys(terms))
        return normalized, " ".join(deduplicated)


class NutritionDocumentParser:
    _TEXT_TYPES = {
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "application/markdown",
    }
    _HTML_TYPES = {"text/html", "application/xhtml+xml"}

    def parse(
        self,
        *,
        content: bytes,
        media_type: str,
        filename: str,
    ) -> ParsedNutritionDocument:
        if not content:
            raise NutritionDocumentProcessingError("empty_document")
        normalized_type = media_type.split(";", 1)[0].strip().casefold()
        suffix = filename.casefold().rsplit(".", 1)[-1] if "." in filename else ""
        if normalized_type == "application/pdf" or suffix == "pdf":
            return self._parse_pdf(content)
        if normalized_type in self._HTML_TYPES or suffix in {"html", "htm"}:
            return self._parse_html(content)
        if normalized_type in self._TEXT_TYPES or suffix in {"txt", "md", "markdown"}:
            return self._parse_text(content, media_type=normalized_type or "text/plain")
        raise NutritionDocumentProcessingError("unsupported_media_type")

    def _parse_pdf(self, content: bytes) -> ParsedNutritionDocument:
        try:
            reader = PdfReader(io.BytesIO(content))
            pages = tuple(normalize_text(page.extract_text() or "") for page in reader.pages)
        except Exception as error:
            raise NutritionDocumentProcessingError("pdf_parse_failed") from error
        searchable_chars = sum(len(re.sub(r"\s", "", page)) for page in pages)
        if not pages or searchable_chars < max(80, len(pages) * 20):
            raise NutritionDocumentNeedsOcr("pdf_needs_ocr")
        sections: list[ParsedSection] = []
        for page_number, page in enumerate(pages, start=1):
            if not page:
                continue
            heading = _first_heading(page) or f"第 {page_number} 页"
            sections.append(
                ParsedSection(
                    ordinal=len(sections),
                    heading_path=(heading,),
                    page_from=page_number,
                    page_to=page_number,
                    content=page,
                    content_sha256=_sha256(page),
                )
            )
        normalized = normalize_text("\n\n".join(section.content for section in sections))
        return ParsedNutritionDocument(
            media_type="application/pdf",
            content=normalized,
            content_sha256=_sha256(normalized),
            sections=tuple(sections),
        )

    def _parse_html(self, content: bytes) -> ParsedNutritionDocument:
        text = _decode_text(content)
        parser = _VisibleHtmlParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception as error:
            raise NutritionDocumentProcessingError("html_parse_failed") from error
        return self._parse_text(parser.text().encode(), media_type="text/html")

    def _parse_text(self, content: bytes, *, media_type: str) -> ParsedNutritionDocument:
        normalized = normalize_text(_decode_text(content))
        if len(normalized) < 20:
            raise NutritionDocumentProcessingError("document_too_short")
        sections = _split_sections(normalized)
        return ParsedNutritionDocument(
            media_type=media_type,
            content=normalized,
            content_sha256=_sha256(normalized),
            sections=sections,
        )


class ParentChildNutritionChunker:
    profile_key = CHUNKER_PROFILE_KEY

    def __init__(
        self,
        *,
        child_target_chars: int = 520,
        child_max_chars: int = 700,
        parent_max_chars: int = 1_800,
    ) -> None:
        if not 128 <= child_target_chars <= child_max_chars <= parent_max_chars <= 5000:
            raise ValueError("Invalid nutrition chunk size configuration")
        self.child_target_chars = child_target_chars
        self.child_max_chars = child_max_chars
        self.parent_max_chars = parent_max_chars

    def split(
        self,
        *,
        source_content_sha256: str,
        title: str,
        sections: tuple[ParsedSection, ...],
        analyzer: ChineseNutritionLexicalAnalyzer,
    ) -> tuple[NutritionChunkDraftV2, ...]:
        drafts: list[NutritionChunkDraftV2] = []
        ordinal = 0
        for section in sections:
            for parent_index, parent_text in enumerate(
                _boundary_chunks(section.content, maximum=self.parent_max_chars)
            ):
                parent_id = _chunk_id(
                    source_content_sha256,
                    section.ordinal,
                    parent_index,
                    "context_parent",
                    parent_text,
                )
                lexical_text, lexical_terms = analyzer.analyze(parent_text)
                heading = " / ".join(section.heading_path)
                drafts.append(
                    NutritionChunkDraftV2(
                        id=parent_id,
                        section_ordinal=section.ordinal,
                        parent_chunk_id=None,
                        chunk_kind="context_parent",
                        ordinal=ordinal,
                        page_from=section.page_from,
                        page_to=section.page_to,
                        content=parent_text,
                        content_sha256=_sha256(parent_text),
                        lexical_text=lexical_text,
                        lexical_terms=lexical_terms,
                        token_count=_token_count(parent_text),
                        char_count=len(parent_text),
                        embedding_text=_embedding_text(title, heading, parent_text),
                    )
                )
                ordinal += 1
                for child_index, child_text in enumerate(
                    _boundary_chunks(
                        parent_text,
                        target=self.child_target_chars,
                        maximum=self.child_max_chars,
                    )
                ):
                    lexical_text, lexical_terms = analyzer.analyze(child_text)
                    drafts.append(
                        NutritionChunkDraftV2(
                            id=_chunk_id(
                                source_content_sha256,
                                section.ordinal,
                                (parent_index * 10_000) + child_index,
                                "retrieval_child",
                                child_text,
                            ),
                            section_ordinal=section.ordinal,
                            parent_chunk_id=parent_id,
                            chunk_kind="retrieval_child",
                            ordinal=ordinal,
                            page_from=section.page_from,
                            page_to=section.page_to,
                            content=child_text,
                            content_sha256=_sha256(child_text),
                            lexical_text=lexical_text,
                            lexical_terms=lexical_terms,
                            token_count=_token_count(child_text),
                            char_count=len(child_text),
                            embedding_text=_embedding_text(title, heading, child_text),
                        )
                    )
                    ordinal += 1
        if not any(draft.chunk_kind == "retrieval_child" for draft in drafts):
            raise NutritionDocumentProcessingError("no_retrieval_chunks")
        return tuple(drafts)


class _VisibleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._heading_level: int | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if self._ignored_depth:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._heading_level = int(tag[1])
            self._parts.append("\n" + ("#" * self._heading_level) + " ")
        elif tag in {"p", "div", "section", "article", "li", "tr", "br"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if not self._ignored_depth and re.fullmatch(r"h[1-6]", tag):
            self._heading_level = None
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]
    compact: list[str] = []
    blank = False
    for line in lines:
        if line:
            compact.append(line)
            blank = False
        elif compact and not blank:
            compact.append("")
            blank = True
    return "\n".join(compact).strip()


def _split_sections(content: str) -> tuple[ParsedSection, ...]:
    headings: list[str] = []
    current_heading = "正文"
    current_lines: list[str] = []
    sections: list[ParsedSection] = []

    def flush() -> None:
        body = normalize_text("\n".join(current_lines))
        if not body:
            return
        sections.append(
            ParsedSection(
                ordinal=len(sections),
                heading_path=tuple(headings) or (current_heading,),
                page_from=None,
                page_to=None,
                content=body,
                content_sha256=_sha256(body),
            )
        )

    for line in content.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            flush()
            current_lines = []
            level = len(match.group(1))
            current_heading = match.group(2)[:256]
            headings = headings[: level - 1]
            headings.append(current_heading)
        else:
            current_lines.append(line)
    flush()
    if not sections:
        sections.append(
            ParsedSection(
                ordinal=0,
                heading_path=("正文",),
                page_from=None,
                page_to=None,
                content=content,
                content_sha256=_sha256(content),
            )
        )
    return tuple(sections)


def _boundary_chunks(text: str, *, maximum: int, target: int | None = None) -> tuple[str, ...]:
    target = target or maximum
    paragraphs = tuple(item.strip() for item in re.split(r"\n\s*\n", text) if item.strip())
    units: list[str] = []
    for paragraph in paragraphs or (text,):
        if len(paragraph) <= maximum:
            units.append(paragraph)
            continue
        cursor = 0
        while cursor < len(paragraph):
            hard_end = min(cursor + maximum, len(paragraph))
            end = hard_end
            if hard_end < len(paragraph):
                window_start = min(cursor + max(64, target // 2), hard_end)
                for mark in ("。", "！", "？", "；", ".", "!", "?", ";", "，", ","):
                    candidate = paragraph.rfind(mark, window_start, hard_end)
                    if candidate >= window_start:
                        end = max(end if end < hard_end else 0, candidate + 1)
                if end <= cursor:
                    end = hard_end
            units.append(paragraph[cursor:end].strip())
            cursor = end
    chunks: list[str] = []
    current = ""
    for unit in units:
        combined = unit if not current else current + "\n\n" + unit
        if current and len(combined) > maximum:
            chunks.append(current)
            current = unit
        else:
            current = combined
        if len(current) >= target:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return tuple(chunk for chunk in chunks if chunk)


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise NutritionDocumentProcessingError("text_encoding_unsupported")


def _first_heading(page: str) -> str | None:
    for line in page.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if 2 <= len(candidate) <= 80 and not re.search(r"[。！？.!?]$", candidate):
            return candidate
    return None


def _embedding_text(title: str, heading: str, content: str) -> str:
    return normalize_text(f"资料：{title}\n章节：{heading}\n{content}")[:12_000]


def _is_search_term(term: str) -> bool:
    if not term or term.isspace():
        return False
    if re.fullmatch(r"[\u3400-\u9fff]+", term):
        return len(term) >= 2
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._%+-]*", term)) and len(term) >= 2


def _token_count(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    other = len(re.findall(r"[A-Za-z0-9]+", text))
    return max(1, cjk + other)


def _chunk_id(
    source_hash: str,
    section_ordinal: int,
    local_ordinal: int,
    kind: str,
    content: str,
) -> str:
    identity = (
        f"{source_hash}:{CHUNKER_PROFILE_KEY}:{section_ordinal}:"
        f"{local_ordinal}:{kind}:{_sha256(content)}"
    )
    return "nutrition-rag-" + _sha256(identity)[:48]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "ChineseNutritionLexicalAnalyzer",
    "NutritionChunkDraftV2",
    "NutritionDocumentNeedsOcr",
    "NutritionDocumentParser",
    "NutritionDocumentProcessingError",
    "ParentChildNutritionChunker",
    "ParsedNutritionDocument",
    "ParsedSection",
    "normalize_text",
]
