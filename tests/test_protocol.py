"""The real backend process, driven over its JSON-lines protocol (theory engine: no model downloads)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


class Backend:
    def __init__(self, out):
        env = dict(os.environ, MUSIC_AI_OUT=str(out))
        self.p = subprocess.Popen([sys.executable, "-u", "backend.py"], cwd=ROOT, env=env, text=True,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.ready = self.until({"ready"})[0]

    def send(self, **req):
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()

    def until(self, stops):
        events = []
        while True:
            line = self.p.stdout.readline()
            assert line, "backend exited unexpectedly"
            events.append(json.loads(line))
            if events[-1]["type"] in stops:
                return events

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=30)


@pytest.fixture
def backend(tmp_path):
    b = Backend(tmp_path)
    yield b
    b.close()


MELODY = dict(cmd="melodies", prompt="a bright tune", bars=4, program=73, sig="4/4", complexity=3, count=3,
              melody_engine="theory", bpm=100, key=[7, "major"], phrasing=1, shape=1, seed=11)


def test_theory_melodies_come_back_as_candidates(backend):
    backend.send(**MELODY)
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    cands = [e for e in ev if e["type"] == "candidate"]
    assert len(cands) == 3
    for c in cands:
        assert Path(c["path"]).exists() and c["tempo"] == 100 and c["sig"] == "4/4" and c["notes"]


def test_a_seed_makes_a_run_repeatable(backend):
    runs = []
    for _ in range(2):
        backend.send(**MELODY)
        ev = backend.until({"melodies_done", "error"})
        runs.append([[(n[1], round(n[2], 3)) for n in e["notes"]] for e in ev if e["type"] == "candidate"])
    assert runs[0] == runs[1]


def test_unknown_session_path_is_an_error_not_a_crash(backend, tmp_path):
    backend.send(cmd="session_open", path=str(tmp_path.parent))
    assert backend.until({"error"})[-1]["type"] == "error"
    backend.send(cmd="session_list")                        # and the backend is still alive
    assert backend.until({"sessions"})[-1]["type"] == "sessions"


def test_the_ready_event_carries_the_presets_the_sliders_start_from(backend):
    presets = backend.ready["presets"]
    assert set(presets["form"]) == {"0", "1", "2", "3"}
    assert set(presets["shape"]) == {"0", "1", "2"}
    assert presets["defaults"]["chord"] > 0
    assert presets["labels"]["form"][0] == "Free"


def test_a_request_can_turn_the_knobs(backend):
    backend.send(**MELODY, form=3, knobs={"motif": 0.0, "cadence": 0.0})
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    backend.send(**MELODY, form=3, knobs={"motif": 20.0, "cadence": 15.0})
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]


def test_a_nonsense_knob_is_ignored_rather_than_crashing(backend):
    backend.send(**MELODY, form=3, knobs={"nonsense": 5, "motif": None})
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]


def test_round_trip_create_approve_rename_reopen(backend):
    backend.send(**MELODY)
    ev = backend.until({"melodies_done"})
    first = [e for e in ev if e["type"] == "candidate"][0]
    session = [e for e in ev if e["type"] == "session"][-1]

    backend.send(cmd="approve_part", path=first["path"], label="Flute")
    approved = backend.until({"part_approved", "error"})[-1]
    assert approved["type"] == "part_approved" and Path(approved["path"]).name.startswith("piece01-")

    backend.send(cmd="session_rename", name="Renamed piece")
    renamed = [e for e in backend.until({"session"}) if e["type"] == "session"][-1]
    assert renamed["name"] == "Renamed piece" and renamed["path"] != session["path"]
    assert Path(renamed["path"]).is_dir() and not Path(session["path"]).exists()

    backend.send(cmd="session_new")
    backend.until({"session"})
    backend.send(cmd="session_open", path=renamed["path"])
    ev = backend.until({"melodies_done", "error"})          # restore() always ends its options with melodies_done
    backend.send(cmd="session_list")                         # ... and anything after that comes before this reply
    ev += backend.until({"sessions"})[:-1]
    kinds = [e["type"] for e in ev]
    assert "error" not in kinds
    assert kinds.count("candidate") == 3, "the melody options come back"
    done = [e for e in ev if e["type"] == "done"]
    assert done and "piece01-" in done[-1]["full"], "the approved piece comes back"


def test_the_app_sends_one_shape_slider_and_the_plan_strengths(backend):
    """What the app's sliders send: shape_amount and knobs, with no form or shape of their own."""
    req = {k: v for k, v in MELODY.items() if k != "shape"}
    for amount, knobs in [(0.0, {"chord": 0.0, "cadence": 0.0, "motif": 0.0, "beat": 0.5}),
                          (1.4, {"chord": 2.2, "cadence": 5.0, "motif": 0.0, "beat": 0.5}),
                          (2.0, {"chord": 2.2, "cadence": 5.0, "motif": 8.0, "beat": 3.0})]:
        backend.send(**req, shape_amount=amount, knobs=knobs)
        ev = backend.until({"melodies_done", "error"})
        assert ev[-1]["type"] == "melodies_done", (amount, ev[-1])
