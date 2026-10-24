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


# ---- the lyrics are kept apart from the melody: only as many lines as fit are used, and the next extension carries on

@pytest.fixture
def fake_lines(monkeypatch, tmp_path):
    """line_melody replaced by one note per syllable, one bar per four syllables; the session goes to a temp folder."""
    import text2music as t

    def line(parsed, sig, key, llm, level, mood, before, log):
        n = L.count(parsed)
        bars = max(1, -(-n // L.SYL_PER_BAR))
        return [(60 + i % 5, float(i), 1.0) for i in range(n)], bars
    monkeypatch.setattr(L, "line_melody", line)
    monkeypatch.setattr(t, "OUTPUT_DIR", tmp_path / "out", raising=False)
    monkeypatch.setattr(t, "SESSION_DIR", None, raising=False)
    t.new_session("lyrictest")
    return tmp_path


TEXT = "Little star\nHow I wonder what you are\nUp above the world so high\nLike a diamond in the sky"


def test_a_motif_uses_only_the_lines_that_fit_and_remembers_them(fake_lines):
    small = L.lyric_motifs(TEXT, 2, 0, "4/4", 90, (0, "major"), "7B", 3, "", 2)
    assert len(small) == 2
    assert [u["text"] for u in L.used_lines(small[0]["path"])] == ["Little star"]         # 1 bar; the 9-syllable line needs 3
    big = L.lyric_motifs(TEXT, 4, 0, "4/4", 90, (0, "major"), "7B", 3, "", 1)
    assert [u["text"] for u in L.used_lines(big[0]["path"])] == ["Little star", "How I wonder what you are"]


def test_extending_carries_on_with_the_next_line_and_stops_when_the_lyrics_run_out(fake_lines):
    first = L.lyric_motifs(TEXT, 1, 0, "4/4", 90, (0, "major"), "7B", 3, "", 1)[0]["path"]
    assert [u["text"] for u in L.used_lines(first)] == ["Little star"]
    second = L.lyric_extend(first, TEXT, 4, "7B", 3, "", 1)[0]["path"]
    lines = L.used_lines(second)
    assert [u["text"] for u in lines][0] == "Little star" and len(lines) >= 2
    assert lines[1]["start_beat"] == 4.0                                                  # after the motif's one bar
    done = second
    for _ in range(4):
        r = L.lyric_extend(done, TEXT, 8, "7B", 3, "", 1)
        if r is None:
            break
        done = r[0]["path"]
    assert len(L.used_lines(done)) == 4 and L.lyric_extend(done, TEXT, 4, "7B", 3, "", 1) is None


def test_unpronounceable_words_are_reported_by_the_check_not_raised():
    rows, unknown = L.check("Little star\nthe flurbo song")
    assert rows[0] == ("Little star", 3) and rows[1][1] is None and unknown == ["flurbo"]
