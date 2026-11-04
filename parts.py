"""Accompaniment parts: sampling instruments with the Anticipatory Music Transformer, texture options and directions."""
import time
from pathlib import Path

import mido
import numpy as np

import dynamics as dynamics_mod
import plan
from playable import DRUMS, fold_pitch, limits, sampler_pool
from config import ATTEMPTS, BEAT_BOOST, DEFAULT_FORM, TOP_P, bar_beats, instrument_name
from keys import estimate_key, key_for_file, key_label, plan_for_file, scale_pcs
from midifiles import _melody_program_of, add_layers_file, midi_info, part_range
from models import _generate, _get_ant, _get_llm, _llm
from sessions import ensure_session, slugify


def make_playable(events):
    """Cleanup pass over generated accompaniment tokens so every part is something a person could play:
    notes outside an instrument's range are folded by octaves into it, a single-note instrument's overlapping
    notes are trimmed legato (and a second note at the very same instant is dropped), and an instrument is never
    asked for more simultaneous notes than it has hands/strings/fingers for. Drums are left alone.
    Returns (tokens, (folded, trimmed, dropped))."""
    from anticipation.vocab import DUR_OFFSET, NOTE_OFFSET, TIME_OFFSET

    by_instr, folded, trimmed, dropped = {}, 0, 0, 0
    for t, d, n in zip(events[0::3], events[1::3], events[2::3]):
        note = n - NOTE_OFFSET
        by_instr.setdefault(note // 128, []).append([t - TIME_OFFSET, d - DUR_OFFSET, note % 128])
    out = []
    for instr, notes in by_instr.items():
        if instr == DRUMS:
            out += [[t, d, instr, p] for t, d, p in notes]
            continue
        poly = limits(instr)[2]
        for nt in notes:
            p = fold_pitch(nt[2], instr)
            folded += p != nt[2]
            nt[2] = p
        notes.sort(key=lambda x: (x[0], x[2]))
        kept = []
        for nt in notes:
            active = [k for k in kept if k[0] + k[1] > nt[0]]  # still sounding when this one starts
            if poly == 1:
                if any(k[0] == nt[0] for k in active):
                    dropped += 1
                    continue
                for k in active:  # legato: the previous note ends where this one begins
                    k[1] = max(nt[0] - k[0], 1)
                    trimmed += 1
            elif len(active) >= poly:
                dropped += 1
                continue
            kept.append(nt)
        out += [[t, d, instr, p] for t, d, p in kept]
    out.sort(key=lambda x: (x[0], x[2], x[3]))
    tokens = []
    for t, d, instr, p in out:
        tokens += [TIME_OFFSET + t, DUR_OFFSET + max(d, 1), NOTE_OFFSET + instr * 128 + p]
    return tokens, (int(folded), trimmed, dropped)


def add_part_options(source_midi, program, count=3, size="small", playable=True, name=None, log=print,
                     on_candidate=None, texture_style=None, in_key=True, direction="", dynamics=5.0,
                     from_bar=None, to_bar=None):
    """Write `count` alternative parts for ONE instrument, each as a complete piece: the existing piece (held fixed)
    plus a freshly generated part for `program`, in bars `from_bar` to `to_bar` only (default: the whole piece). Files go
    to <session>/parts/. on_candidate(info) fires as each is ready. Approving an option just means using its file as the
    piece for the next round."""
    import direction as dr
    import texture as tx

    # Read the direction (an LLM understands free text; keywords are the fallback), with enough context to be sensible
    context = "unknown key"
    try:
        piece = tx.piece_notes(source_midi)
        t0, s0, _ = midi_info(source_midi)
        k0 = key_for_file(source_midi) or (estimate_key([(p, st, du) for p, st, du, _ in piece]) if piece else None)
        mel = tx.melody_program(piece)
        names = sorted({instrument_name(pr) + (" (melody)" if pr == mel else "") for _, _, _, pr in piece})
        context = f"{key_label(k0) if k0 else 'unknown key'}, {s0}, {t0} bpm. Already playing: {', '.join(names) or 'nothing'}."
    except Exception:
        pass
    d = interpret_direction(direction, instrument_name(program), context, log)
    if not d["empty"]:
        log("      " + dr.describe(d))
    style = d["style"] or texture_style   # a pattern named in the direction overrides the pattern picker
    if style:  # rule-based chords / arpeggios / bass lines instead of a model-generated part
        return texture_options(source_midi, style, count, name, log, on_candidate, program, d, dynamics, from_bar, to_bar)

    from anticipation import ops
    from anticipation.convert import midi_to_events
    from anticipation.sample import generate
    from anticipation.tokenize import extract_instruments
    from anticipation.vocab import DUR_OFFSET, NOTE_OFFSET, TIME_OFFSET

    folder = ensure_session(Path(source_midi).stem)
    parts_dir = folder / "parts"
    parts_dir.mkdir(exist_ok=True)
    name = name or time.strftime("%H%M%S")
    label = instrument_name(program)

    tempo, sig, length = midi_info(source_midi)
    length = min(length, 100.0)  # Anticipation's clock tops out at 100 s
    lo_beat, hi_beat, lo_bar, hi_bar = part_range(source_midi, program, from_bar, to_bar)
    start_sec, end_sec = lo_beat * 60 / tempo, min(hi_beat * 60 / tempo, length)
    if start_sec >= end_sec:
        raise RuntimeError("Anticipation can only play along with the first 100 seconds of a piece.")
    events = midi_to_events(str(source_midi))
    present = set(ops.get_instruments(events))
    if program in present and program == _melody_program_of(source_midi):
        raise RuntimeError(f"{label} is the melody's own instrument. Pick a different instrument.")
    if len(present - {program}) >= 16:
        raise RuntimeError("The piece already has 16 instruments, the most Anticipation can handle.")
    _, controls = extract_instruments(events, list(present))  # the whole existing piece is held fixed
    if length >= 100:
        controls = ops.clip(controls, 0, length, clip_duration=True)
    span_sec = end_sec - start_sec

    q = 6000.0 / tempo
    num, den = (int(x) for x in sig.split("/"))
    poly = limits(program)[2] if playable else 4
    # What the direction can steer in a model-generated part: how busy it is, where in the instrument's range it
    # sits, how long its notes are, whether it leans on or away from the beat, whether it answers the melody
    # instead of playing with it, and how adventurous the sampling is.
    dens, reg, ln = d["density"], d["register"], d["length"]
    boost = {-2: -2.5, -1: -1.5, 1: 2.0, 2: 3.5}.get(d["rhythm"], BEAT_BOOST)
    grid = {
        "step": q / 4 if dens > 0 else q / 2,
        "beat": q * 1.5 if den == 8 and num % 3 == 0 else q, "bar": q * bar_beats(sig),
        "max_gap": q * {-2: 6, -1: 4, 0: 2, 1: 1, 2: 0.5}[dens], "max_chord": 1 if dens < 0 else max(1, poly),
        "beat_boost": boost, "bar_boost": boost * 2,
    }
    if dens < 0:
        grid["min_gap"] = int(q * {-2: 3, -1: 2}[dens])   # leave room between onsets
    if ln > 0:
        grid["min_dur"] = int(q * {1: 1.5, 2: 3}[ln])
    elif ln < 0:
        grid["max_dur"] = int(q * {-1: 1.0, -2: 0.5}[ln])
    if d["complementary"]:  # never attack together with the melody: answer it, fill its gaps
        mel_prog = tx.melody_program(tx.piece_notes(source_midi))
        if mel_prog is not None:
            to_tick = lambda beats: round(beats * 60.0 / tempo * 100)
            grid["avoid"] = np.array(sorted({to_tick(st) for _, st, _, pr in tx.piece_notes(source_midi) if pr == mel_prog}))
    lo, hi = limits(program)[:2]
    span = hi - lo
    ranges = None
    if reg:
        centre = {-2: 0.12, -1: 0.25, 1: 0.75, 2: 0.88}[reg]
        ranges = {program: (max(lo, int(lo + (centre - 0.225) * span)), min(hi, int(lo + (centre + 0.225) * span)))}
    pool = sampler_pool([program], free=True, ranges=ranges) if playable else {"allowed": [program], "free": True}
    if ranges and not playable:
        pool["ranges"] = ranges
    top_p = {-2: 0.6, -1: 0.85, 1: 1.0, 2: 1.0}.get(d["adventurous"], TOP_P)
    skey = key_for_file(source_midi) if in_key else None
    if skey and program != DRUMS:
        pool["pitch_classes"] = scale_pcs(skey)
        log(f"      keeping the part in {key_label(skey)}")
    theplan = plan_for_file(source_midi)
    if theplan is not None and program != DRUMS:
        if pool.get("pitch_classes") and plan.plan_harmony_pcs(theplan):
            pool["pitch_classes"] = set(pool["pitch_classes"]) | plan.plan_harmony_pcs(theplan)   # the written chords' own notes
        if pool.get("pitch_classes") and plan.scale_regions(theplan, tempo):
            pool["scale_regions"] = plan.scale_regions(theplan, tempo)                          # each bar's own scale
        w = plan.form_weights(theplan.get("form", DEFAULT_FORM))
        if w.get("chord"):
            pool["regions"] = plan.plan_regions(theplan, tempo)   # ... and on the melody's own chords
            pool["chord_weight"] = w["chord"]
            log("      the part follows the melody's chords")
    min_notes = {-2: max(3, int(0.08 * span_sec)), -1: max(4, int(0.15 * span_sec)), 0: max(3, int(0.5 * span_sec)),
                 1: max(5, int(1.0 * span_sec)), 2: max(7, int(1.5 * span_sec))}[dens]
    model = _get_ant(size)

    log(f"Writing {count} options for {label}...")
    out = []
    for k in range(1, count + 1):
        best = []
        for _attempt in range(1, ATTEMPTS + 1):
            part = generate(model, start_sec, end_sec, inputs=[], controls=controls, top_p=top_p, grid=grid, instruments=pool)
            n = len(part) // 3
            if n > len(best) // 3:
                best = part
            if n >= min_notes:
                break
        if not best:
            log(f"      option {k}: the model produced nothing, skipping")
            continue
        if playable:
            best, _ = make_playable(best)
        path = parts_dir / f"{name}-{slugify(label)}-b{lo_bar}-{hi_bar}-{k}.mid"
        notes = [((n - NOTE_OFFSET) % 128, (tt - TIME_OFFSET) / 100 * tempo / 60, (dd - DUR_OFFSET) / 100 * tempo / 60)
                 for tt, dd, n in zip(best[0::3], best[1::3], best[2::3])]
        add_layers_file(source_midi, path, {program: notes}, lo_beat, hi_beat)
        dynamics_mod.finish(path, source_midi, sig, dynamics)    # the model writes every note at one velocity
        if velocity_for(d):
            apply_velocity(path, program, velocity_for(d))
        info = {"index": len(out) + 1, "path": path, "program": program, "added": len(notes)}
        out.append(info)
        log(f"      option {info['index']}: {info['added']} {label} notes")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError(f"No usable {label} part was produced; try again")
    return out


def velocity_for(d):
    """MIDI velocity for a direction's dynamics (-2 very soft .. 2 very loud); None leaves the default."""
    return {-2: 30, -1: 45, 1: 95, 2: 115}.get((d or {}).get("dynamics", 0))


def apply_velocity(path, program, velocity):
    """Set how hard one instrument is played in a file written by this app: its notes keep their relative dynamics, and
    their average moves to `velocity`."""
    mid = mido.MidiFile(str(path))
    for trk in mid.tracks:
        notes = [m for m in trk if m.type == "note_on"]
        if not notes:
            continue
        drums = notes[0].channel == 9
        prog = next((m.program for m in trk if m.type == "program_change"), 0)
        if (drums and program == DRUMS) or (not drums and prog == program):
            on = [m for m in notes if m.velocity > 0]
            if not on:
                continue
            shift = velocity - sum(m.velocity for m in on) / len(on)
            for m in on:
                m.velocity = int(max(20, min(127, round(m.velocity + shift))))
    mid.save(str(path))


def texture_options(source_midi, style="mix", count=3, name=None, log=print, on_candidate=None, program=0, d=None,
                    dynamics=5.0, from_bar=None, to_bar=None):
    """Chords / arpeggios / bass lines for one instrument under the existing piece, by music theory (see texture.py).
    The piece is left exactly as it is; each option is the whole piece plus a different pattern, written to
    <session>/parts/. If the instrument is already there and is a keyboard (e.g. the melody is on piano), the new
    notes join that same instrument, like a left hand."""
    import texture as tx
    from anticipation import ops
    from anticipation.convert import midi_to_events

    d = d or {}
    folder = ensure_session(Path(source_midi).stem)
    parts_dir = folder / "parts"
    parts_dir.mkdir(exist_ok=True)
    name = name or time.strftime("%H%M%S")
    tempo, sig, length = midi_info(source_midi)
    notes = tx.piece_notes(source_midi)
    if not notes:
        raise RuntimeError("There are no pitched notes to harmonise")
    key = key_for_file(source_midi) or estimate_key([(p, s_, d_) for p, s_, d_, _ in notes])
    events = midi_to_events(str(source_midi))
    present = list(ops.get_instruments(events))
    lo_beat, hi_beat, lo_bar, hi_bar = part_range(source_midi, program, from_bar, to_bar)
    if program in present and program == _melody_program_of(source_midi) and program not in tx.LEFT_HAND:
        raise RuntimeError(f"{instrument_name(program)} is the melody's own instrument. Pick a different instrument.")
    label0 = instrument_name(program)
    log(f"Writing {count} {label0.lower()} options ({key_label(key)})...")
    out = []
    theplan = plan_for_file(source_midi)
    plan_chords = None
    if theplan is not None and plan.form_weights(theplan.get("form", DEFAULT_FORM)).get("chord"):
        plan_chords = plan.bar_chords(theplan) if theplan.get("harmony") or theplan.get("modes") else theplan["chords"]
    for k in range(1, count + 1):
        label, part = tx.option(notes, tempo, sig, key, style, k, program, d, plan_chords, plan.scale_pcs_at(theplan))
        path = parts_dir / f"{name}-{slugify(label0)}-{slugify(label)}-b{lo_bar}-{hi_bar}-{k}.mid"
        add_layers_file(source_midi, path, {program: sorted(part, key=lambda n: (n[1], n[0]))}, lo_beat, hi_beat)
        dynamics_mod.finish(path, source_midi, sig, dynamics)
        if velocity_for(d):
            apply_velocity(path, program, velocity_for(d))
        info = {"index": k, "path": path, "program": program,
                "added": sum(1 for _p, st, _d in part if lo_beat - 1e-6 <= st < hi_beat - 1e-6), "label": label}
        out.append(info)
        log(f"      option {k}: {label}, {len(part)} notes")
        if on_candidate:
            on_candidate(info)
    return out


def approve_part(candidate_path, label):
    """Keep a chosen option: copy it into the session folder as the next numbered version of the piece."""
    import shutil

    folder = ensure_session()
    n = len(list(folder.glob("piece*.mid"))) + 1
    dest = folder / f"piece{n:02d}-{slugify(label)}.mid"
    shutil.copyfile(candidate_path, dest)
    return dest


def interpret_direction(text, instrument, context, log=print, model_key=None):
    """Free-text direction -> controls. A language model reads it (so "something dreamy that floats underneath" works);
    the keyword matcher is the fallback when the LLM is unavailable or returns nothing usable."""
    import direction as dr

    keywords = dr.parse(text)
    if keywords["empty"]:
        return keywords
    # Prefer whichever LLM is already in memory; otherwise the 7B (reads directions best), then the lighter 3B.
    order = [model_key] if model_key else (list(_llm) or []) + [k for k in ("7B", "3B") if k not in _llm]
    for key in order:
        try:
            model, tok = _get_llm(key)
            msgs = [{"role": "system", "content": dr.SYSTEM}, {"role": "user", "content": dr.build_prompt(text, instrument, context)}]
            raw = _generate(model, tok, tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False), 320)
            got = dr.from_llm(text, raw)
            if got is None:
                log(f"      (the {key} LLM's reply wasn't usable)")
                continue   # try the next model in the chain before giving up on the LLM
            return got   # may have no effect (e.g. "purple"): describe() then says so rather than guessing
        except Exception as e:
            log(f"      (the {key} LLM couldn't run: {type(e).__name__})")
    log("      (no LLM available, so keyword matching was used instead)")
    return keywords
