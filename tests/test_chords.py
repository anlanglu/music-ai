"""Chords written by the user: the names, the plan that holds them, the chord instruments that play them, the commands."""
import sys
from pathlib import Path

import pytest

import expand
import harmony as h
import plan
import text2music as t
import texture as tx

sys.path.insert(0, str(Path(__file__).parent))
from test_protocol import MELODY, Backend  # noqa: E402,F401


@pytest.fixture
def backend(tmp_path):
    b = Backend(tmp_path)
    yield b
    b.close()


@pytest.mark.parametrize("name,notes", [
    ("Dm7", "D F A C"), ("Cmaj9", "C E G B D"), ("G7#9", "G B D F Bb"), ("F#dim7", "F# A C Eb"), ("Csus4", "C F G"),
    ("C6/9", "C E G A D"), ("Am7/G", "A C E G"), ("C/E", "C E G"), ("E5", "E B"), ("Bbm7b5", "Bb C# E Ab"),
])
def test_chord_names_are_read_into_their_notes(name, notes):
    assert h.parse(name).notes() == notes


def test_a_progression_has_bars_and_half_bar_changes():
    assert h.parse_progression("Dm7 G7 | Cmaj7 | % | Am7 D7") == [["Dm7", "G7"], ["Cmaj7"], ["Cmaj7"], ["Am7", "D7"]]
    assert h.parse_progression("Dm G C") == [["Dm"], ["G"], ["C"]]
    for bad, why in [("Cxyz", "can't read"), ("H7", "note name"), ("C | D E F", "two is the most"), ("Cmaj", "number")]:
        with pytest.raises(ValueError, match=why):
            h.parse_progression(bad)


def test_cm_sharp_nine_says_the_ninth_is_the_minor_third():
    c = h.parse("Cm#9")
    assert c.notes() == "C Eb G" and "same note" in c.remark and h.parse("C7#9").remark == ""


def test_the_sampler_gets_a_region_for_each_half_bar():
    p = plan.build_plan(4, "4/4", (2, "dorian"))
    plan.set_harmony(p, [["Dm7", "G7"], ["Cmaj7"]])
    assert p["harmony"] == [["Dm7", "G7"], ["Cmaj7"], ["Dm7", "G7"], ["Cmaj7"]], "a short progression repeats to fill the bars"
    regions = plan.plan_regions(p, 120)
    assert len(regions) == 6
    (s0, e0, a), (s1, e1, b) = regions[0], regions[1]
    assert e0 == s1 and e1 - s0 == round(4 * 50) and a == {2, 5, 9, 0} and b == {7, 11, 2, 5}
    assert p["chords"][0] == 0 and p["chords"][1] == 6, "the degree view is the nearest scale degree of each bar's first chord"


def test_chords_the_mode_lacks_are_flagged():
    p = plan.build_plan(2, "4/4", (2, "dorian"))
    plan.set_harmony(p, [["Dm7"], ["A", "Gm"]])
    fit = plan.chord_fit(p)
    assert fit[0] == [[]]                                         # D dorian has every note of Dm7
    assert fit[1][0] == ["C#"] and fit[1][1] == ["Bb"], fit      # A major's C# and G minor's Bb are not in D dorian
    assert plan.plan_harmony_pcs(p) >= {1, 10}


def test_a_chord_with_a_seventh_is_voiced_with_its_seventh_and_follows_the_half_bar():
    p = plan.build_plan(2, "4/4", (2, "minor"))
    plan.set_harmony(p, [["Dm7", "G7"], ["Cmaj7"]])
    out = tx.build([(60, 0.0, 8.0, 0)], 100, "4/4", (2, "minor"), "sustained", 0, 0, None, plan.bar_chords(p), lead=True)
    first = {pitch % 12 for pitch, s, d in out if s == 0.0}
    second = {pitch % 12 for pitch, s, d in out if s == 2.0}
    assert 0 in first and 5 in first and 9 in first, first          # C, F, A: the seventh and third of Dm7
    assert 5 in second and 11 in second, second                       # F and B: the seventh and third of G7


def test_triads_of_scale_degrees_still_work_for_the_instruments():
    out = tx.build([(60, 0.0, 4.0, 0)], 100, "4/4", (0, "major"), "block", 0, 0, None, [0], lead=True)
    assert {p % 12 for p, _, _ in out} >= {0, 4, 7}


def test_set_chords_shows_what_each_bar_is_and_extend_follows_them(backend):
    backend.send(**{**MELODY, "bars": 4, "count": 1, "mode": "dorian", "key": [2, "dorian"]})
    chosen = [e for e in backend.until({"melodies_done", "error"}) if e["type"] == "candidate"][0]
    backend.send(cmd="set_chords", source_midi=chosen["path"], progression="Dm7 G7 | A | Dm7 | Cmaj7")
    ev = backend.until({"chords", "error"})[-1]
    assert ev["type"] == "chords", ev
    assert [[c["symbol"] for c in b] for b in ev["bars"]] == [["Dm7", "G7"], ["A"], ["Dm7"], ["Cmaj7"]]
    assert ev["written"] and [c["outside"] for c in ev["bars"][1]] == [["C#"]]
    assert ev["bars"][0][0]["notes"] == "D F A C"
    backend.send(cmd="chords", source_midi=chosen["path"])
    again = backend.until({"chords", "error"})[-1]
    assert again["bars"] == ev["bars"], "the chords are kept in the session"
    backend.send(cmd="expand", source_midi=chosen["path"], bars=4, chords=True, program=0, count=1, seed=2,
                 progression="Am7 | Dm7 | G7 | Cmaj7")
    done = backend.until({"melodies_done", "error"})
    assert done[-1]["type"] == "melodies_done", done[-1]
    cand = [e for e in done if e["type"] == "candidate"][0]
    backend.send(cmd="chords", source_midi=cand["path"])
    whole = backend.until({"chords", "error"})[-1]
    assert [[c["symbol"] for c in b][0] for b in whole["bars"]] == ["Dm7", "A", "Dm7", "Cmaj7", "Am7", "Dm7", "G7", "Cmaj7"]


def test_a_chord_that_cannot_be_read_is_an_error_not_a_crash(backend):
    backend.send(**{**MELODY, "bars": 4, "count": 1})
    chosen = [e for e in backend.until({"melodies_done", "error"}) if e["type"] == "candidate"][0]
    backend.send(cmd="set_chords", source_midi=chosen["path"], progression="Dm7 | Qm")
    assert "Qm" in backend.until({"chords", "error"})[-1]["text"]


def test_the_motif_is_given_the_chords_and_keeps_them(backend):
    backend.send(**{**MELODY, "bars": 4, "count": 1, "progression": "Cm7 | Fm7 | G7 | Cm"})
    chosen = [e for e in backend.until({"melodies_done", "error"}) if e["type"] == "candidate"][0]
    backend.send(cmd="chords", source_midi=chosen["path"])
    ev = backend.until({"chords", "error"})[-1]
    assert [b[0]["symbol"] for b in ev["bars"]] == ["Cm7", "Fm7", "G7", "Cm"]


def test_modes_by_bar_are_read_from_text():
    assert h.parse_modes("major*3 lydian", plan.MODE_ORDER) == ["major", "major", "major", "lydian"]
    assert h.parse_modes("- lydian", plan.MODE_ORDER) == ["", "lydian"]
    with pytest.raises(ValueError, match="isn't a mode"):
        h.parse_modes("majr", plan.MODE_ORDER)


def test_a_bar_with_its_own_mode_has_its_own_scale_and_chord_check():
    p = plan.build_plan(4, "4/4", (0, "major"))
    plan.set_modes(p, ["", "", "", "lydian"])
    assert plan.bar_modes(p) == ["major", "major", "major", "lydian"]
    at = plan.scale_pcs_at(p)
    assert 6 not in at(0.0) and 6 in at(13.0), "F# only in the lydian bar"
    plan.set_harmony(p, [["C"], ["C"], ["C"], ["D"]])
    assert plan.chord_fit(p)[3] == [[]], "D major (F#) belongs in lydian"
    plan.set_modes(p, [])
    assert plan.scale_pcs_at(p) is None and plan.chord_fit(p)[3] == [["F#"]], "... and not in C major"


def test_the_raised_fourth_stays_out_of_the_major_bars_and_is_brought_into_the_lydian_one():
    p = plan.build_plan(4, "4/4", (0, "major"))
    plan.set_modes(p, ["", "", "", "lydian"])
    notes = [(66 if i in (1, 5, 9) else 64, float(i), 1.0) for i in range(16)]          # F# in the first three bars, E in the last
    out = t.finish_melody(notes, 4, "4/4", 0, (0, "major"), False, 0, {"flavor": 10, "strict": 10}, p)
    assert all(pitch % 12 != 6 for pitch, s, _ in out if s < 12), "bars 1-3 are C major"
    assert any(pitch % 12 == 6 for pitch, s, _ in out if s >= 12), "bar 4 gets its raised 4th"


def test_the_sampler_is_given_each_bars_scale():
    p = plan.build_plan(4, "4/4", (0, "major"))
    assert plan.scale_regions(p, 120) is None
    plan.set_modes(p, ["", "", "", "lydian"])
    r = plan.scale_regions(p, 120)
    assert len(r) == 4 and 6 not in r[0][2] and 6 in r[3][2] and r[0][1] == r[1][0]


def test_set_modes_is_kept_and_shown_and_extend_adds_modes_for_the_new_bars(backend):
    backend.send(**{**MELODY, "bars": 4, "count": 1, "key": [0, "major"], "mode": "major", "modes": "major*3 lydian",
                    "progression": "C | C | C | D"})
    chosen = [e for e in backend.until({"melodies_done", "error"}) if e["type"] == "candidate"][0]
    backend.send(cmd="chords", source_midi=chosen["path"])
    ev = backend.until({"chords", "error"})[-1]
    assert ev["modes"] == ["major", "major", "major", "lydian"] and ev["labels"][3] == "C lydian"
    assert all(c["outside"] == [] for b in ev["bars"] for c in b), "D is in C lydian"
    backend.send(cmd="set_modes", source_midi=chosen["path"], modes="")
    cleared = backend.until({"chords", "error"})[-1]
    assert cleared["modes"] == ["", "", "", ""] and cleared["bars"][3][0]["outside"] == ["F#"]
    backend.send(cmd="set_modes", source_midi=chosen["path"], bar_modes=["", "", "", "dorian"])
    assert backend.until({"chords", "error"})[-1]["labels"][3] == "C dorian"
    backend.send(cmd="expand", source_midi=chosen["path"], bars=4, count=1, engine="llm", seed=2, modes="- - lydian -")
    done = backend.until({"melodies_done", "error"})
    assert done[-1]["type"] == "melodies_done", done[-1]
    cand = [e for e in done if e["type"] == "candidate"][0]
    backend.send(cmd="chords", source_midi=cand["path"])
    assert backend.until({"chords", "error"})[-1]["modes"] == ["", "", "", "dorian", "", "", "lydian", ""]
