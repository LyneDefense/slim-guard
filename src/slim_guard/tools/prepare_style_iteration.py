"""Freeze reviewed A/B decisions and named corrections for one new style version."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from slim_guard.config import DatabaseSettings
from slim_guard.db.session import Database
from slim_guard.style_iteration_sources import collect_style_iteration_sources
from slim_guard.tools.style_asset_io import write_private_json


def _normalized_version(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError(f"{field} must contain 1 to 128 characters")
    return normalized


async def prepare(args: argparse.Namespace) -> dict[str, Any]:
    """Build a private immutable input snapshot; never generate, publish, or activate."""

    if not args.confirm_reviewed_inputs:
        raise ValueError("--confirm-reviewed-inputs is required")
    actor = args.actor.strip()
    if not actor or len(actor) > 128:
        raise ValueError("A real operator actor is required")
    source_version = _normalized_version(
        args.source_profile_version, field="source_profile_version"
    )
    target_version = _normalized_version(
        args.target_profile_version, field="target_profile_version"
    )
    if source_version == target_version:
        raise ValueError("Target profile version must be new")

    database = Database(args.database_url)
    try:
        await database.migrate()
        snapshot = await collect_style_iteration_sources(
            database,
            source_profile_version=source_version,
        )
        return {
            "schema_version": "1",
            "status": "prepared_pending_profile_revision",
            "source_profile_version": source_version,
            "target_profile_version": target_version,
            "actor": actor,
            "prepared_at": datetime.now(UTC).isoformat(),
            "reviewed_inputs_confirmed": True,
            "source_sha256": snapshot["source_sha256"],
            "counts": snapshot["counts"],
            "sources": snapshot["sources"],
            "next_steps": [
                "Derive expression-only rules and examples without copying user facts.",
                "Generate the exact target bundle and scenario-bound regression cases.",
                "Run real-model generation and automated fidelity/style evaluation.",
                "Import the exact passing comparison for named A/B human review.",
            ],
            "published": False,
            "activated": False,
        }
    finally:
        await database.close()


def main() -> None:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--database-url",
        default=DatabaseSettings().database_url,
        help="Application database containing named reviews and corrections",
    )
    command.add_argument("--source-profile-version", required=True)
    command.add_argument("--target-profile-version", required=True)
    command.add_argument("--actor", required=True)
    command.add_argument("--confirm-reviewed-inputs", action="store_true")
    command.add_argument("--output", required=True, type=str)
    args = command.parse_args()
    result = asyncio.run(prepare(args))
    destination = write_private_json(result, Path(args.output))
    print(
        json.dumps(
            {
                "output": str(destination),
                "status": result["status"],
                "source_sha256": result["source_sha256"],
                "counts": result["counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
