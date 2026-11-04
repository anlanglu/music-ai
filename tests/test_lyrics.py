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

    def line(parsed, sig, key, llm, level, mood, before, log, chords=None, piano=None, bars=None, scales=None):
        n = L.count(parsed)
        bars = bars or max(1, -(-n // L.SYL_PER_BAR))
        return [(60 + i % 5, round(i * bars * 4 / n * 4) / 4, 1.0) for i in range(n)], bars
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


def test_the_backing_drops_the_voice_melody_and_keeps_the_rest(tmp_path):
    import mido
    m = mido.MidiFile(ticks_per_beat=480)
    voice, cello = mido.MidiTrack(), mido.MidiTrack()
    voice += [mido.Message("program_change", program=L.VOICE_PROGRAM, channel=0), mido.Message("note_on", note=60, velocity=80),
              mido.Message("note_off", note=60, time=480)]
    cello += [mido.Message("program_change", program=42, channel=1), mido.Message("note_on", note=48, channel=1, velocity=80),
              mido.Message("note_off", note=48, channel=1, time=960)]
    m.tracks += [voice, cello]
    m.save(str(tmp_path / "full.mid"))
    out = mido.MidiFile(str(L.backing(tmp_path / "full.mid")))
    notes = [(x.channel, x.type) for t in out.tracks for x in t if x.type.startswith("note")]
    assert notes == [(1, "note_on"), (1, "note_off")]
    m.tracks = [voice]
    m.save(str(tmp_path / "solo.mid"))
    assert L.backing(tmp_path / "solo.mid") is None          # nothing but the voice: play the voice alone


def test_each_voice_has_its_reference_recording_and_notes():
    for key, base in L.VOICE_FILES.items():
        assert base.with_suffix(".wav").exists() and base.with_suffix(".json").exists(), key


def test_chinese_lines_give_one_syllable_and_one_soulx_token_per_character():
    line = L.parse_line("像我这样懦弱的人")
    assert L.count(line) == 8
    assert [w["token"] for w in line][:3] == ["zh_xiang4", "zh_wo3", "zh_zhe4"] and line[6]["token"] == "zh_de5"
    assert L.parse_line("女儿绿")[0]["token"] == "zh_nu:3" or L.parse_line("女")[0]["token"] == "zh_nu:3"   # the umlaut is written u:
    segs = L.metadata([line], [([(60 + i % 3, float(i), 1.0) for i in range(8)], 2)], 90, "4/4")
    assert segs[0]["language"] == "Mandarin" and "zh_xiang4" in segs[0]["phoneme"].split()
    assert L.check("小星星\nLittle star")[0] == [("小星星", 3), ("Little star", 3)]
    assert L.check("星星 abc")[1] == ["abc"]


def test_voices_you_added_are_listed_and_a_name_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "USER_VOICES", tmp_path / "voices")
    d = tmp_path / "voices" / "my-voice"
    d.mkdir(parents=True)
    for f in ("voice.wav", "voice.json"):
        (d / f).write_text("x")
    (d / "voice.txt").write_text('{"name": "My voice", "language": "English"}')
    assert L.voice_list()[-1] == ("u-my-voice", "My voice")
    assert L.voice_base("u-my-voice") == d / "voice" and L.voice_base("u-nobody") is None
    monkeypatch.setattr(L, "prep_ready", lambda language: (True, ""))
    with pytest.raises(RuntimeError, match="already a voice"):
        L.add_voice("whatever.wav", "My voice")


def test_making_a_voice_says_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "PREP_PY", tmp_path / "nope" / "python")
    with pytest.raises(RuntimeError, match="venv-prep"):
        L.add_voice("whatever.wav", "x")


# ---- each extension has its own instrument and singer

def test_the_melody_can_change_instrument_along_the_way(tmp_path):
    import expand
    import text2music as t
    path = tmp_path / "m.mid"
    t.write_melody_midi(path, 90, [(60, 0, 1), (62, 4, 1), (64, 8, 1)], [(0.0, 0), (4.0, 53), (8.0, 73)], "4/4", 12)
    assert expand.read_programs(path) == [(0.0, 0), (4.0, 53), (8.0, 73)]
    assert expand.program_at(expand.read_programs(path), 5.0) == 53
    assert [n[0] for n in expand.read_melody(path)[0]] == [60, 62, 64], "the notes are one line whatever plays them"
    t.write_melody_midi(path, 90, [(60, 0, 1)], 40, "4/4", 4)
    assert expand.read_programs(path) == [(0.0, 40)]


def test_each_extension_stores_its_own_singer_and_the_voice_follows_an_instrument(fake_lines):
    import expand
    first = L.lyric_motifs(TEXT, 1, 53, "4/4", 90, (0, "major"), "7B", 3, "", 1, voice="en")[0]["path"]
    second = L.lyric_extend(first, TEXT, 4, "7B", 3, "", 1, voice="zh")[0]["path"]
    lines = L.used_lines(second)
    assert lines[0]["voice"] == "en" and {ln["voice"] for ln in lines[1:]} == {"zh"}
    assert expand.read_programs(second) == [(0.0, 53)], "the voice carried on: no change of instrument"


def test_a_voice_part_after_another_instrument_and_back_again(fake_lines, monkeypatch):
    import expand
    import text2music as t
    path = fake_lines / "flute.mid"
    t.write_melody_midi(path, 90, [(72, 0, 1), (74, 1, 1), (76, 2, 2)], 73, "4/4", 4)
    sung = L.lyric_extend(path, TEXT, 4, "7B", 3, "", 1, voice="zh")[0]["path"]
    assert expand.read_programs(sung) == [(0.0, 73), (4.0, 53)]
    assert L.used_lines(sung)[0]["start_beat"] == 4.0 and L.used_lines(sung)[0]["voice"] == "zh"
    back = L.backing(sung)           # the flute stays, the voice goes
    kept = [(m.type, m.note) for tr in __import__("mido").MidiFile(str(back)).tracks for m in tr if m.type == "note_on"]
    assert kept == [("note_on", 72), ("note_on", 74), ("note_on", 76)]


def test_singing_runs_once_per_singer_and_joins_them(fake_lines, monkeypatch):
    import wave

    import numpy as np
    first = L.lyric_motifs(TEXT, 1, 53, "4/4", 90, (0, "major"), "7B", 3, "", 1, voice="en")[0]["path"]
    second = L.lyric_extend(first, TEXT, 4, "7B", 3, "", 1, voice="zh")[0]["path"]
    calls = []

    def fake_sing(segments, out_wav, log=print, n_steps=32, voice="en"):
        calls.append((voice, [s["text"] for s in segments]))
        with wave.open(str(out_wav), "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(8000)
            f.writeframes((np.ones(8000, dtype=np.int16) * (1000 if voice == "en" else 2000)).tobytes())
        return out_wav
    monkeypatch.setattr(L, "sing", fake_sing)
    wav = L.sing_midi(second, log=lambda *_: None)
    assert [c[0] for c in calls] == ["en", "zh"] and calls[0][1][0].startswith("<SP> Little")
    assert wav.name.endswith(".voices.wav") and wav.exists()
    assert not list(wav.parent.glob("*.part.wav")), "the per-singer files are removed"
    with wave.open(str(wav)) as f:
        got = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    assert abs(int(got[0]) - 3000) <= 1, "the two singers are added"
    calls.clear()
    L.sing_midi(first, log=lambda *_: None)
    assert [c[0] for c in calls] == ["en"], "one singer: one pass, as before"


# ---- duets and choirs: the same notes, or a harmony part each

def test_harmony_parts_move_along_the_scale_and_stay_in_the_key():
    import text2music as t
    key = (0, "major")
    notes = [(60, 0, 1), (62, 1, 1), (64, 2, 1), (71, 3, 1)]
    assert [n[0] for n in L.harmonize(notes, key, 2)] == [64, 65, 67, 74], "a third above, in the scale: E F G D"
    assert [n[0] for n in L.harmonize(notes, key, -2)] == [57, 59, 60, 67]
    assert L.harmonize(notes, key, 0) == notes
    assert all(p % 12 in t.scale_pcs(key) for p, _, _ in L.harmonize([(61, 0, 1)], key, 2)), "an out-of-scale note is taken into the key"
    assert [n[1:] for n in L.harmonize(notes, key, 4)] == [n[1:] for n in notes], "only the pitches move"


def test_singers_are_listed_once_lead_first_and_capped():
    assert L.singers_of("en", ["zh", "en", "u-a", "u-b", "u-c"]) == ["en", "zh", "u-a", "u-b"]
    assert L.line_singers({"voice": "zh"}) == (["zh"], False)
    assert L.line_singers({}, "en") == (["en"], False)


def test_a_duet_sings_the_same_notes_or_a_harmony_and_the_choice_is_stored(fake_lines, monkeypatch):
    import wave

    import numpy as np
    made = L.lyric_motifs(TEXT, 1, 53, "4/4", 90, (0, "major"), "7B", 3, "", 1, voice="en", voices=["zh"], harmony=True)[0]["path"]
    ln = L.used_lines(made)[0]
    assert ln["voices"] == ["en", "zh"] and ln["harmony"] is True and ln["voice"] == "en"
    calls = {}

    def fake_sing(segments, out_wav, log=print, n_steps=32, voice="en"):
        calls[voice] = [int(x) for x in segments[0]["note_pitch"].split() if int(x)]
        with wave.open(str(out_wav), "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(8000)
            f.writeframes(np.zeros(800, dtype=np.int16).tobytes())
        return out_wav
    monkeypatch.setattr(L, "sing", fake_sing)
    L.sing_midi(made, log=lambda *_: None)
    lead, high = calls["en"], calls["zh"]
    assert len(lead) == len(high) == 3 and all(h > l for l, h in zip(lead, high)), "the second singer is above the lead"
    same = L.lyric_motifs(TEXT, 1, 53, "4/4", 90, (0, "major"), "7B", 3, "", 1, voice="en", voices=["zh"], harmony=False)[0]["path"]
    calls.clear()
    L.sing_midi(same, log=lambda *_: None)
    assert calls["en"] == calls["zh"], "unticked: the same notes"


def test_a_sung_line_is_moved_off_the_pianos_clashes_but_keeps_its_rhythm():
    key = (2, "minor")                                                  # D minor
    piano = [(74, 0.0, 2.0), (72, 2.0, 2.0), (70, 4.0, 2.0), (69, 6.0, 2.0)]
    sung = [(73, 0.0, 1.0), (71, 1.0, 1.0), (71, 2.0, 2.0), (70, 4.0, 1.0), (68, 5.0, 1.0), (70, 6.0, 2.0)]   # mostly seconds and sevenths
    chords = [{2, 5, 9}, {10, 2, 5}]
    out = L.harmonize_to_piano(sung, piano, key, chords, 4.0)
    assert [(s, d) for _, s, d in out] == [(s, d) for _, s, d in sung], "same rhythm, one note per syllable"
    pcs = {(2 + x) % 12 for x in (0, 2, 3, 5, 7, 8, 10)}
    assert all(p % 12 in pcs for p, _, _ in out) and all(57 <= p <= 74 for p, _, _ in out)
    bad = {1, 2, 6, 10, 11}

    def clashes(line):
        return sum(1 for p, s, d in line for q, qs, qd in piano if min(s + d, qs + qd) > max(s, qs) and abs(p - q) % 12 in bad)
    assert clashes(out) < clashes(sung) and clashes(out) == 0, (clashes(sung), clashes(out), out)
