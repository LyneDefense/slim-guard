from __future__ import annotations

import json
import socket
import stat
import subprocess

import pytest

from slim_guard.tools.prepare_style_export import main, write_prepared_export
from slim_guard.wechat_style_export import PreparedExport, StyleExportError


def prepared():
    return PreparedExport(source_sha256="a" * 64, pairs=(), statistics={"messages": 0})


def test_output_is_private_and_never_overwrites_existing_data(tmp_path):
    path = write_prepared_export(
        prepared(), tmp_path / "data/style/prepared.json", workspace_root=tmp_path
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["privacy_review_required"] is True
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_prepared_export(prepared(), path, workspace_root=tmp_path)
    assert path.read_bytes() == original


def test_output_cannot_escape_workspace_or_use_unignored_location(tmp_path):
    with pytest.raises(StyleExportError, match="outside_workspace"):
        write_prepared_export(prepared(), tmp_path.parent / "outside.json", workspace_root=tmp_path)
    with pytest.raises(StyleExportError, match="gitignored"):
        write_prepared_export(prepared(), tmp_path / "src/private.json", workspace_root=tmp_path)
    with pytest.raises(StyleExportError, match="private_json"):
        write_prepared_export(prepared(), tmp_path / "data/private.html", workspace_root=tmp_path)


def test_output_rejects_symlinked_directory_and_file(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(StyleExportError, match="symlink"):
        write_prepared_export(
            prepared(), tmp_path / "data/link/private.json", workspace_root=tmp_path
        )
    (tmp_path / "data/file.json").symlink_to(tmp_path / "missing.json")
    with pytest.raises(StyleExportError, match="symlink"):
        write_prepared_export(prepared(), tmp_path / "data/file.json", workspace_root=tmp_path)


def test_cli_parse_failure_discloses_neither_input_path_nor_raw_input(tmp_path, capsys):
    source = tmp_path / "PRIVATE-NAME.html"
    source.write_text('<script id="chat-data">PRIVATE RAW CONTENT</script>')
    assert (
        main(
            ["--input", str(source), "--output", "data/prepared.json", "--workspace", str(tmp_path)]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    assert not (tmp_path / "data/prepared.json").exists()


def test_cli_only_writes_redacted_preparation_and_prints_statistics(tmp_path, monkeypatch, capsys):
    def network_forbidden(*args, **kwargs):
        raise AssertionError("Preparation must never access the network")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    rows = [
        {
            "id": index,
            "sender": sender,
            "text": text,
            "type": "text",
            "role": "peer",
            "timestamp": 0,
            "time": "1970-01-01 08:00:00",
            "media": {},
        }
        for index, sender, text in (
            (1, "测试用户", "测试上下文77.6kg"),
            (2, "测试风格源", "@测试用户 收到。"),
        )
    ]
    source = tmp_path / "synthetic.html"
    source.write_text('<script id="chat-data">' + json.dumps({"messages": rows}) + "</script>")
    assert (
        main(
            [
                "--input",
                str(source),
                "--output",
                "data/prepared.json",
                "--workspace",
                str(tmp_path),
                "--target-sender",
                "测试风格源",
            ]
        )
        == 0
    )
    summary = capsys.readouterr().out
    assert json.loads(summary)["status"] == "prepared_pending_review"
    assert "测试上下文" not in summary
    prepared_text = (tmp_path / "data/prepared.json").read_text()
    assert "测试用户" not in prepared_text
    assert "77.6" not in prepared_text
    assert '"eligible_for_judgment": true' in prepared_text
    assert '"privacy_review_required": true' in prepared_text


def test_explicit_gitignored_directory_is_allowed(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("offline-private/\n")
    path = write_prepared_export(
        prepared(), tmp_path / "offline-private/prepared.json", workspace_root=tmp_path
    )
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
