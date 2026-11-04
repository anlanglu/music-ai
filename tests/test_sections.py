"""Accompaniment kept as layers inside the melody file, for the bars asked for: added, cleared, and kept by Extend and Edit."""
import sys
from pathlib import Path

import mido
import pytest

import expand
import text2music as t

sys.path.insert(0, str(Path(__file__).parent))
from test_protocol import MELODY, Backend  # noqa: E402,F401


@pytest.fixture
def backend(tmp_path):
    b = Backend(tmp_path)
    yield b
    b.close()


def piece(tmp_path, bars=8):
    path = tmp_path / "m.mid"
    notes = [(60 + i % 5, float(i), 1.0) for i in range(bars * 4)]
    t.write_melody_midi(path, 90, notes, 0, "4/4", bars * 4, vocal=[(67, 0.0, 1.0), (69, 1.0, 1.0)])
    return path


def cello(lo, hi):
    return [(48 + i % 3, float(i), 1.0) for i in range(lo, hi)]


def test_layers_are_added_for_their_bars_and_the_rest_of_the_file_is_untouched(tmp_path):
    src = piece(tmp_path)
    a = tmp_path / "a.mid"
    t.add_layers_file(src, a, {42: cello(0, 32)}, 0.0, 16.0)                      # bars 1-4 only
    assert t.layers_of(a) == [{"program": 42, "channel": 2, "notes": 16, "from_bar": 1, "to_bar": 4}]
    assert expand.read_melody(a)[0] == expand.read_melody(src)[0] and expand.read_vocal(a) == expand.read_vocal(src)
    assert expand.read_programs(a) == [(0.0, 0)], "an accompaniment layer is not the melody's instrument"
    b = tmp_path / "b.mid"
    t.add_layers_file(a, b, {40: cello(16, 32), 128: [(36, 16.0, 0.5), (36, 20.0, 0.5)]}, 16.0, 32.0)
    got = {(x["program"], x["from_bar"], x["to_bar"], x["channel"]) for x in t.layers_of(b)}
    assert got == {(42, 1, 4, 2), (40, 5, 8, 3), (128, 5, 6, 9)}, "the cello is kept; drums go on channel 10"
    c = tmp_path / "c.mid"
    t.add_layers_file(b, c, {42: cello(16, 24)}, 16.0, 24.0)                      # the cello again, later: it joins its layer
    assert [(x["from_bar"], x["to_bar"], x["notes"]) for x in t.layers_of(c) if x["program"] == 42] == [(1, 6, 24)]


def test_clearing_bars_takes_the_accompaniment_out_of_them_only(tmp_path):
    src = piece(tmp_path)
    full = tmp_path / "f.mid"
    t.add_layers_file(src, full, {42: cello(0, 32), 40: cello(0, 32)}, 0.0, 32.0)
    out = tmp_path / "o.mid"
    assert t.clear_layers_file(full, out, 8.0, 16.0) > 0                          # bars 3-4, every instrument
    for layer in t.layers_of(out):
        assert layer["notes"] == 24
    notes = [n for tr in mido.MidiFile(str(out)).tracks if t.is_acc_track(tr) for n in t._track_notes(tr)]
    assert not [n for n in notes if 8 * 480 <= n[0] < 16 * 480] and expand.read_melody(out)[0] == expand.read_melody(src)[0]
    only = tmp_path / "p.mid"
    t.clear_layers_file(full, only, 0.0, 32.0, [40])                              # one instrument, everywhere: it is gone
    assert [x["program"] for x in t.layers_of(only)] == [42]
    cut = tmp_path / "q.mid"
    t.add_layers_file(src, cut, {42: [(48, 6.0, 4.0)]})                          # a note that runs into the range is cut
    t.clear_layers_file(cut, tmp_path / "r.mid", 8.0, 12.0)
    assert [n[:2] for tr in mido.MidiFile(str(tmp_path / "r.mid")).tracks if t.is_acc_track(tr) for n in t._track_notes(tr)] == [[2880, 960]]


def test_an_instrument_cannot_play_twice_in_the_same_bars_but_can_in_others(tmp_path):
    src = piece(tmp_path)
    a = tmp_path / "a.mid"
    t.add_layers_file(src, a, {42: cello(0, 16)}, 0.0, 16.0)
    assert t.part_range(a, 42, 5, 8)[2:] == (5, 8)
    with pytest.raises(RuntimeError, match="already plays in bars 3-6"):
        t.part_range(a, 42, 3, 6)
    assert t.part_range(a, 40, None, None)[2:] == (1, 8), "no bars given: all of them"
    with pytest.raises(RuntimeError, match="no bars"):
        t.part_range(a, 40, 9, 12)


def test_extending_and_editing_the_melody_keep_the_accompaniment(backend):
    backend.send(**{**MELODY, "bars": 4, "count": 1})
    src = [e for e in backend.until({"melodies_done", "error"}) if e["type"] == "candidate"][0]
    path = Path(src["path"]).with_name("withacc.mid")
    t.add_layers_file(src["path"], path, {42: cello(0, 16)}, 0.0, 16.0)
    before = t.layers_of(path)
    backend.send(cmd="expand", source_midi=str(path), bars=4, engine="llm", count=1, program=0, seed=1)
    ev = backend.until({"melodies_done", "error"})
    longer = [e for e in ev if e["type"] == "candidate"][0]
    assert longer["layers"] == before and expand.read_melody(longer["path"])[0] != expand.read_melody(path)[0]
    backend.send(cmd="edit", source_midi=longer["path"], instruction="calmer", bars=[2], count=1, seed=1)
    ev = backend.until({"melodies_done", "error"})
    edited = [e for e in ev if e["type"] == "candidate"][0]
    same = lambda c: [[n[0], n[1], round(n[2], 2), round(n[3], 2)] for n in c["notes"] if n[0] == 2]
    assert edited["layers"] == before and same(edited) == same(longer)
    backend.send(cmd="clear_bars", source_midi=edited["path"], from_bar=2, to_bar=3)
    last = backend.until({"part_approved", "error"})[-1]
    assert last["type"] == "part_approved" and last["layers"][0]["notes"] < before[0]["notes"]


def test_repeating_bars_copies_melody_singing_lyrics_and_accompaniment_onto_the_end(tmp_path, monkeypatch):
    import json
    import lyrics
    monkeypatch.setattr(t, "OUT", tmp_path / "out", raising=False)
    src = piece(tmp_path)
    layered = tmp_path / "l.mid"
    t.add_layers_file(src, layered, {42: cello(0, 32)}, 0.0, 16.0)                 # a cello in bars 1-4
    lyrics.sidecar(layered).write_text(json.dumps({"lines": [{"text": "la la", "start_beat": 0.0, "bars": 1, "voice": "en",
                                                              "track": "vocal"}]}))
    made = expand.repeat_bars(layered, 1, 2, times=2, log=lambda s: None)
    out = made[0]["path"]
    notes = expand.read_melody(out)[0]
    old = expand.read_melody(layered)[0]
    assert t.piece_bars(out)[2] == 8 + 4, "two copies of two bars"
    assert notes[:len(old)] == old, "what was there stays"
    first = [(p, round(s, 3), round(d, 3)) for p, s, d in old if s < 8]
    again = [(p, round(s - 32, 3), round(d, 3)) for p, s, d in notes if 32 <= s < 40]
    assert again == first, "bars 1-2 come back at bar 9"
    assert [(p, round(s - 32, 3)) for p, s, _ in expand.read_vocal(out) if s >= 32][:2] == [(67, 0.0), (69, 1.0)]
    starts = [ln["start_beat"] for ln in lyrics.used_lines(out)]
    assert starts == [0.0, 32.0, 40.0], "the line is sung again each time"
    cel = [x for x in t.layers_of(out) if x["program"] == 42][0]
    assert cel["notes"] == 16 + 2 * 8, "the cello's bars 1-2 are copied too"
    try:
        expand.repeat_bars(layered, 30, 31, log=lambda s: None)
    except RuntimeError as e:
        assert "not in the melody" in str(e)
    else:
        raise AssertionError("bars outside the melody are refused")


def test_the_repeat_command_answers_with_one_candidate(backend, tmp_path):
    src = piece(tmp_path)
    backend.send(cmd="repeat", source_midi=str(src), from_bar=1, to_bar=2, times=1)
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    cands = [e for e in ev if e["type"] == "candidate"]
    assert len(cands) == 1 and max(n[3] for n in cands[0]["notes"]) > 10 * 4 * 60 / 90 - 0.5


@pytest.mark.parametrize("program", [0, 24])
def test_extending_with_chords_adds_a_chord_layer_and_no_melody_notes(backend, program):
    backend.send(**{**MELODY, "bars": 4, "count": 1})
    chosen = [e for e in backend.until({"melodies_done", "error"}) if e["type"] == "candidate"][0]
    backend.send(cmd="expand", source_midi=chosen["path"], bars=4, chords=True, program=program, count=3, seed=2)
    ev = backend.until({"melodies_done", "error"})
    assert ev[-1]["type"] == "melodies_done", ev[-1]
    cands = [e for e in ev if e["type"] == "candidate"]
    assert len(cands) == 3
    old = expand.read_melody(chosen["path"])[0]
    for c in cands:
        assert expand.read_melody(c["path"])[0] == old, "no melody notes in the chord bars"
        assert t.piece_bars(c["path"])[2] == 8
        layer = [x for x in c["layers"] if x["program"] == program]
        assert layer and layer[0]["from_bar"] == 5 and layer[0]["to_bar"] == 8, c["layers"]
    assert len({tuple(sorted(map(tuple, [n[1:3] for n in c["notes"]]))) for c in cands}) > 1, "each option has its own pattern"
