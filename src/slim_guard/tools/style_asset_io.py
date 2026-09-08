"""Private, non-overwriting output for operator-owned offline style artifacts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def write_private_json(value: Any, output: Path, *, workspace: Path | None = None) -> Path:
    root = (workspace or Path.cwd()).resolve(strict=True)
    requested = output if output.is_absolute() else root / output
    if any(path.is_symlink() for path in (requested, *requested.parents) if path != root):
        raise ValueError("Style output must not traverse symlinks")
    destination = requested.resolve()
    try:
        relative = destination.relative_to(root)
    except ValueError:
        raise ValueError("Style output must stay within the workspace") from None
    if destination.suffix != ".json" or len(relative.parts) < 2:
        raise ValueError("Style output must be a private JSON file")
    if relative.parts[0] != "data":
        ignored = (
            subprocess.run(
                ["git", "-C", str(root), "check-ignore", "-q", "--", str(relative)],
                capture_output=True,
                check=False,
                timeout=5,
            ).returncode
            == 0
        )
        if not ignored:
            raise ValueError("Style artifacts must be gitignored")
    serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(serialized)
    return destination
