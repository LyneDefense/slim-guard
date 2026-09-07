"""Offline operator CLI for the governed nutrition knowledge corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from slim_guard.config import Settings
from slim_guard.db.session import Database
from slim_guard.nutrition_knowledge import (
    KnowledgeDocument,
    NutritionKnowledgeRepository,
    NutritionKnowledgeService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import, review, publish, retire, and inspect nutrition knowledge.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    import_parser = subcommands.add_parser("import", help="Import a JSON manifest as draft")
    import_parser.add_argument("manifest", type=Path)
    import_parser.add_argument("--actor", required=True)

    for command in ("approve", "publish", "retire"):
        transition = subcommands.add_parser(command)
        transition.add_argument("source_id")
        transition.add_argument("--reviewer", required=True)
        transition.add_argument("--reason", required=command == "retire")

    search = subcommands.add_parser("search", help="Audit currently published retrieval")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)

    show = subcommands.add_parser("show", help="Audit a source, including retired history")
    show.add_argument("source_id")
    show.add_argument("--chunk-id")
    return parser


def _documents_from_manifest(path: Path) -> tuple[KnowledgeDocument, ...]:
    manifest_path = path.expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = payload.get("documents") if isinstance(payload, dict) else payload
    if not isinstance(documents, list) or not documents:
        raise ValueError("Knowledge manifest must contain a non-empty documents array")
    parsed: list[KnowledgeDocument] = []
    for index, raw in enumerate(documents):
        if not isinstance(raw, dict):
            raise ValueError(f"Knowledge manifest document {index} must be an object")
        item: dict[str, Any] = dict(raw)
        content_path = item.pop("content_path", None)
        if content_path is not None:
            if "content" in item:
                raise ValueError(f"Knowledge document {index} cannot set content and content_path")
            if not isinstance(content_path, str) or not content_path.strip():
                raise ValueError(f"Knowledge document {index} content_path must be a string")
            resolved_content = (manifest_path.parent / content_path).resolve()
            item["content"] = resolved_content.read_text(encoding="utf-8")
        parsed.append(KnowledgeDocument.model_validate(item))
    return tuple(parsed)


async def _run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings.database_url)
    try:
        await database.create_schema()
        repository = NutritionKnowledgeRepository(database)
        service = NutritionKnowledgeService(repository)
        if arguments.command == "import":
            result = await service.import_documents(
                _documents_from_manifest(arguments.manifest),
                imported_by=arguments.actor,
            )
            output: object = asdict(result)
        elif arguments.command == "approve":
            output = asdict(
                await service.approve_source(
                    arguments.source_id,
                    reviewer=arguments.reviewer,
                    reason=arguments.reason,
                )
            )
        elif arguments.command == "publish":
            output = asdict(
                await service.publish_source(
                    arguments.source_id,
                    reviewer=arguments.reviewer,
                    reason=arguments.reason,
                )
            )
        elif arguments.command == "retire":
            output = asdict(
                await service.retire_source(
                    arguments.source_id,
                    reviewer=arguments.reviewer,
                    reason=arguments.reason,
                )
            )
        elif arguments.command == "search":
            output = await service.search(query=arguments.query, max_results=arguments.limit)
        else:
            source = await repository.get_source(arguments.source_id)
            if source is None:
                output = {"source": None, "chunks": [], "reviews": []}
            else:
                chunks = await repository.list_chunks(source.id)
                if arguments.chunk_id is not None:
                    chunks = tuple(
                        chunk for chunk in chunks if chunk.id == arguments.chunk_id
                    )
                output = {
                    "source": asdict(source),
                    "chunks": [asdict(chunk) for chunk in chunks],
                    "reviews": [
                        asdict(review)
                        for review in await repository.list_reviews(source.id)
                    ],
                }
    finally:
        await database.close()
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parser().parse_args())))


if __name__ == "__main__":
    main()
