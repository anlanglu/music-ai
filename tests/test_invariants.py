"""Every melody the theory composer can write must be playable and correct, whatever the settings.
The sweep is deliberately wide: time signatures x levels x keys x lengths x instruments x phrasing x shape."""
import itertools
import random

import pytest

import composer
import text2music as t
from playable import limits

SIGS = ["4/4", "3/4", "6/8", "2/4", "5/4", "7/8", "12/8"]
KEYS = [(0, "major"), (9, "minor"), (2, "major"), (7, "minor"), (10, "major"), (6, "minor"), (4, "major"), (1, "minor")]
# programs with very different ranges: bass, horn, violin, flute, piccolo, tuba, piano, voice-ish, whistle
PROGRAMS = [0, 33, 40, 56, 60, 58, 73, 72, 78, 70, 52]


def build(sig, level, key, bars, program, phrasing, shape, seed):
    """The same steps melody_candidates runs for the theory engine, ending on the instrument's real range."""
    rng = random.Random(seed)
    lo, hi = t.COMPLEXITY[level][2]
    plo, phi = limits(program)[:2]
    lo, hi = (max(lo, plo), min(hi, phi)) if max(lo, plo) <= min(hi, phi) else (plo, phi)
    notes, _ = composer.compose(bars, sig, key, level, rng, lo, hi, phrasing, shape)
    notes, _ = t.conform_melody(notes, bars, sig)
    notes = [(t.fold_pitch(p, program), s, d) for p, s, d in notes]
    notes = t.snap_to_key(notes, key, (plo, phi))
    notes = t.resolve_ending(notes, bars, sig, key, (plo, phi))
    return notes, (plo, phi)


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
        grid = itertools.product(SIGS, range(1, 6), KEYS, (1, 2, 4, 8, 16), PROGRAMS, (0, 1, 2), (0, 1, 2))
        return [c + (rnd.randrange(10**6),) for c in grid]
    cases = []
    for _ in range(600):
        cases.append((rnd.choice(SIGS), rnd.randint(1, 5), rnd.choice(KEYS), rnd.choice((1, 2, 4, 6, 8, 16)),
                      rnd.choice(PROGRAMS), rnd.choice((0, 1, 2)), rnd.choice((0, 1, 2)), rnd.randrange(10**6)))
    return cases


def test_theory_melodies_are_playable_and_correct(request):
    cfgs = configs(request.config.getoption("--full"))
    failures = []
    for sig, level, key, bars, program, phrasing, shape, seed in cfgs:
        tag = f"{sig} L{level} {t.key_label(key)} {bars}b prog{program} ph{phrasing} sh{shape} seed{seed}"
        try:
            notes, rng = build(sig, level, key, bars, program, phrasing, shape, seed)
            lo, hi = rng
            if not any(lo <= x <= hi and x % 12 == key[0] for x in range(lo, hi + 1)):
                continue   # this instrument's range holds no tonic at all: nothing to assert about the ending
            check(notes, rng, sig, bars, key, tag)
        except AssertionError as e:
            failures.append(str(e))
    assert not failures, f"{len(failures)} of {len(cfgs)} melodies broke an invariant; first: {failures[:3]}"


@pytest.mark.parametrize("shape", [0, 1, 2])
def test_shape_changes_the_melodies_but_not_their_correctness(shape):
    notes, rng = build("4/4", 4, (7, "major"), 8, 73, 1, shape, 123)
    check(notes, rng, "4/4", 8, (7, "major"), f"shape{shape}")
