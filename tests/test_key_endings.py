"""snap_to_key and resolve_ending: edge pitches and narrow instrument ranges."""
import text2music as t


def test_snap_never_leaves_midi_range():
    notes = [(0, 0, 1), (1, 1, 1), (126, 2, 1), (127, 3, 1)]
    for key in [(0, "major"), (1, "major"), (6, "minor"), (11, "major")]:
        out = t.snap_to_key(notes, key)
        assert all(0 <= p <= 127 for p, _, _ in out), (key, out)
        assert all(p % 12 in t.scale_pcs(key) for p, _, _ in out), (key, out)


def test_snap_stays_inside_the_instrument_range():
    # F# is not in C major; the nearest scale tones are F (65) and G (67); the range excludes G
    out = t.snap_to_key([(66, 0, 1)], (0, "major"), (60, 66))
    assert out[0][0] == 65
    out = t.snap_to_key([(61, 0, 1)], (0, "major"), (61, 66))
    assert 61 <= out[0][0] <= 66 and out[0][0] % 12 in t.scale_pcs((0, "major"))


def test_resolve_ending_picks_a_tonic_the_instrument_can_play():
    notes = [(62, 0, 1), (64, 1, 1), (65, 2, 1), (67, 3, 1)]
    # C major, but the instrument stops at 66: the nearest C is 60, not 72
    out = t.resolve_ending(notes, 1, "4/4", (0, "major"), (55, 66))
    assert out[-1][0] == 60
    assert out[:-1] == notes[:-1]                      # only the final note changes
    assert out[-1][1:] == notes[-1][1:]                # ... and not its timing


def test_resolve_ending_leaves_the_note_alone_if_the_range_has_no_tonic():
    notes = [(62, 0, 1), (64, 1, 1), (65, 2, 1), (67, 3, 1)]
    assert t.resolve_ending(notes, 1, "4/4", (0, "major"), (61, 71)) == notes


def test_resolve_ending_already_home_is_unchanged():
    notes = [(62, 0, 1), (60, 1, 1)]
    assert t.resolve_ending(notes, 1, "2/4", (0, "major"), (50, 80)) == notes


def test_empty_melody_is_returned_as_is():
    assert t.resolve_ending([], 1, "4/4", (0, "major")) == []


def test_a_relaxed_scale_lets_some_notes_stay_outside():
    # C major with five chromatic notes (C# = 61) among white notes; the first and last are in the scale
    notes = [(60, 0.0, 1.0)] + [(61 if i % 3 == 0 else 62, 1.0 + i * 0.5, 0.5) for i in range(10)] + [(60, 8.0, 1.0)]
    key = (0, "major")
    strict = t.limit_outside(notes, key, 10)
    assert t.outside_share(strict, key) == 0, "strict: nothing outside"
    some = t.limit_outside(notes, key, 5)
    most = t.limit_outside(notes, key, 0)
    assert 0 < t.outside_share(some, key) <= t.outside_share(most, key) <= 0.25 + 1e-9
    assert [p for p, _, _ in most][0] == 60 and [p for p, _, _ in most][-1] == 60
    assert len(most) == len(notes)


def test_the_allowed_notes_come_from_the_mode_table():
    assert t.scale_names((7, "lydian")) == "G A B C# D E F#"
    assert t.scale_names((2, "dorian")) == "D E F G A B C"
    assert t.scale_names((9, "minor")) == "A B C D E F G"
