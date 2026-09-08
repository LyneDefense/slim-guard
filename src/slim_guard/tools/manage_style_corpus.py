"""Offline style preparation CLI; no application database or profile activation.

Run ``python -m slim_guard.tools.manage_style_corpus --help``. Model calls use
STYLE_CORPUS_API_KEY and STYLE_CORPUS_MODEL, independently of production settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from slim_guard.agent_models.zhipu import ZhipuModelGateway
from slim_guard.agents.style.contracts import StyleProfile
from slim_guard.style_corpus import CorpusReview, OfflineStyleCorpus, StyleEvalCase, parse_export
from slim_guard.tools.style_asset_io import write_private_json


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--database", type=Path, required=True, help="Separate offline SQLite file")
    root.add_argument(
        "--output", type=Path, help="New private JSON file under data/ or ignored dir"
    )
    root.add_argument(
        "--use-project-model",
        action="store_true",
        help="Explicitly use project ZHIPU settings instead of standalone STYLE_CORPUS settings",
    )
    commands = root.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Redact and judge exported context/reply pairs")
    importer.add_argument("--input", type=Path, required=True)
    importer.add_argument("--format", choices=("json", "text"), required=True)
    importer.add_argument(
        "--sender-mapping",
        type=Path,
        required=True,
        help='UTF-8 JSON object mapping all senders; identify "章之文" explicitly',
    )
    importer.add_argument("--target-sender", default="章之文")
    importer.add_argument("--private-terms", type=Path, help="JSON string array of names/addresses")
    prepared = commands.add_parser(
        "import-prepared", help="Judge only eligible redacted HTML pairs"
    )
    prepared.add_argument("--input", type=Path, required=True)
    prepared.add_argument("--max-pairs", type=int, default=40)
    prepared.add_argument("--confirm-redacted-inputs", action="store_true")
    proposal = commands.add_parser("propose", help="Produce an unapproved bundle for human review")
    proposal.add_argument("--profile-spec", type=Path, required=True)
    proposal.add_argument("--candidate-ids", type=Path, required=True, help="JSON array of IDs")
    reviewer = commands.add_parser(
        "review", help="List sanitized candidates or append a human review"
    )
    reviewer.add_argument("--candidate-id")
    reviewer.add_argument(
        "--review", type=Path, help="JSON CorpusReview with actor, note, decision and confirmations"
    )
    for command in ("build", "eval", "export"):
        child = commands.add_parser(command)
        child.add_argument("--profile-id", required=True)
        child.add_argument("--version", required=True)
        child.add_argument("--display-name", required=True)
        child.add_argument(
            "--profile-spec", type=Path, help="Explicitly reviewed StyleProfile JSON"
        )
        if command == "eval":
            child.add_argument(
                "--cases",
                type=Path,
                required=True,
                help="JSON array of actual response_plan/styled_response eval cases",
            )
            child.add_argument("--actor", required=True)
    return root


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


async def run(args: argparse.Namespace) -> dict[str, Any]:
    corpus = OfflineStyleCorpus(args.database)
    gateway: ZhipuModelGateway | None = None
    try:
        if args.command == "review":
            if args.candidate_id is None and args.review is None:
                return {
                    "candidates": [item.model_dump(mode="json") for item in corpus.candidates()]
                }
            if args.candidate_id is None or args.review is None:
                raise ValueError("Review requires both --candidate-id and --review")
            corpus.review(args.candidate_id, CorpusReview.model_validate(_load(args.review)))
            return {"candidate_id": args.candidate_id, "review_appended": True}
        if args.command == "propose":
            ids = _load(args.candidate_ids)
            if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
                raise ValueError("Candidate IDs must be a JSON string array")
            bundle = corpus.propose_bundle(
                profile=StyleProfile.model_validate(_load(args.profile_spec)),
                candidate_ids=ids,
            )
            return {"proposal": bundle.model_dump(mode="json"), "human_review_required": True}
        if args.command in {"import", "import-prepared", "eval"}:
            if args.command == "import-prepared" and not args.confirm_redacted_inputs:
                raise ValueError("Review input privacy and pass --confirm-redacted-inputs first")
            api_key = os.environ.get("STYLE_CORPUS_API_KEY", "")
            model = os.environ.get("STYLE_CORPUS_MODEL", "")
            base_url = os.environ.get(
                "STYLE_CORPUS_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
            )
            if args.use_project_model:
                from slim_guard.config import Settings

                settings = Settings()
                api_key, model, base_url = (
                    settings.zhipu_api_key,
                    settings.zhipu_text_model,
                    settings.zhipu_base_url,
                )
            if not api_key.strip() or not model.strip():
                raise ValueError(
                    "Set STYLE_CORPUS_API_KEY and STYLE_CORPUS_MODEL for offline judging"
                )
            gateway = ZhipuModelGateway(
                api_key=api_key,
                base_url=base_url,
                timeout_seconds=60,
            )
        if args.command == "import-prepared":
            from slim_guard.wechat_style_export import PreparedExport

            assert gateway is not None
            candidates = await corpus.import_prepared_pairs(
                PreparedExport.model_validate(_load(args.input)),
                gateway=gateway,
                model=model,
                max_pairs=args.max_pairs,
            )
            return {
                "candidate_ids": [item.candidate_id for item in candidates],
                "status": "pending_human_review",
            }
        if args.command == "import":
            assert gateway is not None
            mapping = _load(args.sender_mapping)
            if not isinstance(mapping, dict) or any(
                not isinstance(key, str) or not isinstance(value, str) or not value.strip()
                for key, value in mapping.items()
            ):
                raise ValueError("Sender mapping must be an object of nonblank strings")
            terms = _load(args.private_terms) if args.private_terms else []
            if not isinstance(terms, list) or any(not isinstance(term, str) for term in terms):
                raise ValueError("Private terms must be a JSON string array")
            candidates = await corpus.import_messages(
                parse_export(args.input.read_text(encoding="utf-8-sig"), format=args.format),
                sender_mapping=mapping,
                gateway=gateway,
                model=model,
                target_sender=args.target_sender,
                private_terms=terms,
            )
            return {
                "candidate_ids": [item.candidate_id for item in candidates],
                "status": "pending_human_review",
            }
        bundle = corpus.build_bundle(
            profile_id=args.profile_id,
            version=args.version,
            display_name=args.display_name,
            profile_override=StyleProfile.model_validate(_load(args.profile_spec))
            if args.profile_spec
            else None,
        )
        if args.command == "eval":
            assert gateway is not None
            raw_cases = _load(args.cases)
            if not isinstance(raw_cases, list):
                raise ValueError("Eval cases must be a JSON array")
            report = await corpus.evaluate(
                bundle,
                tuple(StyleEvalCase.model_validate(item) for item in raw_cases),
                gateway=gateway,
                model=model,
                actor=args.actor,
            )
            return report.model_dump(mode="json")
        if args.command == "build":
            return bundle.model_dump(mode="json")
        return corpus.export_bundle(bundle)
    finally:
        corpus.close()
        if gateway is not None:
            await gateway.close()


def main() -> None:
    args = parser().parse_args()
    result = asyncio.run(run(args))
    if args.output:
        destination = write_private_json(result, args.output)
        print(json.dumps({"output": str(destination), "status": "written"}))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
