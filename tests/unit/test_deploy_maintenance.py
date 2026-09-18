"""Exercise maintenance ordering and failure behavior without Docker or a server."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("failure", [False, True])
def test_maintenance_stops_old_app_and_never_image_rolls_back_changed_schema(tmp_path, failure):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    root = Path(__file__).resolve().parents[2]
    shutil.copyfile(root / "deploy/scripts/deploy-release.sh", scripts / "deploy-release.sh")
    (scripts / "lib.sh").write_text("""
DEPLOY_DIR="${TEST_DEPLOY_DIR}"
ROOT_DIR="$DEPLOY_DIR"
STATE_DIR="$DEPLOY_DIR/state"
log() { echo "$*"; }
die() { echo "$*"; exit 1; }
require_command() { :; }
flock() { :; }
validate_environment() { :; }
require_data_volumes() { :; }
current_release() { echo old-release; }
env_value() { echo test-image; }
git() { echo new-release; }
docker() { :; }
wait_for_healthy() { :; }
compose() {
  echo "compose $*"
  if [[ "$1" == run && "${TEST_FAIL_MIGRATION:-0}" == 1 ]]; then return 9; fi
}
""")
    for name in ("backup.sh", "smoke-test.sh"):
        path = scripts / name
        path.write_text(f"#!/usr/bin/env bash\necho {name}\n")
        path.chmod(0o700)
    result = subprocess.run(
        ["bash", str(scripts / "deploy-release.sh"), "--skip-build", "--maintenance"],
        env={
            **os.environ,
            "TEST_DEPLOY_DIR": str(tmp_path),
            "TEST_FAIL_MIGRATION": "1" if failure else "0",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.index("backup.sh") < result.stdout.index("stop --timeout 60 app")
    assert result.stdout.index("stop --timeout 60 app") < result.stdout.index("run --rm app")
    if failure:
        assert result.returncode == 9
        assert "NOT restoring incompatible old images" in result.stdout
        assert "up -d app admin-web" not in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "state/previous-release").read_text() == ""
        assert (tmp_path / "state/pre-maintenance-release").read_text().strip() == "old-release"
        assert (tmp_path / "state/current-release").read_text().strip() == "new-release"
