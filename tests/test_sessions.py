"""Sessions: creation, rename, containment, and restoring a saved session's files."""
import time

import pytest

import backend
import text2music as t


def test_open_session_refuses_folders_outside_output(out_dir, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere")
    (outside / "session.json").write_text("{}")
    with pytest.raises(ValueError):
        t.open_session(outside)
    with pytest.raises(ValueError):
        t.open_session(out_dir / ".." / outside.name)


def test_open_session_requires_session_json(out_dir):
    (out_dir / "plain").mkdir()
    with pytest.raises(FileNotFoundError):
        t.open_session(out_dir / "plain")


def test_rename_moves_the_folder_and_keeps_files(out_dir):
    s = t.new_session("first name")
    (s / "x-opt1.mid").write_bytes(b"MThd")
    old, new = t.rename_session("second name")
    assert old == s and new != s and not s.exists()
    assert (new / "x-opt1.mid").exists()
    assert t.session_info(new)["name"] == "second name"


def test_two_sessions_with_one_name_get_distinct_folders(out_dir):
    a = t.new_session("same")
    (a / "f.mid").write_bytes(b"x")
    b = t.new_session("same")
    assert a != b


def test_approved_pieces_are_numbered_and_restore_picks_the_latest(out_dir, monkeypatch):
    s = t.new_session("pieces")
    cand = out_dir / "cand.mid"
    cand.write_bytes(b"MThd")
    first = t.approve_part(cand, "Cello")
    time.sleep(0.02)
    second = t.approve_part(cand, "Piano")
    assert first.name.startswith("piece01-") and second.name.startswith("piece02-")

    sent = []
    monkeypatch.setattr(backend, "send", lambda **ev: sent.append(ev))
    monkeypatch.setattr(backend, "notes_of", lambda p: [])
    monkeypatch.setattr(backend, "programs_of", lambda p: {})
    monkeypatch.setattr(backend, "midi_info", lambda p: (90, "4/4", 1.0))
    backend.restore(s)
    done = [e for e in sent if e["type"] == "done"]
    assert done, "a saved session with approved pieces must re-emit its latest piece"
    assert done[-1]["full"].endswith(second.name)
