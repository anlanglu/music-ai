"""Measure what the plan does to a melody: how well it follows the harmony, how often the phrases cadence, and
how often the opening idea comes back.

The language model is stubbed out, so the only difference between the runs is the plan itself. Run it with:

    .venv/bin/python review/bench_plan.py

It writes nothing; it prints a table. (The Anticipation model has to be in the Hugging Face cache: 0.5 GB.)
"""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plan
import text2music as t
import variety

# Four seed bars: a rising scale, a chord-tone figure, a falling line and a repeated-note start.
SEEDS = [
    [(67, 0, 1), (69, 1, 1), (71, 2, 1), (72, 3, 1)],
    [(67, 0, 1), (71, 1, .5), (74, 1.5, .5), (71, 2, 1), (69, 3, 1)],
    [(74, 0, .5), (72, .5, .5), (71, 1, 1), (69, 2, 1), (67, 3, 1)],
    [(67, 0, 1), (67, 1, .5), (69, 1.5, .5), (71, 2, 2)],
]


def stub_llm():
    """The language model hands back one of the seed bars, so two runs differ only in the plan."""
    cyc = itertools.cycle(SEEDS)
    t.compose = lambda prompt, bars, sig, level, llm, log, bpm=None, key=None, harmony=None: (
        list(next(cyc)), 90, [], "G major")


def intervals(pitches):
    return tuple(b - a for a, b in zip(pitches, pitches[1:]))


def measure(notes, theplan):
    """How well the melody follows the plan."""
    tonic, mode = theplan["key"]
    bb = theplan["bb"]
    inchord = outofchord = 0
    for i, pcs in enumerate(plan.plan_chord_pcs(theplan)):
        lo, hi = i * bb, (i + 1) * bb
        inbar = [p for p, s, d in notes if s < hi - 1e-9 and s + d > lo + 1e-9]
        inchord += sum(1 for p in inbar if p % 12 in pcs)
        outofchord += sum(1 for p in inbar if p % 12 not in pcs)
    cad_hit = cad_tot = 0
    for cad in theplan["cadences"]:
        cad_tot += 1
        before = [p for p, s, d in notes if s < cad["beat"] - 1e-9]
        if not before:
            continue
        want = {plan.degree_pcs(tonic, mode, deg) for deg in cad["targets"]}
        if before[-1] % 12 in want:
            cad_hit += 1
    # does a later phrase use the opening idea's shape? (transposition invariant)
    # The motif comes back as a paraphrase - the same opening gesture with the model's own rhythm - so this compares
    # the first two intervals of each phrase, not the whole bar.
    per = [[(p, s, d) for p, s, d in notes
            if ph["start_beat"] - 1e-9 <= s < ph["end_beat"] - 1e-9] for ph in theplan["phrases"]]
    motif_hit = motif_tot = 0
    if per and len(per[0]) >= 3:
        shape = intervals([p for p, _, _ in per[0][:3]])
        for ph_notes in per[1:]:
            if not ph_notes:
                continue
            motif_tot += 1
            got = intervals([p for p, _, _ in ph_notes[:3]])
            if len(got) >= len(shape) and got[:len(shape)] == shape:
                motif_hit += 1
    kinds = [variety.stats(ph)["kinds"] if len(ph) > 3 else 0 for ph in per]
    return {
        "notes": len(notes),
        "in_chord": 100 * inchord / max(inchord + outofchord, 1),
        "cad": cad_hit, "cad_tot": cad_tot,
        "motif": motif_hit, "motif_tot": motif_tot,
        "contrast": (max(kinds) - min(kinds)) if kinds else 0,
        "kinds": variety.stats(notes)["kinds"],
        "score": None,
    }


def anchors_back(notes, motif, theplan, tempo):
    """How many of the motif's notes came back at the start of a later phrase (right pitch, near the right time)."""
    per_beat = 60.0 / tempo * 100.0
    hits = total = 0
    for i in range(1, len(theplan["phrases"])):
        ph = theplan["phrases"][i]
        shifted = plan.fit_motif_to_chord(motif, *theplan["key"], plan.chord_pcs(*theplan["key"], ph["chords"][0]),
                                          prefer=ph["chords"][0])
        for p, s, _ in shifted:
            want_tick = (ph["start_beat"] + s) * per_beat
            total += 1
            for mp, ms, _ in notes:
                if abs((ms * per_beat) - want_tick) < per_beat * 0.6 and mp == p:
                    hits += 1
                    break
    return hits, total


def run(form, prompt, key, bars, sig, tempo, program, seed, count):
    t.seed_everything(seed)
    t.new_session("bench", prompt)
    theplan = plan.build_plan(bars, sig, key)
    rows = []
    # the same plan the melody follows, so the measurement is against the right chords
    for c in t.melody_candidates(prompt, bars, sig, 3, "7B", program, "small", count, "hybrid",
                                 lambda s: None, tempo, True, key, 1, 1, form, theplan=theplan):
        m = measure(c["notes"], theplan)
        m["score"] = c["score"]
        motif = [(p, s, d) for p, s, d in c["notes"] if s < 4.0]
        h, t_ = anchors_back(c["notes"], motif, theplan, tempo)
        m["anchors"] = h
        m["anchors_tot"] = t_
        rows.append(m)
    return rows


def main():
    prompt, key, bars, sig, tempo, program = "a hopeful tune in G major", (7, "major"), 8, "4/4", 90, 73
    stub_llm()
    agg = {}
    for seed in (1, 2, 3, 4, 5, 6):
        for form in (0, 1, 2, 3):
            for m in run(form, prompt, key, bars, sig, tempo, program, seed, 2):
                agg.setdefault(form, []).append(m)
    print(f"{'form':12s} {'n':>3s} {'notes':>6s} {'in-chord':>9s} {'cadences':>9s} {'motif notes back':>17s} "
          f"{'contrast':>9s} {'kinds':>6s} {'score':>6s}")
    for form in sorted(agg):
        rows = agg[form]
        n = len(rows)
        print(f"{plan.form_label(form):12s} {n:3d} {sum(r['notes'] for r in rows) / n:6.1f} "
              f"{sum(r['in_chord'] for r in rows) / n:8.0f}% "
              f"{sum(r['cad'] for r in rows):4d}/{sum(r['cad_tot'] for r in rows):<4d} "
              f"{sum(r['anchors'] for r in rows):7d}/{sum(r['anchors_tot'] for r in rows):<7d} "
              f"{sum(r['contrast'] for r in rows) / n:9.1f} "
              f"{sum(r['kinds'] for r in rows) / n:6.1f} {sum(r['score'] for r in rows) / n:6.2f}")


if __name__ == "__main__":
    main()
