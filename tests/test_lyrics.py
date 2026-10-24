"""Lyrics to a sung melody: syllables, one note per syllable, and the SoulX-Singer job (the voice itself is not run here)."""
import pytest

import lyrics as L


def test_words_are_split_into_syllables_with_stress():
    line = L.parse("Twinkle little star")[0]
    assert [(w["word"], w["stress"]) for w in line] == [("Twinkle", [1, 0]), ("little", [1, 0]), ("star", [1])]
    assert L.count(line) == 5


def test_an_unknown_word_is_named():
    with pytest.raises(ValueError, match="flurbo"):
        L.parse("a flurbo song")


def test_syllabify_gives_one_vowel_to_each_syllable():
    for w in ("twinkle", "wonder", "diamond", "above", "beautiful"):
        syl = L.syllabify(L.pronounce(w))
        assert [sum(p[-1].isdigit() for p in s) for s in syl] == [1] * len(syl)
        assert [p for s in syl for p in s] == L.pronounce(w)


def test_fit_makes_the_note_count_exact():
    notes = [(60, 0, 1), (62, 1, 1), (64, 2, 2)]
    assert len(L._fit(notes, 2)) == 2 and len(L._fit(notes, 5)) == 5


def test_tidy_removes_overlaps_and_stays_in_the_line():
    out = L._tidy([(60, 0, 3), (62, 1, 3), (64, 7, 4)], 2, 4)
    assert all(a[1] + a[2] <= b[1] + 1e-9 for a, b in zip(out, out[1:])) and out[-1][1] + out[-1][2] <= 8


def test_the_job_has_a_token_for_every_note_and_the_right_timing():
    lines = L.parse("Little star\nHow I wonder")
    mel = [([(60, 0, 1), (62, 1, 1), (64, 2, 2)], 1), ([(60, 0, 1), (60, 1, 1), (62, 2, 1), (64, 3, 1)], 1)]
    segs = L.metadata(lines, mel, 120, "4/4")
    assert [s["time"] for s in segs] == [[0, 2000], [2000, 4000]]
    for s in segs:
        n = len(s["phoneme"].split())
        assert n == len(s["duration"].split()) == len(s["note_pitch"].split()) == len(s["note_type"].split()) == len(s["text"].split())
        assert s["phoneme"].startswith("<SP>") and s["phoneme"].endswith("<SP>")
    assert segs[0]["phoneme"].split()[1] == "en_L-IH1"            # syllable tokens: "lit" of little
    assert segs[0]["note_pitch"].split()[1:4] == ["60", "62", "64"]
