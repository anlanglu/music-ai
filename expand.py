"""Step 2: grow a melody a few bars at a time.

Step 1 (text2music.make_melodies) has the language model write a whole melody. This takes one of those melodies, or one
that was already expanded, and adds bars to the end of it, as several options to choose from. Each expansion has its own
settings: the length, the engine, the character sliders and a free-text direction.

  LLM only  the language model continues the melody a phrase at a time, seeing the phrase before and the opening idea.
  Hybrid    the Anticipation model continues it (it was trained on real MIDI, so its rhythms are natural), steered by
            the chords, the cadences and the opening idea of the plan, and by the shape sliders.

The notes already written are never changed (except that the last one may be shortened so the new bars can begin).
"""
import json
import math
import time
from pathlib import Path

import mido

import plan
import text2music as t

CONTEXT_BARS = 8        # how much of the melody the Anticipation model is shown (its clock tops out at 100 s)


def read_melody(path):
    """(notes, tempo, sig, program, total_beats) of a melody file, as [(pitch, start_beat, duration_beats)] with one note at
    a time (when notes overlap, the highest wins)."""
    tempo, sig, seconds = t.midi_info(path)
    mid = mido.MidiFile(str(path))
    tpb = mid.ticks_per_beat
    found, program = [], 0
    for trk in mid.tracks:
        tick, on, local, prog = 0, {}, [], None
        for m in trk:
            tick += m.time
            if m.type == "program_change" and prog is None:
                prog = m.program
            elif m.type == "note_on" and m.velocity > 0 and m.channel != 9:
                on[(m.channel, m.note)] = tick
            elif m.type in ("note_off", "note_on") and (m.channel, m.note) in on:
                start = on.pop((m.channel, m.note))
                local.append((m.note, start / tpb, max((tick - start) / tpb, 0.25)))
        if local and not found:
            program = prog or 0
        found += local
    by_start = {}
    for p, s, d in found:
        if s not in by_start or p > by_start[s][0]:
            by_start[s] = (p, s, d)
    notes = sorted(by_start.values(), key=lambda n: n[1])
    notes = [(p, s, min(d, notes[i + 1][1] - s) if i + 1 < len(notes) else d) for i, (p, s, d) in enumerate(notes)]
    total = max(seconds * tempo / 60, notes[-1][1] + notes[-1][2] if notes else 0)
    return notes, tempo, sig, program, total


def _hybrid_reading(d):
    """What Hybrid will do with a direction, and what it cannot: it steers only how busy, how high or low, how much on the
    beat and how adventurous the new bars are."""
    words = {"density": {-2: "much sparser", -1: "sparser", 1: "busier", 2: "much busier"},
             "register": {-2: "much lower", -1: "lower", 1: "higher", 2: "much higher"},
             "rhythm": {-2: "looser, off the beat", -1: "looser, off the beat", 1: "steadier, on the beat",
                        2: "much steadier, on the beat"},
             "adventurous": {-2: "safer", -1: "safer", 1: "more adventurous", 2: "more adventurous"}}
    used = [words[k][d[k]] for k in words if d.get(k) in words[k]]
    skipped = [name for name, on in (("a playing style", d.get("style")), ("note lengths", d.get("length")),
                                     ("loudness", d.get("dynamics")), ("answering the melody", d.get("complementary")),
                                     ("doubling", d.get("double_interval"))) if on]
    out = ("Hybrid will make the new bars " + ", ".join(used) + ".") if used else "Hybrid found nothing in that to act on."
    if skipped:
        out += f" It cannot do {', '.join(skipped)}; choose LLM only for that."
    return out


def _direction_for_sampler(d, grid, q, top_p, lo, hi):
    """What a free-text direction can change in the Anticipation sampler: how busy the line is, how much it leans on the beat,
    where in the range it sits and how adventurous the sampling is. Returns the (top_p, lo, hi) to use."""
    dens, reg = d["density"], d["register"]
    grid["max_gap"] = q * {-2: 6, -1: 4, 0: 2, 1: 1, 2: 0.5}[dens]
    if dens < 0:
        grid["min_gap"] = int(q * {-2: 3, -1: 2}[dens])
    boost = {-2: -2.5, -1: -1.5, 1: 2.0, 2: 3.5}.get(d["rhythm"])
    if boost is not None:
        grid["beat_boost"], grid["bar_boost"] = boost, boost * 2
    if reg:
        shift = {-2: -9, -1: -5, 1: 5, 2: 9}[reg]
        lo, hi = lo + shift, hi + shift
    return {-2: 0.6, -1: 0.85, 1: 1.0, 2: 1.0}.get(d["adventurous"], top_p), lo, hi


def expand_melody(source_midi, add_bars=4, engine="hybrid", count=3, size="small", llm="7B", complexity=3, sliders=None,
                  direction="", resolve=True, phrasing=1, log=print, on_candidate=None):
    """Write `count` options, each the source melody plus `add_bars` more bars, to the session folder. Returns the list of
    {index, path, tempo, sig, score}; on_candidate(info) is called as each one is ready."""
    src = Path(source_midi)
    notes, tempo, sig, program, total = read_melody(src)
    if not notes:
        raise RuntimeError("There is no melody in that file to expand")
    bb = t.bar_beats(sig)
    old_bars = max(1, math.ceil(total / bb - 0.01))
    old_beats = old_bars * bb
    folder = t.ensure_session()
    try:
        meta = json.loads((folder / "session.json").read_text())
    except (OSError, ValueError):
        meta = {}
    prompt = meta.get("prompt") or folder.name
    sl = plan.clean_sliders(sliders)
    mine = plan.from_sliders(sl)
    kn = plan.knobs(mine["knobs"])
    key = t.key_for_file(src)
    if key is None:
        key = t.estimate_key(notes)
        log(f"  ▸ The melody has no stored key; continuing in {t.key_label(key)}, the best fit for its notes.")
    whole, section = plan.extend(t.plan_for_file(src), key, sig, old_bars, add_bars, notes, level=complexity)
    form = max(mine["form"], int((meta.get("plan") or {}).get("form", 0) or 0))

    d = None
    if direction.strip():
        import direction as dr

        if engine == "llm":
            log(f"  ▸ Your direction goes to the LLM as its first instruction: \"{direction.strip()}\".")
        else:
            d = t.interpret_direction(direction, "melody", "", log)
            if not d["empty"]:
                log("  ▸ " + _hybrid_reading(d))
    log(f"  ▸ Allowed notes in {t.key_label(key)}: {t.scale_names(key)}"
        + ("." if sl["strict"] >= 10 else f" (relaxed: up to {round((10 - sl['strict']) * 2.5)}% of the new notes may be outside)."))
    log(f"  ▸ Expanding {old_bars} bars by {add_bars} bars to {old_bars + add_bars}, in {t.key_label(key)} "
        f"({'the LLM continues it' if engine == 'llm' else 'the Anticipation model continues it'}).")

    name = time.strftime("%H%M%S") + "-exp"
    total_beats = (old_bars + add_bars) * bb
    density = t.COMPLEXITY[complexity][1]
    motif = [n for n in notes if n[1] < bb]                  # the piece's opening idea
    out, tries = [], 0

    def produce():
        if engine == "llm":
            chunk = 2 if complexity >= 4 else 4
            tail = [(p, s - (old_beats - chunk * bb), dd) for p, s, dd in notes if s >= old_beats - chunk * bb]
            new, _tempo, _plans, _ = t.compose(prompt, add_bars, sig, complexity, llm, log, tempo, key, harmony=section,
                                               before=tail or None, final=resolve, sliders=sl, motif=motif,
                                               extra=direction.strip())
            return new
        return _hybrid_section(notes, tempo, sig, program, key, section, add_bars, old_bars, size, complexity, sl, kn,
                               mine["form"], motif, d, log)

    while len(out) < count and tries < count * 3:
        tries += 1
        try:
            raw = produce()
        except (ValueError, KeyError, TypeError, RuntimeError) as e:
            log(f"      (attempt unusable, retrying: {str(e).splitlines()[0][:70]})")
            continue
        new = t.finish_melody(raw, add_bars, sig, program, key, resolve, phrasing, sl, section)
        if len(new) < 4:
            continue
        if sl["strict"] < 10:
            log(f"      {t.outside_share(new, key):.0%} of the new notes are outside {t.key_label(key)}.")
        old = list(notes)
        first = new[0][1] + old_beats
        p, s_, dd = old[-1]
        if s_ + dd > first:                                   # let the last old note end where the new bars begin
            old[-1] = (p, s_, max(first - s_, 0.25))
        combined = old + [(p, s_ + old_beats, dd) for p, s_, dd in new]
        k = len(out) + 1
        path = folder / f"{name}-opt{k}.mid"
        t.write_melody_midi(path, tempo, combined, program, sig, total_beats)
        if not out:    # the accompaniment follows the plan of the whole, longer melody
            t._write_meta(folder, key=list(key), plan=t.plan_json(whole, form), form=form)
        info = {"index": k, "path": path, "tempo": tempo, "sig": sig,
                "score": t.melody_score(combined, total_beats, density)[0]}
        out.append(info)
        log(f"      option {k}: {len(combined)} notes, {old_bars + add_bars} bars")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError("No usable expansion was produced; try again")
    return out


def _hybrid_section(notes, tempo, sig, program, key, section, add_bars, old_bars, size, level, sl, kn, form, motif, d, log):
    """The new bars, from the Anticipation model, continuing the last CONTEXT_BARS bars of the melody. Returns notes in
    the new section's own beats (0 = its first beat)."""
    from anticipation.convert import midi_to_events
    from anticipation.sample import generate
    from anticipation.tokenize import extract_instruments
    from anticipation.vocab import NOTE_OFFSET
    from playable import limits

    bb = t.bar_beats(sig)
    num, den = (int(x) for x in sig.split("/"))
    room = int(95 * tempo / 60 / bb) - add_bars          # the model's clock tops out at 100 s
    ctx_bars = max(1, min(old_bars, CONTEXT_BARS, room))
    ctx_beats = ctx_bars * bb
    edge = old_bars * bb - ctx_beats
    ctx = [(p, s - edge, dd) for p, s, dd in notes if s >= edge - 1e-6]
    model = t._get_ant(size)
    t.TMP_DIR.mkdir(parents=True, exist_ok=True)
    seed_path = t.TMP_DIR / "_expand_seed.mid"
    seed_sec = t.write_melody_midi(seed_path, tempo, ctx, program, sig, ctx_beats)
    seed_events, _ = extract_instruments(midi_to_events(str(seed_path)), [])
    length = (ctx_beats + add_bars * bb) * 60 / tempo
    q = 6000.0 / tempo
    per_beat = 60.0 / tempo * 100.0
    start_tick = round(ctx_beats * per_beat)

    plo, phi = limits(program)[:2]
    pitches = [p for p, _, _ in notes]
    lo, hi = max(plo, min(pitches) - 5), min(phi, max(pitches) + 7)
    grid = {"step": q / 4, "beat": q * 1.5 if den == 8 and num % 3 == 0 else q, "bar": q * bb,
            "max_gap": q * 2, "max_chord": 1, "beat_boost": kn["beat"], "bar_boost": kn["beat"] * 2}
    top_p = min(0.85 + 0.03 * level, 0.99)
    if d is not None and not d["empty"]:
        top_p, lo, hi = _direction_for_sampler(d, grid, q, top_p, lo, hi)
    lo, hi = max(plo, lo), min(phi, hi)
    if lo > hi:
        lo, hi = plo, phi
    pool = {"allowed": [program], "balance": 0, "ranges": {program: (lo, hi)}}
    if plan.clean_sliders(sl)["strict"] >= 6:       # a relaxed scale lets the sampler wander; finish_melody then limits it
        pool["pitch_classes"] = t.scale_pcs(key)
    weights = plan.form_weights(form, kn)
    bias = plan.shape_weights(plan.from_sliders(sl)["shape"], kn)
    if bias:
        pool["melodic"] = dict(bias, instrument=program)
    if weights.get("chord"):
        pool["regions"] = plan.plan_regions(section, tempo, start_tick)
        pool["chord_weight"] = weights["chord"]
    targets = []
    if weights.get("motif"):
        for i in range(len(section["phrases"])):
            targets += plan.motif_targets(motif, section, tempo, i, start_tick)
    if weights.get("cadence"):
        near = sum(p for p, _, _ in ctx[-4:]) / max(len(ctx[-4:]), 1)
        targets += plan.cadence_targets(section, near, tempo, start_tick)
    if targets:
        pool.setdefault("melodic", {"instrument": program}).update({
            "targets": targets, "target_weight": max(weights.get("motif", 0), weights.get("cadence", 0)),
            "target_tol": q * kn["tol"], "target_sigma": kn["sigma"],
        })
    ev = generate(model, seed_sec, length, inputs=seed_events, top_p=top_p, grid=grid, instruments=pool)
    raw = [((n - NOTE_OFFSET) % 128, tt / 100 * tempo / 60, dd / 100 * tempo / 60)
           for tt, dd, n in zip(ev[0::3], ev[1::3], ev[2::3])]
    return [(p, s - ctx_beats, dd) for p, s, dd in raw if s >= ctx_beats - 1e-6]
