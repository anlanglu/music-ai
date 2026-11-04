"""Text prompt -> local LLM melody (MLX) -> MIDI -> Anticipatory Music Transformer accompaniment.

CLI:
    .venv/bin/python text2music.py "a sad, slow melody in D minor" --bars 8
GUI:
    open Composer.app   (Swift app; talks to backend.py)
"""
import argparse
import json
import math
import time
from pathlib import Path


import dynamics as dynamics_mod
import plan
import variety
from llmjson import extract_json
from playable import fold_pitch, limits, sampler_pool
from config import ATTEMPTS, BEAT_BOOST, COMPLEXITY, DEFAULT_FORM, ENSEMBLE, GM_NAMES, LLMS, MAX_FAILED_ATTEMPTS, MIN_DENSITY, TEMPERATURE, TIME_SIGS, TOP_P, VOCAL_CHANNEL, VOCAL_PROGRAM, bar_beats, instrument_name  # noqa: F401  (re-exported for callers)
from keys import PC_NAMES, estimate_key, key_for_file, key_label, limit_outside, outside_share, parse_key, parse_mode, plan_for_file, plan_json, resolve_ending, scale_names, scale_pcs, snap_to_key  # noqa: F401  (re-exported for callers)
from melody_llm import _clean_notes, _json_with_notes, conform_melody, llm_melody  # noqa: F401  (re-exported for callers)
from midifiles import _rebuild_track, _track_notes, add_layers_file, clear_layers_file, conductor_track, is_acc_track, layers_of, melody_velocities, midi_info, part_range, piece_bars, read_acc, retime, vocal_channel, write_melody_midi  # noqa: F401  (re-exported for callers)
from models import _generate, _get_ant, _get_llm  # noqa: F401  (re-exported for callers)
from parts import approve_part, interpret_direction, make_playable  # noqa: F401  (re-exported for callers)
from sessions import _write_meta, current_session, ensure_session, new_session, open_session, rename_session, seed_everything, session_info  # noqa: F401  (re-exported for callers)


def guidance_text(sliders, motif=None, key=None):
    """The character sliders (0..10), said to the LLM in words. A language model cannot be told 'smoothness 3'; it can
    be told what the line should do."""
    s = plan.clean_sliders(sliders)
    out = []
    if s["shape"] < 2.5:
        out.append("Shape: move mostly by step (seconds, sometimes thirds), with no leap larger than a fourth; smooth and singable.")
    elif s["shape"] > 7.5:
        out.append("Shape: angular. Use frequent leaps of a fourth or more, often answered by a step the other way.")
    else:
        out.append("Shape: a mix of steps and leaps.")
    if s["runs"] > 7.5:
        out.append("Scale runs: never play more than three notes in a row in one direction along the scale; break up any "
                   "run with a leap or a turn.")
    elif s["runs"] > 5.5:
        out.append("Scale runs: never play more than four notes in a row in one direction along the scale.")
    elif s["runs"] > 2.5:
        out.append("Scale runs: avoid long runs; no more than five notes in a row in one direction.")
    if s["pulse"] < 2.5:
        out.append("Rhythm: loose and syncopated; start many notes off the beat and tie across the barline.")
    elif s["pulse"] > 7.5:
        out.append("Rhythm: square and regular; start almost every note on a beat.")
    if s["theme"] > 0 and motif:
        how = "nearly note for note" if s["theme"] > 6 else "recognisably but varied"
        out.append(f"Theme: begin each phrase with the piece's opening idea {json.dumps([[p, st, d] for p, st, d in motif])} "
                   f"(start_beat, duration_beats), moved to fit that phrase's chord, {how}.")
    if key:
        if s["strict"] >= 7:
            out.append("Scale rule: use only the notes of the scale.")
        elif s["strict"] >= 3:
            out.append("Scale rule: use the scale's notes; an occasional chromatic passing note on a weak beat is fine.")
        else:
            out.append("Scale rule: the scale is the home base, but chromatic notes and notes borrowed from other scales are welcome.")
    if key and plan.is_modal(key[1]) and s["flavor"] > 2.5:
        semis, what = plan.FLAVOR[key[1]]
        note = PC_NAMES[(key[0] + semis) % 12]
        if s["flavor"] > 7.5:
            out.append(f"Mode: this is {key_label(key)}, not major or minor. Make {what} ({note}) prominent: use it on strong "
                       f"beats and held notes, several times in every phrase, and avoid a dominant-to-tonic cadence.")
        else:
            out.append(f"Mode: this is {key_label(key)}, not major or minor. Use {what} ({note}) regularly so the mode is heard.")
    return "\n".join(out)


def pick_key(prompt, llm, log):
    """Ask the language model for the key that suits a request (a quick call, before any notes are written).
    Falls back to a key named in the prompt, then to C major / C minor from the mood."""
    named = parse_key(prompt)
    if named:
        return named
    model, tok = _get_llm(llm)
    msgs = [{"role": "system", "content": 'Reply with ONLY a JSON object: {"key": "<tonic> <major|minor>", "why": "<one short '
                                          'sentence>"}. Choose the key that best suits the music requested.'},
            {"role": "user", "content": prompt}]
    text = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
    raw = _generate(model, tok, text, 120)
    data = extract_json(raw, "key")
    key = parse_key(str(data.get("key", ""))) if data else None
    if key is None:
        key = (0, plan.mode_for(prompt))
        log(f"  ▸ The LLM gave no usable key; using {key_label(key)}.")
    else:
        log(f"  ▸ Key chosen by the LLM: {key_label(key)}" + (f" ({_short(data.get('why', ''), 160)})" if data.get("why") else ""))
    return key


def _short(text, n=900):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def compose(prompt, bars, sig, level, llm, log, bpm=None, key=None, harmony=None, before=None, final=True,
            sliders=None, motif=None, extra=""):
    """Write the melody a short phrase at a time: small LLMs lose the plot (and stop early) over long
    spans, and each phrase can then answer / develop the one before it.
    `harmony` is the piece's plan (see plan.py); the chords of the bars this phrase covers are given to the LLM.
    `before` is the melody this continues (its last phrase, in its own beats), `final` whether the last phrase closes the
    piece, `sliders` the character sliders (said to the LLM in words), `motif` the piece's opening idea, `extra` a
    direction from the user."""
    bb = bar_beats(sig)
    chunk = 2 if level >= 4 else 4
    n_chunks = -(-bars // chunk)
    all_notes, plans, tempo, prev, declared = [], [], bpm, before, ""
    sl = plan.clean_sliders(sliders) if sliders is not None else None
    sparse = False
    if extra:
        import direction as dr

        sparse = dr.parse(extra)["density"] < 0       # "sparse", "spacious"...: don't retry for lack of notes
    off = 1 if before else 0
    chord_names = None
    if harmony and (sl is None or sl["harmony"] > 0):
        tonic, mode = harmony["key"]
        chord_names = [" then ".join(c.symbol for c in bar) if harmony.get("harmony") else plan.chord_name(tonic, mode, deg)
                       for bar, deg in zip(plan.bar_chords(harmony), harmony["chords"])]
    for c in range(n_chunks):
        n_bars = min(chunk, bars - c * chunk)
        target = COMPLEXITY[level][1] * n_bars * bb * (0.5 if sparse else 1)
        here = chord_names[c * chunk: c * chunk + n_bars] if chord_names else None
        best = None
        for attempt in range(1, 4):
            try:
                if sl is not None and sl["theme"] > 0 and motif is None and all_notes:
                    motif = [n for n in all_notes if n[1] < bb]      # the first bar written is the piece's idea
                guide = guidance_text(sl, motif, key) if sl is not None else ""
                ask = prompt
                if extra:     # the user's instruction comes first, and is spread over the parts of the addition
                    ask = (f"INSTRUCTION FROM THE USER for these new bars, which comes before everything else, including "
                           f"the original description: {extra}\nThe new bars are written in {n_chunks} part"
                           f"{'s' if n_chunks > 1 else ''}; this is part {c + 1} of {n_chunks}. Spread what the instruction "
                           f"asks for over the parts (a build or an arc runs across them, not inside each one).\n"
                           f"(The piece was originally described as: {prompt}. Follow the instruction where they differ.)")
                t, raw, idea, said_key, reading = llm_melody(
                    ask, n_bars, sig, level, llm, prev, (c + 1 + off, n_chunks + off + (0 if final else 1)), tempo, key,
                    here, guide, ending=sl is None or sl["endings"] > 0, climax=not extra)
            except ValueError as e:   # unusable reply: ask again instead of failing the whole melody
                log(f"      phrase {c + 1}/{n_chunks} attempt {attempt}: unusable reply ({str(e).splitlines()[0][:50]}), retrying")
                continue
            notes, coverage = conform_melody(raw, n_bars, sig)
            richness = min(len(notes) / target, 1.0)
            score = 2 * coverage + richness + min(len({d for _, _, d in notes}) / (level + 1), 1.0)
            if best is None or score > best[0]:
                best = (score, t, notes, idea, said_key, reading)
            if coverage >= 0.95 and richness >= 0.7:
                break
            log(f"      phrase {c + 1}/{n_chunks} attempt {attempt}: {len(notes)} notes (want ~{target:.0f}), "
                f"fills {coverage:.0%}, retrying")
        if best is None:
            raise RuntimeError("The LLM did not produce a usable melody; try again or reword the prompt")
        _, t, notes, idea, said_key, reading = best
        declared = declared or said_key  # the key the LLM says it wrote the opening phrase in
        if not notes:
            raise RuntimeError("The LLM did not produce a usable melody; try again or reword the prompt")
        tempo = tempo or max(40, min(t, 220))  # a requested bpm wins over whatever the LLM wrote
        if idea:
            plans.append(idea)
        if c == 0:       # what the language model made of the request, once per melody
            if reading:
                log(f"  ▸ The LLM understood: {_short(reading)}")
            if bpm and t and abs(t - bpm) > 2:
                log(f"  ▸ It suggested {t} bpm; your {bpm} bpm is used.")
            said = parse_key(said_key) if said_key else None
            if key and said and said != tuple(key):
                log(f"  ▸ It wrote in {said_key}; the notes are fitted to your {key_label(key)}.")
            elif said and not key:
                log(f"  ▸ It chose the key itself: {key_label(said)}.")
        if idea:
            log(f"  ▸ Its plan for phrase {c + 1}: {_short(idea)}")
        shift = c * chunk * bb
        all_notes += [(p, s + shift, d) for p, s, d in notes]
        prev = notes
        log(f"      phrase {c + 1}/{n_chunks}: {len(notes)} notes")
    return all_notes, tempo, plans, declared


def melody_score(notes, total_beats, target_density):
    """Heuristic 'interestingness' of a monophonic melody [(pitch, start, dur)] -> (score, details)."""
    from collections import Counter

    if len(notes) < 4:
        return -9.0, {}
    pitches = [p for p, _, _ in notes]
    durs = [round(d * 4) for _, _, d in notes]
    ivs = [b - a for a, b in zip(pitches, pitches[1:])]
    cnt = Counter(durs)
    share = max(cnt.values()) / len(durs)
    ent = -sum(c / len(durs) * math.log(c / len(durs)) for c in cnt.values()) / math.log(6)
    trigrams = [tuple(ivs[i : i + 3]) for i in range(len(ivs) - 2)]
    rep = (len(trigrams) - len(set(trigrams))) / max(len(trigrams), 1)
    steps = sum(abs(i) <= 2 for i in ivs) / len(ivs)
    span = max(pitches) - min(pitches)
    dens = len(notes) / total_beats
    cover = min((notes[-1][1] + notes[-1][2]) / total_beats, 1.0)
    d = {
        "rhythm": min(len(cnt) / 5, 1.0),
        "entropy": min(ent, 1.0),
        "mono": -max(share - 0.5, 0) * 3,
        "intervals": min(len(set(ivs)) / 8, 1.0),
        "range": min(span / 14, 1.0) - max(span - 30, 0) / 20,
        "steps": 1 - min(abs(steps - 0.6) / 0.4, 1.0),
        "repeat": 1 - min(abs(rep - 0.3) / 0.3, 1.0),
        "density": 1 - min(abs(dens - target_density) / target_density, 1.0),
        "cover": cover,
        "scalar": 1 - min(variety.stats(notes)["scalar"] / 0.25, 1.0),   # long one-way scale runs sound monotonous
    }
    w = {"rhythm": 2, "entropy": 1.5, "mono": 1, "intervals": 1, "range": 1, "steps": 1, "repeat": 1.5,
         "density": 1.5, "cover": 2, "scalar": 1.5}
    return sum(w[k] * v for k, v in d.items()), d


def breathe(notes, boundaries, phrasing=1):
    """Make ideas audibly separate. `boundaries` is a list of (time_in_beats, 'idea' | 'phrase'). Just before each one,
    leave a rest: the note sounding there is shortened and notes that would start inside the rest are dropped.
    phrasing 0 = flowing (no rests), 1 = balanced, 2 = spacious. Safe to apply twice."""
    idea_rest, phrase_rest = {0: (0.0, 0.0), 1: (0.5, 1.0), 2: (1.0, 1.5)}[phrasing]
    out = [list(n) for n in notes]
    for at, kind in boundaries:
        rest = phrase_rest if kind == "phrase" else idea_rest
        cut = at - rest
        if rest <= 0:
            continue
        kept_before = [n for n in out if at - 4.0 <= n[1] < cut - 1e-9]
        if len(kept_before) < 2:   # never hollow out a bar
            continue
        nxt = []
        for n in out:
            if cut - 1e-9 <= n[1] < at - 1e-9:
                continue
            if n[1] < cut - 1e-9 < n[1] + n[2]:
                n[2] = cut - n[1]
                if n[2] < 0.25 - 1e-9:
                    continue
            nxt.append(n)
        out = nxt
    return [tuple(n) for n in out]


def apply_breaths(notes, bars, sig, phrasing):
    """Leave rests between ideas (every two bars) and phrases (every four) so the melody doesn't run on without
    pause. phrasing 0 = flowing, 1 = balanced, 2 = spacious."""
    bb = bar_beats(sig)
    return breathe(notes, [(b * bb, "phrase" if b % 4 == 0 else "idea") for b in range(2, bars, 2)], phrasing)


def explain_prompt(prompt, user_key, bpm, bars, level, llm, log, what="melody"):
    """Tell the user, in the log, how the prompt is going to be used."""
    tag = "  ▸ "
    log(f"{tag}Your prompt: “{_short(' '.join(prompt.split()), 120)}”")
    log(f"{tag}How it is used: the {llm} LLM reads all of it and writes every one of the {bars} bars of the {what}, a phrase "
        f"at a time, each answering the last. The character sliders are passed to it as instructions in words.")
    named = parse_key(prompt)
    key_text = (f"key {key_label(user_key)} (set by you)" if user_key else
                f"key {key_label(named)} (named in your prompt)" if named else "key chosen by the LLM from your prompt")
    log(f"{tag}Fixed rather than guessed: {key_text}, {bpm} bpm, {bars} bars, complexity {level}.")


def finish_melody(notes, bars, sig, program, enforce, resolve, phrasing, sliders, theplan):
    """What every melody gets after the language model has written it: onto the grid, into the instrument's range and
    the key, rests between phrases, each phrase ending on its cadence note, and the last note home."""
    sl = plan.clean_sliders(sliders)
    span = limits(program)[:2]
    notes, _ = conform_melody(notes, bars, sig)
    notes = [(fold_pitch(p, program), s_, d) for p, s_, d in notes]
    if enforce:
        notes = limit_outside(notes, enforce, sl["strict"], span, plan.chord_pcs_at(theplan), plan.scale_pcs_at(theplan))
        if theplan is not None and plan.scale_pcs_at(theplan):
            pass                                                              # the bars with modes of their own: below, after the cadences
        elif plan.is_modal(enforce[1]):
            notes = plan.flavor_notes(notes, enforce, sl["flavor"], span)    # bring in the note that gives the mode its sound
    notes = apply_breaths(notes, bars, sig, phrasing)
    if enforce and theplan is not None and sl["endings"] > 0:
        notes = plan.resolve_phrase_endings(notes, theplan, enforce, span)
    if resolve:
        notes = resolve_ending(notes, bars, sig, enforce, span)
    if enforce and theplan is not None and plan.scale_pcs_at(theplan):
        notes = plan.flavor_by_bar(notes, theplan, sl["flavor"], span)       # the note that gives each bar's mode its sound
    return notes


def melody_candidates(prompt, bars, sig, level, llm, program, count, log, bpm=None, resolve=True, key=None,
                      phrasing=1, sliders=None, theplan=None, mode=None, progression=None, modes=None):
    """Yield `count` melodies as dicts {notes, tempo, score}, each written in full by the language model.
    The key is the one given, else one named in the prompt, else one the LLM picks for the whole batch. `mode` (a name
    from plan.MODES) overrides the mode of whichever key that is."""
    total_beats = bars * bar_beats(sig)
    target = COMPLEXITY[level][1]
    sl = plan.clean_sliders(sliders)
    enforce = key or parse_key(prompt)
    explain_prompt(prompt, key, bpm or 90, bars, level, llm, log)
    if not enforce:
        enforce = pick_key(prompt, llm, log)
    mode = mode if mode in plan.MODES else parse_mode(prompt)         # the picker wins, then a mode named in the prompt
    if mode and enforce[1] != mode:
        enforce = (enforce[0], mode)
        log(f"  ▸ Mode: {key_label(enforce)}.")
    log(f"  ▸ Allowed notes in {key_label(enforce)}: {scale_names(enforce)}"
        + ("." if sl["strict"] >= 10 else f" (relaxed: up to {round((10 - sl['strict']) * 2.5)}% of the notes may be outside)."))
    if theplan is None and (progression or modes or plan.from_sliders(sl)["form"] > 0):
        theplan = plan.build_plan(bars, sig, enforce)
        if modes:
            plan.set_modes(theplan, modes)
            log(f"  ▸ Mode by bar: {' '.join(m or '-' for m in theplan['modes'])}")
        if progression:
            plan.set_harmony(theplan, progression)
            log(f"  ▸ Chords: {' | '.join(' '.join(b) for b in theplan['harmony'])}")
    for _ in range(count):
        notes, tempo, _plans, _declared = compose(prompt, bars, sig, level, llm, log, bpm, enforce, harmony=theplan,
                                                  sliders=sl)
        notes = finish_melody(notes, bars, sig, program, enforce, resolve, phrasing, sl, theplan)
        if sl["strict"] < 10:
            log(f"      {outside_share(notes, enforce):.0%} of this melody's notes are outside {key_label(enforce)}.")
        yield {"notes": notes, "tempo": tempo, "score": melody_score(notes, total_beats, target)[0], "key": enforce,
               "plan": theplan}


def best_melody(prompt, bars, sig, level, llm, program, candidates, tempo_hint, log, resolve=True, key=None, phrasing=1,
                sliders=None, mode=None):
    """Best-scoring melody out of `candidates` (the command-line one-shot mode)."""
    best = None
    for k, c in enumerate(melody_candidates(prompt, bars, sig, level, llm, program, candidates, log, tempo_hint, resolve,
                                            key, phrasing, sliders, mode=mode), 1):
        log(f"      melody candidate {k}/{candidates}: {len(c['notes'])} notes, score {c['score']:.1f}")
        if best is None or c["score"] > best["score"]:
            best = c
    if best is None:
        raise RuntimeError("The LLM produced no melody")
    return best


def make_melodies(prompt, bars=8, program=0, sig="4/4", complexity=3, llm="7B", count=4, name=None, log=print,
                  on_candidate=None, bpm=None, resolve=True, key=None, phrasing=1, sliders=None, mode=None,
                  progression=None, modes=None):
    """Stage 1: write `count` melody options to output/<name>-optN.mid. Returns a list of dicts
    {index, path, tempo, sig, score}; on_candidate(dict) is called as each one is ready."""
    folder = ensure_session(prompt)
    name = name or time.strftime("%H%M%S")
    total_beats = bars * bar_beats(sig)
    out = []
    log(f"Composing {count} melody options...")
    for c in melody_candidates(prompt, bars, sig, complexity, llm, program, count, log, bpm, resolve, key, phrasing, sliders,
                               mode=mode, progression=progression, modes=modes):
        if not out:    # the first one has chosen the key and the plan, which the accompaniment will follow
            _write_meta(folder, key=list(c["key"]), prompt=prompt, level=complexity,
                        plan=plan_json(c["plan"], plan.from_sliders(sliders)["form"]) if c["plan"] else None,
                        form=plan.from_sliders(sliders)["form"])
        k = len(out) + 1
        path = folder / f"{name}-opt{k}.mid"
        write_melody_midi(path, c["tempo"], c["notes"], program, sig, total_beats,
                          depth=plan.clean_sliders(sliders)["dynamics"] / 5)
        info = {"index": k, "path": path, "tempo": c["tempo"], "sig": sig, "score": c["score"]}
        out.append(info)
        log(f"      option {k}: {len(c['notes'])} notes, {c['tempo']} bpm")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError("No usable melody was produced; try again")
    return out


def make_music(
    prompt,
    bars=8,
    program=0,
    size="small",
    melody_only=False,
    name=None,
    log=print,
    sig="4/4",
    complexity=3,
    llm="7B",
    ensemble=0,
    candidates=3,
    source_midi=None,
    bpm=None,
    resolve=True,
    playable=True,
    key=None,
    in_key=True,
    phrasing=1,
    sliders=None,
    dynamics=5.0,
    layered=False,
):
    """Run the whole pipeline. Returns (melody_path, full_path or None). With `layered` (and source_midi) the full piece is
    the source file plus the new instruments as accompaniment layers, as the app's step 4 keeps them, instead of a file
    the model has written again.

    With source_midi, the melody step is skipped: the imported file's notes are kept exactly as they are
    and Anticipation orchestrates around them (adding instruments the file doesn't already have)."""
    folder = ensure_session(prompt or (Path(source_midi).stem if source_midi else ""))
    name = name or time.strftime("%H%M%S")
    melody_path = folder / f"{name}-melody.mid"

    from anticipation import ops
    from anticipation.convert import events_to_midi, midi_to_events
    from anticipation.sample import generate
    from anticipation.tokenize import extract_instruments

    if source_midi:
        import shutil

        log("[1/3] Reading imported MIDI...")
        tempo, sig, length = midi_info(source_midi)
        if length > 100:  # Anticipation's clock tops out at 100 s
            log(f"      source is {length:.0f}s long; using the first 100 s")
            length = 100.0
        melody_path = folder / f"{name}-source.mid"
        shutil.copyfile(source_midi, melody_path)
        log(f"      {tempo} bpm, {sig}, {length:.1f}s -> {melody_path.name}")
        log("[2/3] Loading Anticipatory Music Transformer...")
        model = _get_ant(size)
        events = midi_to_events(str(melody_path))
        present = set(ops.get_instruments(events))
        if not present:
            raise RuntimeError("No notes found in the imported MIDI file")
        _, melody = extract_instruments(events, list(present))  # every source note becomes a fixed control
        melody = ops.clip(melody, 0, length, clip_duration=True) if length >= 100 else melody
        events = []
        log(f"      {len(present)} source instrument(s) kept as-is")
    else:
        present = {program}
        log("[1/3] LLM composing melody...")
        total_beats = bars * bar_beats(sig)
        best = best_melody(prompt, bars, sig, complexity, llm, program, candidates, bpm, log, resolve, key, phrasing, sliders)
        notes, tempo = best["notes"], best["tempo"]
        _write_meta(folder, key=list(best["key"]), prompt=prompt, level=complexity,
                    plan=plan_json(best["plan"], plan.from_sliders(sliders)["form"]) if best["plan"] else None,
                    form=plan.from_sliders(sliders)["form"])     # the accompaniment and the sheet music follow these
        length = write_melody_midi(melody_path, tempo, notes, program, sig, total_beats)
        log(f"      {len(notes)} notes, {tempo} bpm, {sig}, {length:.1f}s -> {melody_path}")
        if melody_only:
            return melody_path, None

        log("[2/3] Loading Anticipatory Music Transformer...")
        model = _get_ant(size)
        events = midi_to_events(str(melody_path))
        events, melody = extract_instruments(events, [program])
        if not melody:
            raise RuntimeError("Melody was not found after tokenizing; check the melody instrument")

    # Metrical grid in Anticipation's clock (100 ticks/s): accompaniment notes must start and end on
    # eighth-note positions, and onsets on beats / downbeats get a boost so the meter is audible.
    q = 6000.0 / tempo  # ticks per quarter note
    num, den = (int(x) for x in sig.split("/"))
    grid = {
        "step": q / 2,
        "beat": q * 1.5 if den == 8 and num % 3 == 0 else q,
        "bar": q * bar_beats(sig),
        "max_gap": q * (1 if ensemble >= 4 else 2),  # keep a large ensemble busy
        "beat_boost": BEAT_BOOST,
        "bar_boost": BEAT_BOOST * 2,
    }

    pool = None
    if ensemble > 0:
        chosen = [i for i in ENSEMBLE if i not in present][: max(0, min(ensemble, 16 - len(present)))]
        pool = sampler_pool(chosen, balance=1.5) if playable else {"allowed": chosen, "balance": 1.5}
        log(f"      ensemble: {len(present)} source/melody + {len(chosen)} added instruments")
    elif playable:  # model's choice of instruments, but each still held to its own range and polyphony
        pool = sampler_pool([i for i in range(129) if i not in present], free=True)
    if pool is not None and playable:
        grid["max_chord"] = min(24, max(4, 3 * len(pool["allowed"])))  # per-instrument limits do the real work
    skey = key_for_file(melody_path) if in_key else None   # the key the melody was generated in, if one was chosen
    if skey and pool is not None:
        pool["pitch_classes"] = scale_pcs(skey)
        log(f"      keeping the added parts in {key_label(skey)}")
    theplan = plan_for_file(melody_path)
    if theplan is not None and pool is not None:
        if pool.get("pitch_classes") and plan.plan_harmony_pcs(theplan):
            pool["pitch_classes"] = set(pool["pitch_classes"]) | plan.plan_harmony_pcs(theplan)   # the written chords' own notes
        if pool.get("pitch_classes") and plan.scale_regions(theplan, tempo):
            pool["scale_regions"] = plan.scale_regions(theplan, tempo)                          # each bar's own scale
        w = plan.form_weights(theplan.get("form", DEFAULT_FORM))
        if w.get("chord"):
            pool["regions"] = plan.plan_regions(theplan, tempo)   # ... and on the melody's own chords
            pool["chord_weight"] = w["chord"]
            log("      the added parts follow the melody's chords")

    log("[3/3] Generating accompaniment (this can take a few minutes)...")
    # The model sometimes picks a first event past the end of the clip and returns nothing (or a
    # very sparse part), so resample until the accompaniment has a reasonable note density.
    min_notes = int(MIN_DENSITY * length)
    if ensemble > 0:  # a big ensemble should give every instrument a real part, not a single note each
        min_notes = max(min_notes, 6 * len(pool["allowed"]))
    best = []
    for attempt in range(1, ATTEMPTS + 1):
        accomp = generate(model, 0, length, inputs=events, controls=melody, top_p=TOP_P, grid=grid, instruments=pool)
        n = len(accomp) // 3
        if n > len(best) // 3:
            best = accomp
        if n >= min_notes:
            break
        log(f"      attempt {attempt}: only {n} accompaniment notes (want >= {min_notes}), retrying")
    if not best:
        raise RuntimeError("Anticipation produced no accompaniment; try again")
    if playable:
        best, (folded, trimmed, dropped) = make_playable(best)
        log(f"      playability pass: {folded} notes folded into range, {trimmed} overlaps trimmed, {dropped} dropped")
    used = sorted(ops.get_instruments(best))
    log(f"      accompaniment instruments used: {len(used)}")
    out = folder / f"{name}-full.mid"
    if layered and source_midi:
        from anticipation.vocab import DUR_OFFSET, NOTE_OFFSET, TIME_OFFSET
        layers = {}
        for tt, dd, n in zip(best[0::3], best[1::3], best[2::3]):
            layers.setdefault((n - NOTE_OFFSET) // 128, []).append(
                ((n - NOTE_OFFSET) % 128, (tt - TIME_OFFSET) / 100 * tempo / 60, (dd - DUR_OFFSET) / 100 * tempo / 60))
        add_layers_file(melody_path, out, layers)
        dynamics_mod.finish(out, melody_path, sig, dynamics)
        log(f"Done: {out}")
        return melody_path, out
    full = ops.combine(best, melody)
    events_to_midi(full).save(str(out))
    retime(out, tempo, sig)
    dynamics_mod.finish(out, melody_path, sig, dynamics)           # the melody's own dynamics come back; the parts follow it
    log(f"Done: {out}")
    return melody_path, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--bars", type=int, default=8)
    ap.add_argument("--bpm", type=int, default=80, help="tempo in beats per minute (40-220)")
    ap.add_argument("--sig", default="4/4", choices=TIME_SIGS, help="time signature")
    ap.add_argument("--program", type=int, default=0, help="GM program for the melody (0=piano, 40=violin, 73=flute)")
    ap.add_argument("--size", default="small", choices=["small", "medium", "large"], help="Anticipation model size")
    ap.add_argument("--complexity", type=int, default=3, choices=[1, 2, 3, 4, 5], help="melody complexity (1 simple .. 5 virtuosic)")
    ap.add_argument("--llm", default="7B", choices=list(LLMS), help="melody LLM (14B downloads ~8 GB on first use)")
    ap.add_argument("--ensemble", type=int, default=0, help="accompaniment instruments, 1-15 (0 = model's choice)")
    ap.add_argument("--candidates", type=int, default=3, help="melodies to generate and score; the best one is used")
    ap.add_argument("--source-midi", default=None, help="orchestrate this MIDI file instead of generating a melody")
    ap.add_argument("--key", default=None, help='force a key, e.g. "D minor" or "Bb major"')
    ap.add_argument("--phrasing", type=int, default=1, choices=[0, 1, 2], help="rests between ideas: 0 flowing, 1 balanced, 2 spacious")
    for name, text in [("smooth-jumpy", "0 smooth .. 10 jumpy"), ("pulse", "how strongly notes start on the beat"),
                       ("harmony", "how much the melody fits each bar's chord"),
                       ("endings", "how strongly phrases end on a stopping note"),
                       ("theme", "how strongly the opening idea returns in each phrase"),
                       ("flavor", "how strongly a mode's own note is used (dorian, phrygian, lydian, mixolydian)"),
                       ("strict", "0 relaxed .. 10 strict: how closely the notes keep to the key or mode"),
                       ("dynamics", "0 flat .. 10 expressive: how much the loudness varies (melody and accompaniment)")]:
        ap.add_argument("--" + name, type=float, default=None, help=f"{text} (0..10; default {plan.SLIDER_DEFAULTS[name.replace('-', '_').replace('smooth_jumpy', 'shape')]})")
    ap.add_argument("--seed", type=int, default=None, help="random seed, for repeatable output")
    ap.add_argument("--no-resolve", action="store_true", help="don't force the melody to end on the tonic")
    ap.add_argument("--no-playable", action="store_true", help="skip the instrument range / polyphony limits")
    ap.add_argument("--melody-only", action="store_true", help="Skip Anticipation, just save the LLM melody")
    ap.add_argument("--name", default=None, help="file name stem for the outputs")
    ap.add_argument("--session", default=None, help="session (folder) name; defaults to Session <date> <time>")
    a = ap.parse_args()
    if a.seed is not None:
        seed_everything(a.seed)
    new_session(a.session, a.prompt)  # each command-line run is its own session
    make_music(
        a.prompt, a.bars, a.program, a.size, a.melody_only, a.name,
        sig=a.sig,
        complexity=a.complexity, llm=a.llm, ensemble=a.ensemble,
        candidates=a.candidates, source_midi=a.source_midi,
        bpm=max(40, min(a.bpm, 220)), resolve=not a.no_resolve, playable=not a.no_playable, key=parse_key(a.key or ""),
        phrasing=a.phrasing, sliders=plan.clean_sliders({"shape": a.smooth_jumpy, "pulse": a.pulse, "harmony": a.harmony,
                                                         "endings": a.endings, "theme": a.theme,
                                                         "flavor": a.flavor, "strict": a.strict,
                                                         "dynamics": a.dynamics}),
        dynamics=plan.clean_sliders({"dynamics": a.dynamics})["dynamics"],
    )


if __name__ == "__main__":
    main()