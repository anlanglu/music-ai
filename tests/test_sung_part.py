"""Step 4's singing voice: a sung line over the melody, in the same file, that Extend and Edit leave alone."""
import sys
from pathlib import Path

import pytest

import expand
import lyrics as L
import text2music as t

sys.path.insert(0, str(Path(__file__).parent))
from test_protocol import MELODY, Backend  # noqa: E402,F401

TEXT = "Little star\nHow I wonder what you are\nUp above the world so high\nLike a diamond in the sky"


@pytest.fixture
def backend(tmp_path):
    b = Backend(tmp_path)
    yield b
    b.close()


def melody(backend, bars=8):
    backend.send(**{**MELODY, "bars": bars, "count": 1, "program": 0})
    ev = backend.until({"melodies_done", "error"})
    return [e for e in ev if e["type"] == "candidate"][0]


def sing(backend, src, **over):
    backend.send(**{"cmd": "sing_part", "source_midi": src["path"], "lyrics": TEXT, "bars": 2, "count": 1, "seed": 1, **over})
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    return [e for e in ev if e["type"] == "candidate"][0]


def vocal_notes(c):
    return [n for n in c["notes"] if n[0] == t.VOCAL_CHANNEL]


def test_the_voice_is_a_second_line_over_the_melody_and_the_melody_is_untouched(backend):
    src = melody(backend)
    one = sing(backend, src, voice="en")
    assert len(vocal_notes(one)) == 3                               # "Little star": 3 syllables
    assert [n for n in one["notes"] if n[0] != t.VOCAL_CHANNEL] == [n for n in src["notes"] if n[0] != t.VOCAL_CHANNEL]
    lines = L.used_lines(one["path"], "vocal")
    assert [ln["text"] for ln in lines] == ["Little star"] and lines[0]["start_beat"] == 0 and L.used_lines(one["path"], "melody") == []
    assert expand.read_melody(one["path"])[0] == expand.read_melody(src["path"])[0], "read_melody ignores the singing"
    two = sing(backend, one, voice="zh", voices=["en"], harmony=True)           # more bars of singing, after the first
    lines = L.used_lines(two["path"], "vocal")
    assert lines[1]["start_beat"] == 4.0 and lines[1]["voices"] == ["zh", "en"] and lines[1]["harmony"] is True
    assert lines[0]["voices"] == ["en"], "each stretch keeps its own singers"
    assert vocal_notes(two)[:3] == vocal_notes(one), "the first stretch is kept"


def test_singing_bars_are_chosen_and_cannot_overlap_or_run_past_the_melody(backend):
    src = melody(backend, bars=4)
    sing(backend, src, start_bar=3)
    backend.send(cmd="sing_part", source_midi=src["path"], lyrics=TEXT, bars=2, start_bar=9)
    assert "no bar 9" in backend.until({"melodies_done", "error"})[-1]["text"]
    one = sing(backend, src, start_bar=2)
    backend.send(cmd="sing_part", source_midi=one["path"], lyrics=TEXT, bars=2, start_bar=1)
    assert "already have singing" in backend.until({"melodies_done", "error"})[-1]["text"]


def test_extending_the_melody_keeps_the_singing_and_it_can_be_sung_on_the_new_bars(backend):
    src = melody(backend, bars=4)
    sung = sing(backend, src, bars=2)
    backend.send(cmd="expand", source_midi=sung["path"], bars=4, engine="llm", count=1, program=0, seed=2)
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    longer = [e for e in ev if e["type"] == "candidate"][0]
    assert vocal_notes(longer) == vocal_notes(sung) and len(longer["notes"]) > len(sung["notes"])
    assert L.used_lines(longer["path"], "vocal")[0]["text"] == "Little star"
    again = sing(backend, longer, bars=4)                                          # the next lines, on the new bars
    assert [ln["start_beat"] for ln in L.used_lines(again["path"], "vocal")][:2] == [0, 4.0]


def test_editing_the_singing_keeps_the_melody_and_one_note_per_syllable(backend):
    src = melody(backend, bars=4)
    sung = sing(backend, src, bars=2)
    backend.send(cmd="edit", source_midi=sung["path"], instruction="make it higher", bars=[1], part="vocal", count=1, seed=3)
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    new = [e for e in ev if e["type"] == "candidate"][0]
    assert len(vocal_notes(new)) == len(vocal_notes(sung)) and vocal_notes(new) != vocal_notes(sung)
    assert [n for n in new["notes"] if n[0] != t.VOCAL_CHANNEL] == [n for n in sung["notes"] if n[0] != t.VOCAL_CHANNEL]
    # and the melody can be edited without touching the singing
    backend.send(cmd="edit", source_midi=sung["path"], instruction="calmer", bars=[2], count=1, seed=3)
    ev = backend.until({"melodies_done", "error"})
    new = [e for e in ev if e["type"] == "candidate"][0]
    assert vocal_notes(new) == vocal_notes(sung)


def test_a_hand_edit_of_the_singing_may_not_break_one_note_per_syllable(backend):
    src = melody(backend, bars=4)
    sung = sing(backend, src, bars=2)
    notes = [[n[1], 0.0, 1.0] for n in vocal_notes(sung)][:2]                  # a note short: the line has 3 syllables
    backend.send(cmd="save_edit", source_midi=sung["path"], notes=notes, part="vocal")
    last = backend.until({"melodies_done", "error"})[-1]
    assert last["type"] == "error" and "3 syllables" in last["text"]
    ok = [[n[1] + 1, i * 1.0, 1.0] for i, n in enumerate(vocal_notes(sung))]    # same count, other pitches
    backend.send(cmd="save_edit", source_midi=sung["path"], notes=ok, part="vocal")
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]


def test_the_backing_drops_only_the_voice_so_the_melody_is_mixed_in(backend):
    import mido
    src = melody(backend, bars=4)
    sung = sing(backend, src, bars=2)
    back = L.backing(sung["path"])
    channels = {m.channel for tr in mido.MidiFile(str(back)).tracks for m in tr if m.type == "note_on"}
    assert channels == {0}
