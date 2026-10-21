"""The sampler constraints, tested on synthetic logits (no model needed)."""
import numpy as np
import torch

from anticipation import sample as smp
from anticipation.vocab import (CONTROL_OFFSET, DUR_OFFSET, MAX_DUR, MAX_NOTE, MAX_PITCH, MAX_TIME, NOTE_OFFSET,
                                TIME_OFFSET)
from playable import sampler_pool

VOCAB = CONTROL_OFFSET + 4 * MAX_NOTE   # big enough for every index used below


def logits():
    return torch.zeros(VOCAB)


def finite_ticks(l):
    seg = l[TIME_OFFSET:TIME_OFFSET + MAX_TIME]
    return set(torch.nonzero(torch.isfinite(seg)).flatten().tolist())


# ---- n_same: anticipated controls must not hide the notes before them
def triple(time, instr, pitch, control=False, dur=10):
    off = CONTROL_OFFSET if control else 0
    return [off + TIME_OFFSET + time, off + DUR_OFFSET + dur, off + NOTE_OFFSET + instr * MAX_PITCH + pitch]


def test_count_simultaneous_plain():
    toks = triple(100, 0, 60) + triple(100, 0, 64)
    assert smp.count_simultaneous(toks, 100) == 2
    assert smp.count_simultaneous(toks, 150) == 0


def test_count_simultaneous_skips_trailing_controls():
    toks = triple(100, 0, 60) + triple(100, 0, 64) + triple(150, 1, 50, control=True) + triple(160, 1, 52, control=True)
    assert smp.count_simultaneous(toks, 100) == 2      # the old loop stopped at the control and returned 0


def test_count_simultaneous_stops_at_an_earlier_note():
    toks = triple(90, 0, 60) + triple(100, 0, 62) + triple(120, 1, 40, control=True)
    assert smp.count_simultaneous(toks, 100) == 1


# ---- time grid
GRID = {"step": 25, "beat": 100, "bar": 400, "max_gap": 200, "max_chord": 2, "beat_boost": 0.5, "bar_boost": 1.0}


def test_grid_time_only_allows_grid_points():
    l = smp.grid_time_logits(logits(), 0, GRID, current_time=0, n_same=0)
    assert all(t % 25 == 0 for t in finite_ticks(l))


def test_grid_time_max_gap_does_not_discard_the_grid_when_unsatisfiable():
    # nothing on the grid lies within max_gap of current_time=13 (gap 1 tick, grid step 25): the grid must still hold
    g = dict(GRID, max_gap=1)
    l = smp.grid_time_logits(logits(), 0, g, current_time=13, n_same=0)
    ticks = finite_ticks(l)
    assert ticks and all(t % 25 == 0 for t in ticks)


def test_grid_time_beat_and_bar_are_optional():
    g = {"step": 25}
    l = smp.grid_time_logits(logits(), 0, g, current_time=0, n_same=0)
    assert finite_ticks(l)


def test_grid_time_stacking_cap_moves_time_forward():
    l = smp.grid_time_logits(logits(), 0, GRID, current_time=100, n_same=2)
    assert min(finite_ticks(l)) > 100


def test_grid_dur_relaxes_one_requirement_at_a_time():
    g = {"step": 25, "min_dur": 10_000}   # impossible minimum length
    l = smp.grid_dur_logits(logits(), 0, g)
    seg = l[DUR_OFFSET:DUR_OFFSET + MAX_DUR]
    ok = set(torch.nonzero(torch.isfinite(seg)).flatten().tolist())
    assert ok and all(d % 25 == 0 for d in ok), "still on the grid even though the minimum length was impossible"


# ---- instrument steering
def pitches_allowed(l, instr):
    seg = l[NOTE_OFFSET + instr * MAX_PITCH: NOTE_OFFSET + (instr + 1) * MAX_PITCH]
    return set(torch.nonzero(torch.isfinite(seg)).flatten().tolist())


def test_steering_keeps_range_and_scale():
    pool = sampler_pool([73], free=True, pitch_classes={0, 2, 4, 5, 7, 9, 11})
    l = smp.steer_instruments(logits(), [], pool, onset=0)
    allowed = pitches_allowed(l, 73)
    assert allowed and all(60 <= p <= 96 and p % 12 in {0, 2, 4, 5, 7, 9, 11} for p in allowed)
    assert pitches_allowed(l, 0) == set()     # other instruments are excluded


def test_steering_relaxes_the_scale_before_the_range():
    # a range holding only out-of-scale pitches: the scale is dropped, the range stays
    pool = sampler_pool([73], free=True, pitch_classes={0}, ranges={73: (61, 63)})
    l = smp.steer_instruments(logits(), [], pool, onset=0)
    assert pitches_allowed(l, 73) == {61, 62, 63}


def test_steering_with_no_instruments_is_a_no_op():
    l = logits()
    assert torch.equal(smp.steer_instruments(l.clone(), [], {"allowed": []}, onset=0), l)


def test_pool_mask_is_rebuilt_when_the_pool_changes():
    pool = sampler_pool([73], free=True)
    m1 = smp._pool_mask(pool, torch.device("cpu"))
    assert m1[73, 60] and not m1[73, 50]
    pool["ranges"][73] = (40, 50)
    m2 = smp._pool_mask(pool, torch.device("cpu"))
    assert m2[73, 45] and not m2[73, 60]


# ---- melodic bias
def test_melodic_bias_discourages_a_third_step_the_same_way():
    # melody instrument 0 has just played 60, 62, 64 (two steps up): a third step up should lose to turning back
    toks = sum((triple(i * 100, 0, p) for i, p in enumerate([60, 62, 64])), [])
    pool = {"allowed": [0], "melodic": {"instrument": 0, "run": 3.0, "leap": 0.0, "repeat": 0.0, "variety": 0.0}}
    l = smp.melodic_bias(logits(), toks, pool)
    seg = l[NOTE_OFFSET:NOTE_OFFSET + MAX_PITCH]
    assert seg[66] < seg[62]        # continuing the run (66) is penalised relative to turning back (62)
    assert seg[65] < seg[62]


def test_melodic_bias_uses_the_named_instrument_only():
    toks = triple(0, 5, 60) + triple(100, 5, 62) + triple(200, 5, 64) + triple(300, 0, 90)
    pool = {"allowed": [0, 5], "melodic": {"instrument": 5, "run": 3.0}}
    l = smp.melodic_bias(logits(), toks, pool)
    seg = l[NOTE_OFFSET + 5 * MAX_PITCH: NOTE_OFFSET + 6 * MAX_PITCH]
    assert seg[66] < seg[62]


def test_melodic_bias_keeps_forbidden_pitches_forbidden():
    l = logits()
    l[NOTE_OFFSET + 60] = -float("inf")
    toks = triple(0, 0, 60) + triple(100, 0, 62)
    pool = {"allowed": [0], "melodic": {"instrument": 0, "run": 1.0, "leap": 1.0, "repeat": 1.0, "variety": 1.0}}
    out = smp.melodic_bias(l, toks, pool)
    assert out[NOTE_OFFSET + 60] == -float("inf")
    assert not torch.isnan(out).any()
