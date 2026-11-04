"""The command-line entry points start up and describe themselves (a broken option list crashes --help)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("script", ["text2music.py", "lyrics.py"])
def test_help_runs(script):
    r = subprocess.run([sys.executable, str(ROOT / script), "--help"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    assert "usage" in r.stdout.lower()


def test_every_module_imports_on_its_own():
    """The split into modules must not depend on which one is imported first."""
    for mod in ["config", "sessions", "models", "melody_llm", "keys", "midifiles", "parts", "text2music", "backend",
                "expand", "edit", "lyrics", "notation", "texture", "harmony", "plan"]:
        r = subprocess.run([sys.executable, "-c", f"import {mod}"], capture_output=True, text=True, cwd=ROOT, timeout=120)
        assert r.returncode == 0, f"{mod}: {r.stderr[-400:]}"
