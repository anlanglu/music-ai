"""The musical plan: the phrases, the harmony under them, and how much of it a melody follows."""
import numpy as np
import pytest
import torch

import plan
import text2music as t
from anticipation import sample as smp
from anticipation.vocab import CONTROL_OFFSET, MAX_NOTE, MAX_PITCH, NOTE_OFFSET

VOCAB = CONTROL_OFFSET + 4 * MAX_NOTE      # big enough for every index used below


# ---- the plan itself
def test_the_phrases_tile_the_piece_exactly():
    for bars in range(1, 17):
        p = plan.build_plan(bars, "4/4", (7, "major"))
        assert sum(ph["bars"] for ph in p["phrases"]) == bars
        assert len(p["chords"]) == bars
        assert p["phrases"][0]["start_bar"] == 0
        for a, b in zip(p["phrases"], p["phrases"][1:]):
            assert a["start_bar"] + a["bars"] == b["start_bar"]
        assert p["phrases"][-1]["end_beat"] == pytest.approx(bars * 4.0)


def test_every_chord_belongs_to_the_key():
    for mode in ("major", "minor"):
        key = (9, mode)
        p = plan.build_plan(8, "4/4", key)
        allowed = plan.key_pcs(*key)
        for pcs in plan.plan_chord_pcs(p):
            assert pcs <= allowed, f"{pcs} is outside the key {allowed}"


def test_the_minor_dominant_is_a_major_triad():
    # A minor: the V chord is E major, so it can be cadenced on
    assert plan.chord_pcs(9, "minor", 4) == {4, 8, 11}
    assert plan.chord_name(9, "minor", 4) == "E major"
    assert plan.chord_name(7, "major", 2) == "B minor"


def test_the_piece_ends_on_the_tonic_chord():
    p = plan.build_plan(8, "4/4", (7, "major"))
    assert p["chords"][-1] == 0
    assert p["cadences"][-1]["targets"] == [0]


def test_the_cadences_land_on_phrase_ends():
    p = plan.build_plan(8, "4/4", (7, "major"))
    ends = [ph["end_beat"] for ph in p["phrases"]]
    assert [c["beat"] for c in p["cadences"]] == ends


# ---- the form slider
def test_the_form_slider_goes_from_free_to_the_whole_plan():
    assert plan.form_weights(0) == {}
    assert set(plan.form_weights(1)) == {"chord"}
    assert set(plan.form_weights(2)) == {"chord", "cadence"}
    assert set(plan.form_weights(3)) == {"chord", "cadence", "motif"}
    assert plan.form_weights(3)["motif"] >= plan.form_weights(2).get("motif", 0)


# ---- the knobs the app can turn
def test_the_knobs_start_at_the_defaults():
    k = plan.knobs()
    assert k["chord"] == plan.DEFAULT_KNOBS["chord"]
    assert k["motif"] == plan.DEFAULT_KNOBS["motif"]
    assert k["run"] is None, "the shape's numbers come from the shape preset"


def test_a_knob_overrides_the_default():
    assert plan.knobs({"motif": 12.0})["motif"] == 12.0


def test_an_unknown_or_unset_knob_is_ignored():
    k = plan.knobs({"nonsense": 5.0, "motif": None})
    assert "nonsense" not in k
    assert k["motif"] == plan.DEFAULT_KNOBS["motif"]


def test_a_knob_overrides_the_form_preset():
    assert plan.form_weights(3)["motif"] == plan.DEFAULT_KNOBS["motif"]
    assert plan.form_weights(3, {"motif": 19.0})["motif"] == 19.0
    # a knob cannot switch a component on that the preset does not use
    assert "motif" not in plan.form_weights(1, {"motif": 19.0})


def test_a_knob_overrides_the_shape_preset():
    assert plan.shape_weights(2)["run"] == plan.SHAPE_BIAS[2]["run"]
    assert plan.shape_weights(2, {"run": 0.0})["run"] == 0.0
    assert plan.shape_weights(0) is None, "stepwise means no bias at all"
    assert plan.shape_weights(0, {"run": 1.0})["run"] == 1.0, "but an explicit knob turns it on"


def test_the_sliders_turn_into_the_samplers_numbers():
    d = plan.from_sliders()
    assert d["shape"] == 1 and d["form"] == 2, "the defaults are what the app did before the sliders"
    assert abs(d["knobs"]["chord"] - 2.2) < 0.01 and abs(d["knobs"]["cadence"] - 5.0) < 0.01
    assert abs(d["knobs"]["beat"] - 0.5) < 0.01 and d["knobs"]["motif"] == 0
    top = plan.from_sliders({"shape": 10, "pulse": 10, "harmony": 10, "endings": 10, "theme": 10})
    assert top["shape"] == 2 and top["form"] == 3 and top["knobs"]["motif"] == 20.0
    free = plan.from_sliders({"shape": 0, "harmony": 0, "endings": 0, "theme": 0})
    assert free["shape"] == 0 and free["form"] == 0
    assert plan.from_sliders({"shape": 99, "theme": -4, "nonsense": 1})["knobs"]["motif"] == 0, "clamped, unknowns ignored"


def test_a_seeded_run_gives_the_same_plan():
    """The plan's chords must not wander: they come from the seeded random module, not a fresh one."""
    import random

    random.seed(7)
    first = plan.build_plan(8, "4/4", (7, "major"))["chords"]
    random.seed(7)
    assert plan.build_plan(8, "4/4", (7, "major"))["chords"] == first


# ---- moving a motif
def test_a_transposed_motif_keeps_its_intervals():
    motif = [(67, 0, 1), (69, 1, 1), (72, 2, 1)]
    moved = plan.transpose_motif(motif, "major", 2)
    assert [p for p, _, _ in moved] == [71, 73, 76]
    ivals = lambda xs: [b - a for a, b in zip(xs, xs[1:])]
    assert ivals([p for p, _, _ in moved]) == ivals([p for p, _, _ in motif])


def test_steps_down_are_the_same_distance():
    assert plan.step_semitones("major", 1) == 2
    assert plan.step_semitones("major", 4) == 7
    assert plan.step_semitones("major", 7) == 12
    assert plan.step_semitones("major", -1) == -1
    assert plan.step_semitones("major", -7) == -12


def test_a_motif_is_fitted_to_the_chord_it_plays_over():
    motif = [(67, 0, 1), (69, 1, 1), (71, 2, 1)]        # G A B
    fitted = plan.fit_motif_to_chord(motif, 7, "major", {4, 8, 11})   # an E minor chord
    hits = sum(1 for p, _, _ in fitted if p % 12 in {4, 8, 11})
    assert hits >= 2, "the transposition should put most of the motif inside the chord"


# ---- resolving the phrase endings
def test_a_phrase_ends_on_its_cadence_degree():
    p = plan.build_plan(8, "4/4", (7, "major"))
    notes = [(67, 13.0, 0.5), (69, 13.5, 0.25), (67, 14.5, 0.5), (71, 16.5, 0.25)]
    out = plan.resolve_phrase_endings(notes, p, (7, "major"), (60, 96))
    last_before_16 = [n for n in out if n[1] < 16.0][-1]
    want = {plan.degree_pcs(7, "major", d) for d in p["cadences"][0]["targets"]}
    assert last_before_16[0] % 12 in want


def test_the_last_phrase_is_left_to_the_tonic():
    p = plan.build_plan(8, "4/4", (7, "major"))
    notes = [(67, 30.0, 0.5), (69, 31.0, 0.5)]
    out = plan.resolve_phrase_endings(notes, p, (7, "major"), (60, 96))
    assert out == notes, "the piece's own ending is resolve_ending's job"


def test_resolving_keeps_the_note_inside_the_instrument():
    p = plan.build_plan(8, "4/4", (7, "major"))
    notes = [(60, 13.0, 0.5), (60, 14.5, 0.5)]
    out = plan.resolve_phrase_endings(notes, p, (7, "major"), (60, 96))
    assert all(60 <= n[0] <= 96 for n in out)


# ---- the sampler: the harmony of each bar
def _pool(regions=None, weight=0.0):
    pool = {"allowed": [73], "ranges": {73: (60, 96)}}
    if regions is not None:
        pool["regions"] = regions
        pool["chord_weight"] = weight
    return pool


def test_the_chord_of_the_bar_is_preferred():
    # bars 0-1 are a C major chord, bars 2-3 an A minor chord
    regions = [(0, 100, {0, 4, 7}), (100, 200, {9, 0, 4})]
    pool = _pool(regions, 3.0)
    logits = torch.zeros(VOCAB)
    smp.steer_instruments(logits, [], pool, onset=10)
    seg = logits[NOTE_OFFSET + 73 * MAX_PITCH: NOTE_OFFSET + 74 * MAX_PITCH]
    assert seg[60] > seg[61], "C is a chord tone of the first bar, C# is not"
    assert seg[64] > seg[65]

    logits = torch.zeros(VOCAB)
    smp.steer_instruments(logits, [], pool, onset=150)
    seg = logits[NOTE_OFFSET + 73 * MAX_PITCH: NOTE_OFFSET + 74 * MAX_PITCH]
    assert seg[69] > seg[70], "A is a chord tone of the second bar"
    assert seg[64] > seg[65]


def test_the_scale_still_applies_within_the_regions():
    regions = [(0, 100, {0, 4, 7})]
    pool = {"allowed": [73], "ranges": {73: (60, 96)}, "pitch_classes": {0, 2, 4, 5, 7, 9, 11}}
    pool["regions"] = regions
    pool["chord_weight"] = 3.0
    logits = torch.zeros(VOCAB)
    smp.steer_instruments(logits, [], pool, onset=10)
    seg = logits[NOTE_OFFSET + 73 * MAX_PITCH: NOTE_OFFSET + 74 * MAX_PITCH]
    assert torch.isfinite(seg[62]), "D is in the key"
    assert not torch.isfinite(seg[63]), "D# is in neither the key nor the chord"


def test_no_regions_means_no_harmony_restriction():
    pool = _pool(None)
    logits = torch.zeros(VOCAB)
    smp.steer_instruments(logits, [], pool, onset=10)
    seg = logits[NOTE_OFFSET + 73 * MAX_PITCH: NOTE_OFFSET + 74 * MAX_PITCH]
    assert torch.isfinite(seg[61]) and torch.isfinite(seg[62])


# ---- the sampler: the motif and the cadences
def _melodic_pool(targets):
    return {"allowed": [0], "melodic": {"instrument": 0, "targets": targets, "target_weight": 4.0,
                                        "target_tol": 50, "target_sigma": 1.5}}


def test_a_target_pulls_the_melody_towards_it():
    toks = [0, 0, NOTE_OFFSET + 60]
    pool = _melodic_pool([(100, 67)])
    l = smp.melodic_bias(torch.zeros(VOCAB), toks, pool, onset=100)
    seg = l[NOTE_OFFSET:NOTE_OFFSET + MAX_PITCH]
    assert seg[67] > seg[65] and seg[67] > seg[69], "67 is the target"


def test_a_target_far_away_does_nothing():
    toks = [0, 0, NOTE_OFFSET + 60]
    pool = _melodic_pool([(100, 67)])
    l = smp.melodic_bias(torch.zeros(VOCAB), toks, pool, onset=900)
    seg = l[NOTE_OFFSET:NOTE_OFFSET + MAX_PITCH]
    assert seg[67] == seg[65], "nothing should be pulled when the note lands far from the target"


def test_a_note_between_two_targets_follows_the_line_between_them():
    toks = [0, 0, NOTE_OFFSET + 60]
    pool = _melodic_pool([(100, 60), (200, 72)])
    l = smp.melodic_bias(torch.zeros(VOCAB), toks, pool, onset=150)
    seg = l[NOTE_OFFSET:NOTE_OFFSET + MAX_PITCH]
    assert seg[66] > seg[60], "halfway between 60 and 72 is 66"


def test_the_targets_the_plan_makes_are_where_the_phrase_is():
    p = plan.build_plan(8, "4/4", (7, "major"))
    motif = [(67, 0, 1), (69, 1, 1), (71, 2, 1)]
    targets = plan.motif_targets(motif, p, 90, 1)
    per_beat = 60.0 / 90 * 100
    assert targets[0][0] == pytest.approx(p["phrases"][1]["start_beat"] * per_beat, abs=2)
    cad = plan.cadence_targets(p, 70, 90)
    assert cad[-1][0] == pytest.approx(p["cadences"][-1]["beat"] * per_beat, abs=2)


# ---- the session keeps the plan, so the accompaniment can follow it
def test_the_plan_is_stored_with_the_session(tmp_path):
    import json
    t.OUT_DIR = tmp_path
    try:
        t.new_session("planned")
        p = plan.build_plan(8, "4/4", (7, "major"))
        t._write_meta(t.current_session(), plan=t.plan_json(p, 3), form=3)
        back = t.plan_for_file(t.current_session() / "x")
        assert back is not None
        assert back["chords"] == p["chords"]
        assert back["key"] == (7, "major")
        assert back["form"] == 3
        assert json.loads((t.current_session() / "session.json").read_text())["plan"]["chords"] == p["chords"]
    finally:
        t.OUT_DIR = tmp_path.parent / "output"


def test_the_key_can_be_read_from_the_plan_alone(tmp_path):
    """A session made from the command line may only have the plan; the accompaniment still needs the key."""
    t.OUT_DIR = tmp_path
    try:
        t.new_session("planned")
        p = plan.build_plan(8, "4/4", (9, "minor"))
        t._write_meta(t.current_session(), plan=t.plan_json(p, 3), form=3)
        assert t.key_for_file(t.current_session() / "x") == (9, "minor")
    finally:
        t.OUT_DIR = tmp_path.parent / "output"


def test_the_shape_slider_blends_the_presets():
    assert plan.shape_amount_knobs(1) == {k: v for k, v in plan.SHAPE_BIAS[1].items()}
    assert plan.shape_amount_knobs(2) == plan.SHAPE_BIAS[2]
    assert all(v == 0 for v in plan.shape_amount_knobs(0).values())
    mid = plan.shape_amount_knobs(1.5)
    assert plan.SHAPE_BIAS[1]["run"] < mid["run"] < plan.SHAPE_BIAS[2]["run"]


def test_the_form_is_read_from_the_strengths():
    assert plan.form_from_knobs({"chord": 0, "cadence": 0, "motif": 0}) == 0
    assert plan.form_from_knobs({"chord": 2, "cadence": 0, "motif": 0}) == 1
    assert plan.form_from_knobs({"chord": 0, "cadence": 3, "motif": 0}) == 2
    assert plan.form_from_knobs({"motif": 4}) == 3


def test_the_runs_slider_is_independent_of_shape():
    assert abs(plan.from_sliders()["knobs"]["run"] - 1.5) < 1e-6, "the default is what the shape slider gave at balanced"
    smooth_no_runs = plan.from_sliders({"shape": 0, "runs": 10})["knobs"]["run"]
    jumpy_runs_ok = plan.from_sliders({"shape": 10, "runs": 0})["knobs"]["run"]
    assert smooth_no_runs == 3.0 and jumpy_runs_ok == 0.0


def test_modes_have_the_right_notes_and_signatures():
    import notation
    import text2music as t
    assert t.scale_pcs((2, "dorian")) == {2, 4, 5, 7, 9, 11, 0}, "D dorian is the white notes"
    assert t.scale_pcs((5, "lydian")) == {5, 7, 9, 11, 0, 2, 4}, "F lydian is the white notes"
    assert t.scale_pcs((7, "mixolydian")) == {7, 9, 11, 0, 2, 4, 5}
    assert t.scale_pcs((4, "phrygian")) == {4, 5, 7, 9, 11, 0, 2}
    # a mode takes the signature of its tonic's major or minor key; the notes that make it a mode are accidentals
    assert notation._sharps((2, "lydian")) == 2, "D lydian shows D major's two sharps (its G sharp is an accidental)"
    assert notation._sharps((2, "mixolydian")) == 2 and notation._sharps((7, "lydian")) == 1
    assert notation._sharps((2, "dorian")) == -1 and notation._sharps((2, "phrygian")) == -1, "D minor's one flat"
    assert notation._sharps((9, "minor")) == 0 and notation._sharps((9, "dorian")) == 0
    assert notation._sharps((11, "locrian")) == 2, "B minor's two sharps"
    assert t.scale_pcs((11, "locrian")) == {11, 0, 2, 4, 5, 7, 9}, "B locrian is the white notes"
    assert plan.chord_name(11, "locrian", 0) == "B diminished"
    assert t.parse_key("a dreamy tune in D Dorian") == (2, "dorian")
    assert t.parse_key("something in F# lydian") == (6, "lydian")
    assert t.parse_key("in A aeolian") == (9, "minor") and t.parse_key("C ionian") == (0, "major")


def test_a_modal_plan_uses_the_modes_chords():
    import random
    for mode in ("dorian", "phrygian", "lydian", "mixolydian", "locrian"):
        p = plan.build_plan(8, "4/4", (2, mode), random.Random(3))
        assert p["key"] == (2, mode) and len(p["chords"]) == 8 and p["chords"][-1] == 0
        assert all(0 <= c < 7 for c in p["chords"])
        assert plan.chord_name(2, mode, 0)


def test_flavor_brings_in_the_characteristic_note():
    # D dorian: its raised 6th is B. A tune with no B gets some; strength 0 changes nothing; major keys are left alone.
    notes = [(62 + (0, 2, 3, 5, 7, 5, 3, 2)[i % 8], float(i), 1.0) for i in range(16)]
    notes[0] = (62, 0.0, 1.0)
    assert not any(p % 12 == 11 for p, _, _ in notes)
    assert plan.flavor_notes(notes, (2, "dorian"), 0) == notes
    some = plan.flavor_notes(notes, (2, "dorian"), 5)
    more = plan.flavor_notes(notes, (2, "dorian"), 10)
    count = lambda ns: sum(1 for p, _, _ in ns if p % 12 == 11)
    assert 0 < count(some) <= count(more)
    assert all({p % 12 for p, _, _ in ns} <= {2, 4, 5, 7, 9, 11, 0} for ns in (some, more)), "still in the mode"
    assert some[0] == notes[0] and some[-1] == notes[-1], "the first and last notes stay"
    assert plan.flavor_notes(notes, (2, "major"), 10) == notes


def test_a_mode_named_in_the_prompt_is_picked_up():
    import text2music as t
    assert t.parse_key("a C major scale in lydian mode") == (0, "major"), "no second note: key says major..."
    assert t.parse_mode("a C major scale in lydian mode") == "lydian", "...but the mode is found"
    assert t.parse_mode("a major scale in lydian mode") == "lydian" and t.parse_mode("a tune in Dorian") == "dorian"
    assert t.parse_mode("a sad slow tune") is None and t.parse_mode("an aeolian tune") == "minor"
    assert t.parse_key("play a C major scale in C lydian") == (0, "lydian"), "the modal name wins over major"
    assert t.parse_key("D dorian, not D minor") == (2, "dorian")
