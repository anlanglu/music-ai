"""A session's history: the steps taken, the options each one offered, and the settings behind it (history.json in the session folder).

The app writes it as it goes. File paths are stored relative to the session folder, so renaming the session does not break
them. A session from before the history was kept gets one rebuilt from its file names (`reconstruct`): the steps and their
options come back, the settings do not.
"""
import json
import re
from pathlib import Path

FILE = "history.json"
VERSION = 1

# the file-name kinds a step leaves behind, and what to call the step
_KINDS = {"": "Motifs", "lyr": "Motifs sung to lyrics", "exp": "Extended", "chords": "Chords", "edit": "Edited", "sing": "Singing"}
_OPTION = re.compile(r"^(\d{6})-(?:(lyr|exp|chords|edit|sing)-)?opt(\d+)\.mid$")
_REPEAT = re.compile(r"^(\d{6})-rep-b(\d+)-(\d+)\.mid$")
_PIECE = re.compile(r"^(piece\d+)-(.*)\.mid$")


def _walk(value, fn):
    """Apply `fn` to every string under "path" keys (and "selected"/"selectedPart") of a history document, in place."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("path", "selected", "selectedPart") and isinstance(v, str):
                value[k] = fn(v)
            else:
                _walk(v, fn)
    elif isinstance(value, list):
        for v in value:
            _walk(v, fn)


def paths_in(data):
    """Every file path a history document refers to."""
    found = []
    _walk(json.loads(json.dumps(data)), lambda p: found.append(p) or p)
    return found


def _relative(folder):
    folder = str(folder).rstrip("/") + "/"
    return lambda p: p[len(folder):] if p.startswith(folder) else p


def _absolute(folder):
    folder = str(folder).rstrip("/") + "/"
    return lambda p: p if p.startswith("/") or not p else folder + p


def save(folder, data):
    folder = Path(folder)
    data = json.loads(json.dumps(data))
    data["version"] = VERSION
    _walk(data, _relative(folder))
    data["discarded"] = [_relative(folder)(p) for p in data.get("discarded", [])]
    (folder / FILE).write_text(json.dumps(data, indent=1))


def load(folder):
    """The saved history with absolute paths, or None. Entries whose files are gone keep their place but lose those options."""
    folder = Path(folder)
    f = folder / FILE
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except ValueError:
        return None
    _walk(data, _absolute(folder))
    data["discarded"] = [_absolute(folder)(p) for p in data.get("discarded", [])]
    return data


def _cand(path, index):
    return {"path": str(path), "index": index, "score": None}


def _entry(title, detail, when, cands):
    return {"id": "", "title": title, "detail": detail, "time": when, "panel": {},
            "snapshot": {"candidates": cands, "selected": None, "parts": [], "selectedPart": None, "partHistory": [], "expansion": []}}


def reconstruct(folder):
    """A history for a session that has none, from its file names: one step per batch of options (the files that share a time
    and a kind), one per repeat, one per approved piece. Returns None when there is nothing to show."""
    folder = Path(folder)
    batches, singles = {}, []
    for f in sorted(folder.glob("*.mid")):
        if f.name.endswith("-backing.mid"):
            continue
        if m := _OPTION.match(f.name):
            batches.setdefault((m.group(1), m.group(2) or ""), []).append((int(m.group(3)), f))
        elif m := _REPEAT.match(f.name):
            singles.append((f.stat().st_mtime, f"Repeated bars {m.group(2)}–{m.group(3)}", "", f))
        elif m := _PIECE.match(f.name):
            singles.append((f.stat().st_mtime, f"{m.group(1).capitalize()}: {m.group(2).replace('-', ' ')}", "approved", f))
    entries = []
    for (_stamp, kind), items in batches.items():
        when = max(f.stat().st_mtime for _, f in items)
        n = len(items)
        entries.append(_entry(_KINDS[kind], f"{n} option{'s' if n != 1 else ''}", when, [_cand(f, i) for i, f in sorted(items)]))
    for when, title, detail, f in singles:
        entries.append(_entry(title, detail, when, [_cand(f, 1)]))
    if not entries:
        return None
    entries.sort(key=lambda e: e["time"])
    for i, e in enumerate(entries, 1):
        e["id"] = f"rebuilt-{i}"
    return {"version": VERSION, "current": entries[-1]["id"], "discarded": [], "entries": entries, "rebuilt": True}


def delete_option(folder, path):
    """Delete an option's MIDI file and the files made from it (sheet music, lyrics, sung audio, backing...): everything in its
    folder named like it. Only files inside the session folder are touched. Returns the names removed."""
    folder, path = Path(folder).resolve(), Path(path).resolve()
    if path.suffix != ".mid" or folder not in path.parents or not path.exists():
        return []
    removed = []
    for f in path.parent.iterdir():
        if f.is_file() and (f == path or (f.name.startswith(path.stem) and len(f.name) > len(path.stem) and f.name[len(path.stem)] in ".-")):
            f.unlink()
            removed.append(f.name)
    return removed
