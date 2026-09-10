"""Offline operator CLI for the governed qualitative dish catalog."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from slim_guard.config import Settings
from slim_guard.db.session import Database
from slim_guard.dish_knowledge import (
    DishCatalogDocument,
    DishCatalogRepository,
    DishCatalogService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import, review, publish, retire, and inspect dish knowledge.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    import_parser = subcommands.add_parser("import", help="Import a JSON manifest as draft")
    import_parser.add_argument("manifest", type=Path)
    import_parser.add_argument("--actor", required=True)

    for command in ("approve", "reject", "publish", "retire"):
        transition = subcommands.add_parser(command)
        transition.add_argument("entity_id")
        transition.add_argument("--reviewer", required=True)
        transition.add_argument(
            "--reason",
            required=command in {"reject", "retire"},
        )

    search = subcommands.add_parser("search", help="Search only published entries")
    search.add_argument("name")
    search.add_argument("--limit", type=int, default=10)

    show = subcommands.add_parser("show", help="Inspect one entry and its review history")
    show.add_argument("entity_id")
    return parser


def _documents_from_manifest(path: Path) -> tuple[DishCatalogDocument, ...]:
    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    documents = payload.get("dishes") if isinstance(payload, dict) else payload
    if not isinstance(documents, list) or not documents:
        raise ValueError("Dish manifest must contain a non-empty dishes array")
    return tuple(DishCatalogDocument.model_validate(item) for item in documents)


async def _run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings.database_url)
    try:
        await database.create_schema()
        repository = DishCatalogRepository(database)
        service = DishCatalogService(repository)
        if arguments.command == "import":
            output: object = asdict(
                await service.import_documents(
                    _documents_from_manifest(arguments.manifest),
                    imported_by=arguments.actor,
                )
            )
        elif arguments.command in {"approve", "reject", "publish", "retire"}:
            transition = getattr(service, arguments.command)
            output = asdict(
                await transition(
                    arguments.entity_id,
                    reviewer=arguments.reviewer,
                    reason=arguments.reason,
                )
            )
        elif arguments.command == "search":
            output = asdict(
                await repository.search_published(arguments.name, limit=arguments.limit)
            )
        else:
            entry = await repository.get_entry(arguments.entity_id)
            output = {
                "entry": asdict(entry) if entry is not None else None,
                "reviews": [
                    asdict(item)
                    for item in await repository.list_reviews(arguments.entity_id)
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
