"""A session's history on disk: saved with relative paths, rebuilt from file names for older sessions, sent to the app on open."""
import mido

import journal
from test_protocol import MELODY, Backend, backend  # noqa: F401


def _mid(path):
    m = mido.MidiFile(ticks_per_beat=480)
    t = m.add_track()
    t.append(mido.MetaMessage("set_tempo", tempo=500000))
    t.append(mido.Message("note_on", note=60, velocity=80, time=0))
    t.append(mido.Message("note_off", note=60, velocity=0, time=480))
    m.save(path)


def test_paths_are_stored_relative_and_come_back_absolute(tmp_path):
    f = tmp_path / "s"
    f.mkdir()
    cand = {"path": str(f / "a-opt1.mid"), "index": 1, "score": None}
    data = {"current": "e1", "discarded": [str(f / "a-opt2.mid")],
            "entries": [{"id": "e1", "title": "Motifs", "detail": "", "time": 1.0, "panel": {"bars": 4},
                         "snapshot": {"candidates": [cand], "selected": cand["path"], "parts": [], "selectedPart": None,
                                      "partHistory": [{"before": cand, "label": "x"}], "expansion": [{"candidates": [cand], "selected": None}]}}]}
    journal.save(f, data)
    on_disk = (f / "history.json").read_text()
    assert str(f) not in on_disk and "a-opt1.mid" in on_disk
    back = journal.load(f)
    assert back["entries"][0]["snapshot"]["selected"] == cand["path"] and back["discarded"] == [str(f / "a-opt2.mid")]
    assert back["entries"][0]["panel"] == {"bars": 4}
    g = tmp_path / "renamed"
    f.rename(g)                                                    # renaming the session moves the folder; the paths follow
    assert journal.load(g)["entries"][0]["snapshot"]["candidates"][0]["path"] == str(g / "a-opt1.mid")
    assert sorted(set(journal.paths_in(back))) == [cand["path"]]


def test_an_old_session_gets_a_history_from_its_file_names(tmp_path):
    for n in ("101010-opt1.mid", "101010-opt2.mid", "111111-exp-opt1.mid", "121212-edit-opt1.mid", "121212-sing-opt1.mid",
              "121212-sing-opt1-backing.mid", "131313-rep-b5-8.mid", "piece01-flute.mid"):
        _mid(tmp_path / n)
    data = journal.reconstruct(tmp_path)
    titles = [(e["title"], len(e["snapshot"]["candidates"])) for e in data["entries"]]
    assert ("Motifs", 2) in titles and ("Extended", 1) in titles and ("Edited", 1) in titles and ("Singing", 1) in titles
    assert any(t.startswith("Repeated bars 5") for t, _ in titles) and any(t.startswith("Piece01") for t, _ in titles)
    assert not any("backing" in p for p in journal.paths_in(data))
    assert journal.reconstruct(tmp_path / "nothing") is None


def test_opening_a_session_sends_its_history_with_the_facts_about_each_file(backend):  # noqa: F811
    backend.send(**MELODY)
    ev = backend.until({"melodies_done"})
    first = [e for e in ev if e["type"] == "candidate"][0]
    session = [e for e in ev if e["type"] == "session"][-1]
    cand = {"path": first["path"], "index": 1, "score": None}
    backend.send(cmd="history_save", data={"current": "e1", "discarded": [], "entries": [
        {"id": "e1", "title": "Motifs", "detail": "", "time": 1.0, "panel": {},
         "snapshot": {"candidates": [cand], "selected": first["path"], "parts": [], "selectedPart": None, "partHistory": [], "expansion": []}}]})
    backend.send(cmd="session_new")
    backend.until({"session"})
    backend.send(cmd="session_open", path=session["path"])
    ev = backend.until({"melodies_done"})
    h = [e for e in ev if e["type"] == "history"][0]
    assert h["data"]["entries"][0]["title"] == "Motifs" and h["data"]["entries"][0]["snapshot"]["selected"] == first["path"]
    info = h["files"][first["path"]]
    assert info["notes"] and info["tempo"] and info["sig"] == "4/4"


def test_discarding_deletes_the_option_and_what_was_made_from_it_but_nothing_else(tmp_path):
    f = tmp_path / "s"
    (f / "parts").mkdir(parents=True)
    keep = ["101010-opt10.mid", "101010-opt2.mid", "history.json", "session.json"]
    gone = ["101010-opt1.mid", "101010-opt1.html", "101010-opt1.musicxml", "101010-opt1.lyrics.json", "101010-opt1-backing.mid",
            "101010-opt1.u-ann.wav"]
    for n in keep + gone:
        (f / n).write_bytes(b"x")
    outside = tmp_path / "outside-opt1.mid"
    outside.write_bytes(b"x")
    assert journal.delete_option(f, outside) == [] and outside.exists()                    # not in the session
    assert journal.delete_option(f, f / "history.json") == [] and (f / "history.json").exists()      # not a melody file
    assert sorted(journal.delete_option(f, f / "101010-opt1.mid")) == sorted(gone)
    assert sorted(p.name for p in f.iterdir() if p.is_file()) == sorted(keep)
    assert journal.delete_option(f, f / "101010-opt1.mid") == []                           # already gone
