"""Runs the client-side rules' tests (logic.test.js) with Node, so `pytest` covers them too."""

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_logic_js():
    r = subprocess.run(["node", "--test", str(HERE / "logic.test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
