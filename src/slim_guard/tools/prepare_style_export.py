"""Prepare local de-identified WeChat pairs without model calls or corpus approval."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from slim_guard.wechat_style_export import (
    MAX_EXPORT_BYTES,
    PreparedExport,
    StyleExportError,
    prepare_html_export,
)


def write_prepared_export(prepared: PreparedExport, output: Path, *, workspace_root: Path) -> Path:
    """Create a new 0600 JSON file inside data/ or a git-ignored workspace location."""
    root = workspace_root.resolve(strict=True)
    requested = output if output.is_absolute() else root / output
    if any(parent.is_symlink() for parent in (requested, *requested.parents) if parent != root):
        raise StyleExportError("symlink_output_not_allowed")
    destination = requested.resolve()
    try:
        relative = destination.relative_to(root)
    except ValueError:
        raise StyleExportError("output_outside_workspace") from None
    if destination.suffix.lower() != ".json" or len(relative.parts) < 2:
        raise StyleExportError("output_must_be_private_json")
    if relative.parts[0] != "data":
        try:
            ignored = (
                subprocess.run(
                    ["git", "-C", str(root), "check-ignore", "-q", "--", str(relative)],
                    capture_output=True,
                    check=False,
                    timeout=5,
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            ignored = False
        if not ignored:
            raise StyleExportError("output_must_be_gitignored")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # O_EXCL prevents overwriting an existing file (including a symlink at final open).
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(prepared.model_dump_json(indent=2))
        stream.write("\n")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Local UTF-8 HTML export")
    parser.add_argument(
        "--output", type=Path, required=True, help="New JSON under data/ or ignored dir"
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--target-sender", default="章之文")
    parser.add_argument("--timezone-offset-hours", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        if args.input.stat().st_size > MAX_EXPORT_BYTES:
            raise StyleExportError("invalid_export_size")
        with args.input.open("rb") as stream:
            html = stream.read(MAX_EXPORT_BYTES + 1)
        prepared = prepare_html_export(
            html, target_sender=args.target_sender, timezone_offset_hours=args.timezone_offset_hours
        )
        write_prepared_export(prepared, args.output, workspace_root=args.workspace)
    except StyleExportError as error:
        print(json.dumps({"status": "failed", "error_code": str(error)}))
        return 2
    except (OSError, ValueError):
        print(json.dumps({"status": "failed", "error_code": "local_preparation_failed"}))
        return 2
    print(
        json.dumps(
            {
                "status": "prepared_pending_review",
                "source_sha256": prepared.source_sha256,
                "statistics": prepared.statistics,
                "exclusion_counts": prepared.exclusion_counts,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
