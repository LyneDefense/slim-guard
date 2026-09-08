"""Offline real-model A/B generation; requires credentials and never publishes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from slim_guard.agent_models.zhipu import ZhipuModelGateway
from slim_guard.style_corpus import OfflineStyleCorpus, StyleAssetBundle
from slim_guard.style_evaluation import (
    StyleEvaluationInput,
    generate_style_comparisons,
    synthetic_style_suite,
)
from slim_guard.tools.style_asset_io import write_private_json


async def run(args: argparse.Namespace) -> dict[str, Any]:
    bundle = StyleAssetBundle.model_validate_json(args.bundle.read_text(encoding="utf-8"))
    inputs = (
        tuple(
            StyleEvaluationInput.model_validate(value)
            for value in json.loads(args.cases.read_text(encoding="utf-8"))
        )
        if args.cases
        else synthetic_style_suite()
    )
    key, model, base_url = (
        os.getenv("STYLE_CORPUS_API_KEY", ""),
        os.getenv("STYLE_CORPUS_MODEL", ""),
        os.getenv("STYLE_CORPUS_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    )
    if args.use_project_model:
        from slim_guard.config import Settings

        settings = Settings()
        key, model, base_url = (
            settings.zhipu_api_key,
            settings.zhipu_text_model,
            settings.zhipu_base_url,
        )
    if not key.strip() or not model.strip():
        raise ValueError("Configure model credentials locally before generating evaluations")
    if not args.confirm_redacted_inputs:
        raise ValueError("--confirm-redacted-inputs is required before model calls")
    corpus = OfflineStyleCorpus(args.database)
    gateway = ZhipuModelGateway(api_key=key, base_url=base_url, timeout_seconds=60)
    try:
        return await generate_style_comparisons(
            bundle=bundle,
            inputs=inputs,
            gateway=gateway,
            model=model,
            corpus=corpus,
            actor=args.actor,
            redacted_inputs_confirmed=args.confirm_redacted_inputs,
        )
    finally:
        corpus.close()
        await gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--database", type=Path, required=True, help="Separate offline corpus SQLite"
    )
    parser.add_argument(
        "--cases", type=Path, help="Explicit redacted plans; default is synthetic suite"
    )
    parser.add_argument("--actor", required=True)
    parser.add_argument("--use-project-model", action="store_true")
    parser.add_argument("--confirm-redacted-inputs", action="store_true")
    parser.add_argument("--output", type=Path, help="New private JSON under data/ or ignored dir")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    if args.output:
        destination = write_private_json(result, args.output)
        print(json.dumps({"output": str(destination), "status": "generated_pending_human_review"}))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
