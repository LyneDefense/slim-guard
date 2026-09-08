"""Synthetic fixtures only: offline tools require deliberate private-data handling."""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path

import pytest

from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1
from slim_guard.style_corpus import StyleAssetBundle
from slim_guard.tools.evaluate_style_asset import run as evaluate_run
from slim_guard.tools.manage_style_corpus import parser
from slim_guard.tools.manage_style_corpus import run as corpus_run
from slim_guard.tools.style_asset_io import write_private_json


def test_private_json_is_owner_only_non_overwriting_and_lossless(tmp_path: Path) -> None:
    payload = {"status": "测试数据", "approved": False}
    path = write_private_json(payload, Path("data/test.json"), workspace=tmp_path)
    assert json.loads(path.read_text()) == payload
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_private_json({"overwrite": True}, path, workspace=tmp_path)
    assert json.loads(path.read_text()) == payload


@pytest.mark.parametrize("name", ["public/test.json", "data/test.txt", "test.json", "../out.json"])
def test_private_json_rejects_unscoped_paths(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        write_private_json({}, Path(name), workspace=tmp_path)


def test_private_json_rejects_symlink_ancestors(tmp_path: Path) -> None:
    (tmp_path / "data").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        write_private_json({}, Path("data/test.json"), workspace=tmp_path)


async def test_prepared_import_requires_privacy_confirmation_before_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(**kwargs: object) -> None:
        raise AssertionError("No gateway may be created before confirmation")

    monkeypatch.setattr("slim_guard.tools.manage_style_corpus.ZhipuModelGateway", forbidden)
    args = parser().parse_args(
        [
            "--database",
            str(tmp_path / "corpus.sqlite"),
            "import-prepared",
            "--input",
            str(tmp_path / "does-not-exist.json"),
        ]
    )
    with pytest.raises(ValueError, match="confirm-redacted-inputs"):
        await corpus_run(args)


async def test_real_evaluation_requires_locally_configured_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STYLE_CORPUS_API_KEY", raising=False)
    monkeypatch.delenv("STYLE_CORPUS_MODEL", raising=False)
    profile = SLIMGUARD_DEFAULT_V1.model_copy(update={"version": "synthetic_candidate_v1"})
    bundle = StyleAssetBundle(source_corpus_sha256="a" * 64, profile=profile, examples=())
    path = tmp_path / "bundle.json"
    path.write_text(bundle.model_dump_json())
    args = argparse.Namespace(
        bundle=path,
        cases=None,
        use_project_model=False,
        database=tmp_path / "corpus.sqlite",
        actor="test-operator",
        confirm_redacted_inputs=True,
    )
    with pytest.raises(ValueError, match="Configure model credentials locally"):
        await evaluate_run(args)
    assert not args.database.exists()


def test_cli_can_build_exact_approved_bundle_before_ab_evaluation(tmp_path: Path) -> None:
    args = parser().parse_args(
        [
            "--database",
            str(tmp_path / "corpus.sqlite"),
            "build",
            "--profile-id",
            "synthetic",
            "--version",
            "synthetic_v1",
            "--display-name",
            "Synthetic",
            "--profile-spec",
            "synthetic.json",
        ]
    )
    assert args.command == "build"
    assert args.profile_spec == Path("synthetic.json")
