from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

source_path = Path("/app/history/history.db")
if not source_path.is_file():
    raise SystemExit("Mem0 history database does not exist")

with tempfile.NamedTemporaryFile(suffix=".db") as temporary_file:
    with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source, sqlite3.connect(
        temporary_file.name,
    ) as destination:
        source.backup(destination)
    temporary_file.seek(0)
    sys.stdout.buffer.write(temporary_file.read())
