"""Sessions: one folder per piece under output/, with the files, names and seeds that belong to it."""
import json
import os
import re
import time
from pathlib import Path

import numpy as np
import torch



OUT_DIR = Path(os.environ.get("MUSIC_AI_OUT") or Path(__file__).parent / "output")   # holds one folder per session
TMP_DIR = OUT_DIR / ".tmp"                    # scratch files (seed melodies, notation intermediates)


# ---- sessions: one folder per project, holding every file it produces -------------------------------------

_session = None  # Path of the current session folder (created lazily on first use)


def slugify(text, limit=40):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-")
    return slug or "untitled"


def _write_meta(path, **fields):
    meta_path = path / "session.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.update(fields)
    meta_path.write_text(json.dumps(meta, indent=2))


def session_info(path):
    path = Path(path)
    try:
        meta = json.loads((path / "session.json").read_text())
    except (OSError, ValueError):
        meta = {}
    files = [f for f in path.iterdir() if f.is_file() and f.suffix in (".mid", ".pdf", ".musicxml")]
    return {"name": meta.get("name") or path.name, "path": str(path), "created": meta.get("created", ""),
            "files": len(files)}


def default_session_name():
    return "Session " + time.strftime("%Y-%m-%d %H:%M")


def _unique_dir(name):
    """output/<name as a folder name>, with -2, -3... added if that folder is taken."""
    path = OUT_DIR / slugify(name)
    k = 2
    while path.exists():
        path = OUT_DIR / f"{slugify(name)}-{k}"
        k += 1
    return path


def drop_if_empty(path):
    """Remove a session folder nothing was ever written to (only its own metadata)."""
    try:
        if path and path.exists() and {f.name for f in path.iterdir()} <= {"session.json"}:
            for f in path.iterdir():
                f.unlink()
            path.rmdir()
    except OSError:
        pass


def new_session(name=None, prompt=""):
    """Start a fresh session folder: output/<name>/. The name defaults to "Session <date> <time>"; the folder is
    named after it, and renaming the session renames the folder."""
    global _session
    drop_if_empty(_session)
    name = (name or "").strip() or default_session_name()
    name = name if len(name) <= 60 else name[:57].rstrip() + "..."
    OUT_DIR.mkdir(exist_ok=True)
    path = _unique_dir(name)
    path.mkdir()
    _write_meta(path, name=name, created=time.strftime("%Y-%m-%d %H:%M:%S"))
    _session = path
    return path


def seed_everything(seed):
    """Make a run repeatable: seed every random number generator the pipeline draws from (Python, NumPy, PyTorch for
    the music model, MLX for the language model)."""
    import random

    random.seed(seed)
    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)
    try:
        import mlx.core as mx

        mx.random.seed(seed)
    except ImportError:
        pass


def current_session():
    return _session


def reset_session():
    """Forget the current session; the next piece of work starts a new folder."""
    global _session
    drop_if_empty(_session)
    _session = None


def ensure_session(prompt=""):
    return _session if _session is not None else new_session(prompt=prompt)


def rename_session(name):
    """Rename the current session and move its folder to match. Returns (old_path, new_path)."""
    global _session
    if _session is None:
        raise RuntimeError("There is no session to rename yet")
    name = name.strip() or default_session_name()
    old = _session
    slug = slugify(name)
    same = old.name == slug or re.fullmatch(re.escape(slug) + r"-\d+", old.name)    # already called that
    new = old if same else _unique_dir(name)
    if new != old:
        old.rename(new)
    _write_meta(new, name=name)
    _session = new
    return old, new


def list_sessions():
    if not OUT_DIR.exists():
        return []
    items = []
    for d in OUT_DIR.iterdir():
        if not (d.is_dir() and (d / "session.json").exists()):
            continue
        info = session_info(d)
        if info["files"] == 0 and d != _session:  # leftovers from a run that failed before writing anything
            drop_if_empty(d)
            continue
        items.append(info)
    return sorted(items, key=lambda i: (i["created"], i["path"]), reverse=True)


def open_session(path):
    global _session
    path = Path(path)
    if OUT_DIR.resolve() not in path.resolve().parents:   # only folders inside the output directory are sessions
        raise ValueError(f"{path} is not inside the output folder")
    if not (path / "session.json").exists():
        raise FileNotFoundError(f"{path} is not a session folder")
    if _session != path:
        drop_if_empty(_session)
    _session = path
    return path
