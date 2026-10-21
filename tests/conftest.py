"""Shared test setup. Tests never touch the real output/ folder: MUSIC_AI_OUT points at a temp directory before
text2music is imported, and nothing here needs a model download (the LLM and the music model are never loaded)."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "anticipation"))
_OUT = tempfile.mkdtemp(prefix="composer-test-out-")
os.environ["MUSIC_AI_OUT"] = _OUT

import pytest  # noqa: E402


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    """A fresh output folder for one test, with the module's session state reset."""
    import text2music as t

    monkeypatch.setattr(t, "OUT_DIR", tmp_path)
    monkeypatch.setattr(t, "TMP_DIR", tmp_path / ".tmp")
    monkeypatch.setattr(t, "_session", None)
    return tmp_path


def pytest_addoption(parser):
    parser.addoption("--full", action="store_true", help="run the exhaustive (slow) invariant sweep")
