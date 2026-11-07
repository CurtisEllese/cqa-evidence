"""The launcher must work even when inherited Windows settings request cp1251."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_python_launcher_forces_utf8_fixture_and_console(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node is required to exercise the project launcher")
    text = "Клиент: сәлем, рақмет — € ✅"
    fixture = tmp_path / "utf8.json"
    fixture.write_text(text, encoding="utf-8")
    code = ("import sys; from pathlib import Path; "
            "assert sys.flags.utf8_mode == 1; "
            "assert sys.stdout.encoding.lower() == 'utf-8'; "
            "print(Path(sys.argv[1]).read_text())")
    result = subprocess.run(["node", "scripts/python.mjs", "-c", code, str(fixture)],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1251"},
        capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == text
