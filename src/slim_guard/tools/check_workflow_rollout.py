"""Read-only gate for a manually reviewed rollout report; never changes config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from slim_guard.rollout import RolloutEvidence, check_rollout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, nargs="?")
    parser.add_argument("--schema", action="store_true", help="Print report JSON schema")
    args = parser.parse_args()
    if args.schema:
        print(json.dumps(RolloutEvidence.model_json_schema(), ensure_ascii=False, indent=2))
        return
    if args.report is None:
        parser.error("report is required unless --schema is supplied")
    try:
        evidence = RolloutEvidence.model_validate_json(args.report.read_text(encoding="utf-8"))
    except (ValidationError, OSError, ValueError):
        # Never echo invalid reports: they may contain private evaluation material.
        print('{"passed": false, "reasons": ["invalid_report"]}')
        raise SystemExit(2) from None
    decision = check_rollout(evidence)
    print(decision.model_dump_json(indent=2))
    raise SystemExit(0 if decision.passed else 1)


if __name__ == "__main__":
    main()
