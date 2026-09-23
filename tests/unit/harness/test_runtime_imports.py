"""Runtime boundaries must import independently of test collection order."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    (
        "slim_guard.harness.runner",
        "slim_guard.harness.preparation",
        "slim_guard.runtime.invocation.core",
        "slim_guard.runtime.invocation.core_result",
        "slim_guard.response_pipeline.processor",
        "slim_guard.runtime.turn",
        "slim_guard.agents.core",
    ),
)
def test_runtime_module_imports_in_fresh_interpreter(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
