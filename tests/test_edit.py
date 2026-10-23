"""Step 3, editing the melody: only the bars the user named may change, however the edit was made."""
import sys
from pathlib import Path

import pytest

import edit
import text2music as t

sys.path.insert(0, str(Path(__file__).parent))
from test_protocol import MELODY, Backend, backend  # noqa: E402,F401


def beats(c):
    """A candidate event's notes as (pitch, start_beat, dur_beats)."""
    k = c["tempo"] / 60
    return [(n[1], round(n[2] * k, 3), round((n[3] - n[2]) * k, 3)) for n in c["notes"]]


def bars_of(notes, bb=4):
    out = {}
    for p, s, d in notes:
        out.setdefault(edit.bar_of(s, bb), []).append((p, round(s, 3), round(d, 3)))
    return out


@pytest.mark.parametrize("text,expected", [
    ("I don't think measure 2 leads into measure 3 very well", [2, 3]),
    ("make bars 3-4 busier", [3, 4]),
    ("measures 1, 3 and 5 are dull", [1, 3, 5]),
    ("bar 2 to 4 should climb", [2, 3, 4]),
    ("bar 99 is odd, and so is bar 2", [2]),
    ("make it better", []),
    ("the second bar", []),
])
def test_the_bars_a_sentence_names(text, expected):
    assert edit.parse_bars(text, 8) == expected


def test_splice_keeps_every_other_bar_exactly():
    old = [(60 + i, float(i), 1.0) for i in range(16)]                  # 4 bars of quarter notes
    new = edit.splice(old, {2: [(72, 0, 2), (74, 2, 2)]}, {2}, 4, 16)
    assert edit.untouched_changes(old, new, {2}, 4) == []
    assert [n for n in new if edit.bar_of(n[1], 4) == 2] == [(72, 4.0, 2.0), (74, 6.0, 2.0)]


def test_a_note_cannot_run_out_of_its_bar_into_one_that_was_not_named():
    old = [(60, 0.0, 4.0), (62, 4.0, 4.0), (64, 8.0, 4.0)]
    new = edit.splice(old, {2: [(70, 0, 16)]}, {2}, 4, 12)
    assert new[1] == (70, 4.0, 4.0) and new[2] == old[2]


def test_a_long_note_from_before_the_edit_is_not_cut():
    old = [(60, 0.0, 6.0), (62, 6.0, 2.0), (64, 8.0, 4.0)]            # the first note rings into bar 2
    new = edit.splice(old, {2: [(65, 0, 1), (67, 2, 1), (69, 3, 1)]}, {2}, 4, 12)
    assert new[0] == old[0]
    assert all(n[1] >= 6.0 for n in new[1:])                           # nothing new starts while it is still sounding


def test_a_target_bar_the_model_skipped_keeps_its_notes():
    old = [(60 + i, float(i), 1.0) for i in range(12)]
    new = edit.splice(old, {2: [(72, 0, 4)]}, {2, 3}, 4, 12)
    assert [n for n in new if edit.bar_of(n[1], 4) == 3] == [n for n in old if edit.bar_of(n[1], 4) == 3]


def test_the_check_catches_a_change_outside_the_named_bars():
    old = [(60, 0.0, 4.0), (62, 4.0, 4.0)]
    assert edit.untouched_changes(old, [(60, 0.0, 4.0), (63, 4.0, 4.0)], {1}, 4) == [2]


def first(backend, **over):
    backend.send(**{**MELODY, "bars": 6, "count": 1, **over})
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    return [e for e in ev if e["type"] == "candidate"][0]


def test_an_llm_edit_changes_the_named_bars_and_nothing_else(backend):
    src = first(backend)
    backend.send(cmd="edit", source_midi=src["path"], instruction="rewrite measure 3 so it is calmer", count=2, seed=5)
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    cands = [e for e in ev if e["type"] == "candidate"]
    assert cands
    old = bars_of(beats(src))
    for c in cands:
        new = bars_of(beats(c))
        assert new[3] != old[3]
        for b in set(old) | set(new):
            if b != 3:
                assert new.get(b) == old.get(b), f"bar {b} changed"


def test_with_no_bars_named_it_asks_instead_of_guessing(backend):
    src = first(backend)
    backend.send(cmd="edit", source_midi=src["path"], instruction="make it better", count=1)
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "error" and "which bars" in ev[-1]["text"]


def test_the_bars_picked_in_the_app_are_used_when_the_text_names_none(backend):
    src = first(backend)
    backend.send(cmd="edit", source_midi=src["path"], instruction="make it calmer", bars=[2], count=1, seed=1)
    ev = backend.until({"melodies_done", "error"})
    c = [e for e in ev if e["type"] == "candidate"][0]
    old, new = bars_of(beats(src)), bars_of(beats(c))
    assert [b for b in set(old) | set(new) if old.get(b) != new.get(b)] == [2]


def test_the_model_wanting_other_bars_is_a_question_not_a_change(backend):
    src = first(backend)
    backend.send(cmd="edit", source_midi=src["path"], instruction="connect measure 2 better", count=1, seed=2)
    ev = backend.until({"melodies_done", "error"})
    sug = [e for e in ev if e["type"] == "edit_suggestion"]
    assert sug and sug[0]["bars"] == [3] and sug[0]["targets"] == [2] and sug[0]["reason"]
    c = [e for e in ev if e["type"] == "candidate"][0]
    old, new = bars_of(beats(src)), bars_of(beats(c))
    assert new.get(3) == old.get(3), "bar 3 is only a suggestion until the user agrees"
    # agreeing: the same edit again with the extra bar named
    backend.send(cmd="edit", source_midi=src["path"], instruction="connect measure 2 better", bars=[2, 3], count=1, seed=2)
    ev = backend.until({"melodies_done", "error"})
    c = [e for e in ev if e["type"] == "candidate"][0]
    new = bars_of(beats(c))
    assert new[3] != old[3] and new.get(4) == old.get(4)


def test_a_hand_edit_is_saved_as_a_new_file(backend):
    src = first(backend)
    notes = beats(src)
    notes[2] = (notes[2][0] + 2, notes[2][1], notes[2][2])                 # one note two semitones up
    backend.send(cmd="save_edit", source_midi=src["path"], notes=[list(n) for n in notes])
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    c = [e for e in ev if e["type"] == "candidate"][0]
    assert c["path"] != src["path"] and Path(c["path"]).exists()
    got = beats(c)
    assert got[2][0] == notes[2][0] and [g[0] for g in got[:2]] == [n[0] for n in notes[:2]]
    assert abs(max(n[3] for n in c["notes"]) - max(n[3] for n in src["notes"])) < 0.5      # the length is kept


def test_a_hand_edit_with_no_notes_is_refused(backend):
    src = first(backend)
    backend.send(cmd="save_edit", source_midi=src["path"], notes=[])
    assert backend.until({"melodies_done", "error"})[-1]["type"] == "error"
