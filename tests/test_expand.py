"""Step 2, expanding a melody by 4 or 8 bars: the plan grows with it, what was written stays, and both engines work."""
import json
import os
import random
import sys
from pathlib import Path

import pytest

import expand
import plan
import text2music as t

sys.path.insert(0, str(Path(__file__).parent))
from stub_backend import fake_notes  # noqa: E402
from test_protocol import MELODY, Backend  # noqa: E402,F401


def test_extending_a_plan_keeps_the_old_bars_and_adds_new_phrases():
    key = (7, "major")
    base = plan.build_plan(8, "4/4", key, random.Random(1))
    whole, section = plan.extend(base, key, "4/4", 8, 4, rng=random.Random(2))
    assert whole["bars"] == 12 and len(whole["chords"]) == 12 and section["bars"] == 4
    assert whole["chords"][:8] == base["chords"] and whole["chords"][8:] == section["chords"]
    assert [p["start_bar"] for p in whole["phrases"]] == [0, 4, 8]
    assert whole["cadences"][-1]["beat"] == 48.0 and whole["cadences"][-1]["targets"] == [0]
    assert [p["index"] for p in whole["phrases"]] == [0, 1, 2]


def test_a_longer_old_plan_is_cut_back_to_the_melody_that_was_chosen():
    key = (0, "minor")
    longer, _ = plan.extend(plan.build_plan(8, "4/4", key, random.Random(1)), key, "4/4", 8, 8, rng=random.Random(2))
    again, _ = plan.extend(longer, key, "4/4", 8, 4, rng=random.Random(3))      # expanded twice from the 8-bar melody
    assert again["bars"] == 12 and again["chords"][:8] == longer["chords"][:8]


def test_a_melody_without_a_plan_gets_chords_from_its_notes():
    notes = [(60, 0, 4), (64, 4, 4), (67, 8, 4), (72, 12, 4)]        # C, E, G, C: bars of a C major melody
    assert plan.infer_chords(notes, (0, "major"), "4/4", 4) == [0, 0, 0, 0]
    notes = [(67, 0, 2), (71, 2, 2), (74, 4, 4), (67, 8, 4)]         # bar 2 is all D, then G, B: a G chord (the dominant)
    assert plan.infer_chords(notes, (0, "major"), "4/4", 3)[0] == 4
    whole, section = plan.extend(None, (0, "major"), "4/4", 4, 4, notes, random.Random(1))
    assert whole["bars"] == 8 and len(whole["phrases"]) == 2


def test_reading_a_melody_file_gives_back_what_was_written(out_dir):
    notes = fake_notes(4, "3/4", (2, "major"), random.Random(5))
    path = out_dir / "m.mid"
    t.write_melody_midi(path, 96, notes, 73, "3/4", 12)
    got, tempo, sig, program, total = expand.read_melody(path)
    assert (tempo, sig, program) == (96, "3/4", 73) and abs(total - 12) < 0.05
    assert [(p, round(s, 3), round(d, 3)) for p, s, d in got] == [(p, round(s, 3), round(d, 3)) for p, s, d in notes]


@pytest.fixture
def backend(tmp_path):
    b = Backend(tmp_path)
    yield b
    b.close()


def first_melody(backend, **over):
    backend.send(**{**MELODY, "bars": 4, "count": 2, **over})
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    return [e for e in ev if e["type"] == "candidate"]


@pytest.mark.parametrize("bars", [2, 4, 6, 8])
def test_expanding_with_the_llm_adds_bars_and_keeps_the_original(backend, bars):
    chosen = first_melody(backend)[0]
    backend.send(cmd="expand", source_midi=chosen["path"], bars=bars, engine="llm", count=2, complexity=3, seed=3,
                 sliders={"harmony": 5, "endings": 5, "theme": 5}, direction="build to a climax")
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    cands = [e for e in ev if e["type"] == "candidate"]
    assert len(cands) == 2
    old = [(n[1], round(n[2], 2), round(n[3], 2)) for n in chosen["notes"]]
    for c in cands:
        end = max(n[3] for n in c["notes"])
        assert end > chosen["notes"][-1][3] + 0.75 * bars * 4 * 60 / chosen["tempo"] * 0.5     # clearly longer
        assert abs(end - (4 + bars) * 4 * 60 / chosen["tempo"]) < 0.6                          # ... by about `bars`
        assert [(n[1], round(n[2], 2), round(n[3], 2)) for n in c["notes"][:len(old) - 1]] == old[:-1], "the old notes are kept"
    session = json.loads((Path(cands[0]["path"]).parent / "session.json").read_text())
    assert session["plan"]["bars"] == 4 + bars and len(session["plan"]["chords"]) == 4 + bars


def test_expanding_twice_in_a_row(backend):
    chosen = first_melody(backend)[0]
    backend.send(cmd="expand", source_midi=chosen["path"], bars=4, engine="llm", count=1)
    one = [e for e in backend.until({"melodies_done", "error"}) if e["type"] == "candidate"][0]
    backend.send(cmd="expand", source_midi=one["path"], bars=4, engine="llm", count=1)
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    two = [e for e in ev if e["type"] == "candidate"][0]
    assert max(n[3] for n in two["notes"]) > max(n[3] for n in one["notes"]) + 3


def hybrid_available():
    import text2music
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path.home() / ".cache/huggingface/hub")
    return (hub / "models--stanford-crfm--music-small-800k").exists()


@pytest.mark.skipif(not hybrid_available(), reason="the Anticipation model is not in the Hugging Face cache")
def test_expanding_with_the_anticipation_model(backend):
    chosen = first_melody(backend)[0]
    backend.send(cmd="expand", source_midi=chosen["path"], bars=4, engine="hybrid", count=1, complexity=3, seed=3,
                 sliders={"harmony": 5, "endings": 5, "theme": 5, "shape": 8}, direction="calm and spacious")
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    c = [e for e in ev if e["type"] == "candidate"][0]
    assert max(n[3] for n in c["notes"]) > chosen["notes"][-1][3] + 3
    assert len([n for n in c["notes"] if n[1] > chosen["notes"][-1][3]]) >= 4, "there are new notes after the old ones"


def test_expanding_gives_up_after_a_few_unusable_attempts_in_a_row(out_dir, monkeypatch):
    path = out_dir / "m.mid"
    t.write_melody_midi(path, 90, [(60 + i % 5, float(i), 1.0) for i in range(8)], 0, "4/4", 8.0)
    calls = []

    def broken(*a, **k):
        calls.append(1)
        raise ValueError("LLM returned no usable JSON")

    monkeypatch.setattr(t, "compose", broken)
    with pytest.raises(RuntimeError, match="No usable expansion"):
        expand.expand_melody(path, 2, "llm", 4, log=lambda s: None)
    assert len(calls) == t.MAX_FAILED_ATTEMPTS        # not 3 x the number of options
