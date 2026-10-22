"""Every melody the pipeline finishes must be playable and correct, whatever the settings: after the language model has
written it (here, a stand-in that writes a random walk), finish_melody puts it on the grid, into the instrument's range
and the key, and ends it on the tonic. The sweep is deliberately wide: time signatures x keys x lengths x instruments x
phrasing x character sliders."""
import itertools
import random
import sys
from pathlib import Path

import pytest

import plan
import text2music as t
from playable import limits

sys.path.insert(0, str(Path(__file__).parent))
from stub_backend import fake_notes  # noqa: E402

SIGS = ["4/4", "3/4", "6/8", "2/4", "5/4", "7/8", "12/8"]
KEYS = [(0, "major"), (9, "minor"), (2, "major"), (7, "minor"), (10, "major"), (6, "minor"), (4, "major"), (1, "minor")]
# programs with very different ranges: bass, horn, violin, flute, piccolo, tuba, piano, voice-ish, whistle
PROGRAMS = [0, 33, 40, 56, 60, 58, 73, 72, 78, 70, 52]


def build(sig, key, bars, program, phrasing, endings, seed):
    """The same steps melody_candidates runs after the language model has written the notes."""
    rng = random.Random(seed)
    raw = fake_notes(bars, sig, None, rng, 40, 100)        # not in key and not in range: finish_melody must fix that
    raw = [(p + rng.choice([0, 0, 1]), s, d) for p, s, d in raw]
    sliders = {"endings": endings, "harmony": 3}
    theplan = plan.build_plan(bars, sig, key, rng)
    notes = t.finish_melody(raw, bars, sig, program, key, True, phrasing, sliders, theplan)
    return notes, limits(program)[:2]


def check(notes, rng, sig, bars, key, tag):
    total = bars * t.bar_beats(sig)
    pcs = t.scale_pcs(key)
    assert notes, f"{tag}: empty melody"
    for a, b in zip(notes, notes[1:]):
        assert a[1] + a[2] <= b[1] + 1e-6, f"{tag}: notes overlap"
    for p, s, d in notes:
        assert 0 <= p <= 127, f"{tag}: pitch {p} is not a MIDI note"
        assert rng[0] <= p <= rng[1], f"{tag}: pitch {p} outside the instrument's range {rng}"
        assert p % 12 in pcs, f"{tag}: pitch {p} is out of key"
        assert abs(s * 4 - round(s * 4)) < 1e-6 and abs(d * 4 - round(d * 4)) < 1e-6 and d >= 0.25, f"{tag}: off the grid"
    end = notes[-1][1] + notes[-1][2]
    assert abs(end - total) < 1e-6, f"{tag}: ends at beat {end}, expected {total}"
    assert notes[-1][0] % 12 == key[0], f"{tag}: last note is not the tonic"


def configs(full):
    rnd = random.Random(7)
    if full:
        grid = itertools.product(SIGS, KEYS, (1, 2, 4, 8, 16), PROGRAMS, (0, 1, 2), (0, 3, 10))
        return [c + (rnd.randrange(10**6),) for c in grid]
    cases = []
    for _ in range(600):
        cases.append((rnd.choice(SIGS), rnd.choice(KEYS), rnd.choice((1, 2, 4, 6, 8, 16)),
                      rnd.choice(PROGRAMS), rnd.choice((0, 1, 2)), rnd.choice((0, 3, 10)), rnd.randrange(10**6)))
    return cases


def test_finished_melodies_are_playable_and_correct(request):
    cfgs = configs(request.config.getoption("--full"))
    failures = []
    for sig, key, bars, program, phrasing, endings, seed in cfgs:
        tag = f"{sig} {t.key_label(key)} {bars}b prog{program} ph{phrasing} end{endings} seed{seed}"
        try:
            notes, rng = build(sig, key, bars, program, phrasing, endings, seed)
            lo, hi = rng
            if not any(lo <= x <= hi and x % 12 == key[0] for x in range(lo, hi + 1)):
                continue   # this instrument's range holds no tonic at all: nothing to assert about the ending
            check(notes, rng, sig, bars, key, tag)
        except AssertionError as e:
            failures.append(str(e))
    assert not failures, f"{len(failures)} of {len(cfgs)} melodies broke an invariant; first: {failures[:3]}"


def test_melody_velocities_shape_the_phrase():
    import text2music as t

    notes = [(60, 0, 1), (62, 1, 1), (64, 2, 1), (67, 3, 0.5), (65, 4, 1), (64, 5, 1), (62, 6, 1), (60, 7, 1)]
    v = t.melody_velocities(notes, "4/4")
    assert len(v) == len(notes) and all(40 <= x <= 118 for x in v)
    assert len(set(v)) > 3                      # not flat
    assert v[3] == max(v) or v[3] >= max(v) - 4     # the climax is among the loudest
    assert v[-1] < v[3]                         # and the close tapers
    assert t.melody_velocities([], "4/4") == []
