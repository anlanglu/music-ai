"""Lyrics to a sung melody (demo). The lyrics are kept apart from the melody: the melody is written to as many lines as
fit the bars asked for (the motif or an extension), and the next extension carries on with the next line. The words are split into syllables (CMU pronouncing dictionary), the language model writes
one note per syllable for each line, and SoulX-Singer (soulx/, run in .venv-sing) sings it: a zero-shot singing voice whose
timbre is cloned from a short reference recording (soulx/prompt/).

    .venv/bin/python lyrics.py "Twinkle twinkle little star" --key "C major" --bpm 90 --llm cc-sonnet-5-5"""
import json
import math
import re
import subprocess
import time
from pathlib import Path

import text2music as t
from llmjson import extract_json

ROOT = Path(__file__).resolve().parent
SING_PY = ROOT / ".venv-sing" / "bin" / "python"
SOULX = ROOT / "soulx"
MODEL = ROOT / "models" / "soulx" / "model.pt"
VOICE = SOULX / "prompt" / "en_prompt"        # reference singer: en_prompt.wav + .json
VOICE_PROGRAM = 53                              # the "Voice" instrument: GM Voice Oohs in the MIDI file; such melodies are written to lyrics
SYL_PER_BAR = 4                                 # about this many syllables to a bar when the model picks the rhythm
RANGE = (57, 74)                                # a comfortable singing range (the voice model also shifts to its singer)

_cmu = None


def ready():
    """(ok, what_is_missing)"""
    if not SING_PY.exists():
        return False, "The singing environment (.venv-sing) is not installed."
    if not MODEL.exists():
        return False, "The singing model (models/soulx/model.pt) is not downloaded."
    return True, ""


def pronounce(word):
    """ARPAbet phones (with stress digits) of one word, or None if the dictionary doesn't know it."""
    global _cmu
    if _cmu is None:
        import cmudict
        _cmu = cmudict.dict()
    w = re.sub(r"[^a-z']", "", word.lower()).strip("'")
    if not w:
        return None
    for cand in (w, w.replace("'", ""), w[:-2] if w.endswith("'s") else None):
        if cand and cand in _cmu:
            return _cmu[cand][0]
    return None


def syllable_stresses(phones):
    """One stress digit per syllable (a syllable has one vowel, the phone that ends in a digit)."""
    return [int(p[-1]) for p in phones if p[-1].isdigit()]


def raw_lines(text):
    """The lyric lines that have words in them."""
    return [l.strip() for l in text.splitlines() if re.search(r"[A-Za-z]", l)]


def unknown_words(raw):
    return [w for w in re.findall(r"[A-Za-z][A-Za-z']*", raw) if pronounce(w) is None]


def parse_line(raw):
    """One line as words: [{"word", "phones", "stress"}...]. Raises ValueError listing words the dictionary lacks."""
    words = []
    for w in re.findall(r"[A-Za-z][A-Za-z']*", raw):
        ph = pronounce(w)
        if ph is not None:
            words.append({"word": w, "phones": ph, "stress": syllable_stresses(ph)})
    unknown = unknown_words(raw)
    if unknown:
        raise ValueError("I don't know how to pronounce: " + ", ".join(sorted(set(unknown))) + ". Respell these words (for example phonetically).")
    return words


def parse(text):
    """Lines of words: [[{"word", "phones", "stress"}...] ...]. Raises ValueError listing words the dictionary lacks."""
    lines, unknown = [], []
    for raw in raw_lines(text):
        unknown += unknown_words(raw)
        words = [{"word": w, "phones": ph, "stress": syllable_stresses(ph)}
                 for w, ph in ((w, pronounce(w)) for w in re.findall(r"[A-Za-z][A-Za-z']*", raw)) if ph is not None]
        if words:
            lines.append(words)
    if unknown:
        raise ValueError("I don't know how to pronounce: " + ", ".join(sorted(set(unknown))) + ". Respell these words (for example phonetically).")
    if not lines:
        raise ValueError("No lyrics found.")
    return lines


def count(line):
    return sum(len(w["stress"]) for w in line)


SYSTEM = """You write a singable melody for one line of lyrics. Reply with ONLY a JSON object:
{{"plan": "<one sentence: contour and where the stressed words fall>", "notes": [[midi_pitch, start_beat, duration_beats], ...]}}
Rules: exactly {n} notes, one for each syllable, in order; time signature {sig} (one bar = {bb:g} quarter-note beats), the line
fills {bars} bar(s) and start_beat counts from 0 at the start of the line; pitches {lo}-{hi}; start_beat and duration_beats are
multiples of 0.25; notes never overlap; leave a rest only where the line breathes. Stressed syllables go on strong beats
and get longer notes; unstressed syllables are shorter. Sing in {key}, using only these notes: {scale}. Mostly stepwise with
a few leaps, a clear shape, and the last note of the line long and settled."""


def _line_prompt(line, bars, sig, key, mood, before):
    words = " ".join(f"{w['word']}({'/'.join('STRONG' if s == 1 else 'weak' for s in w['stress'])})" for w in line)
    msg = f"Lyric line ({count(line)} syllables): {words}"
    if mood:
        msg += f"\nMood and style: {mood}"
    if before:
        msg += f"\nThe previous line's melody (same format): {json.dumps([list(n) for n in before])}. Answer it, do not copy it."
    return msg


def _fit(notes, n):
    """Exactly n notes: drop the shortest extras, or split the longest notes in two."""
    notes = list(notes)
    while len(notes) > n:
        i = min(range(len(notes)), key=lambda k: (notes[k][2], k))
        if i == len(notes) - 1 and len(notes) > 1:
            i -= 1
        notes.pop(i)
    while len(notes) < n:
        i = max(range(len(notes)), key=lambda k: notes[k][2])
        p, s, d = notes[i]
        h = round(d / 2 * 4) / 4 or d / 2
        notes[i:i + 1] = [(p, s, h), (p, s + h, d - h)]
    return notes


def _tidy(notes, bars, bb):
    """Sorted, inside the line's bars, no overlaps."""
    notes = sorted(notes, key=lambda x: x[1])
    out, end = [], bars * bb
    for p, s, d in notes:
        s = min(max(s, 0.0), end - 0.25)
        if out and s < out[-1][1] + out[-1][2]:
            pp, ps, pd = out[-1]
            if s - ps >= 0.25:
                out[-1] = (pp, ps, s - ps)
            else:
                s = ps + pd
        d = min(d, end - s)
        if d > 0:
            out.append((p, s, d))
    return out


def line_melody(line, sig, key, llm, level, mood, before, log):
    n, bb = count(line), t.bar_beats(sig)
    bars = max(1, math.ceil(n / SYL_PER_BAR))
    model, tok = t._get_llm(llm)
    msgs = [{"role": "system", "content": SYSTEM.format(n=n, sig=sig, bb=bb, bars=bars, lo=RANGE[0], hi=RANGE[1],
                                                         key=t.key_label(key), scale=t.scale_names(key))},
            {"role": "user", "content": _line_prompt(line, bars, sig, key, mood, before)}]
    text = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
    best, failed = None, 0
    while failed < t.MAX_FAILED_ATTEMPTS:
        raw = t._generate(model, tok, text, 300 + 20 * n, t.TEMPERATURE[level] if hasattr(t, "TEMPERATURE") else 0.8, 0.95)
        obj = extract_json(raw, "notes")
        notes = t._clean_notes(obj["notes"]) if obj and isinstance(obj["notes"], list) else []
        notes = _tidy(notes, bars, bb)
        if len(notes) == n:
            best = notes
            break
        failed += 1
        log(f"      (the model wrote {len(notes)} notes for {n} syllables; trying again)")
        if len(notes) >= n * 0.6 and (best is None or abs(len(notes) - n) < abs(len(best) - n)):
            best = notes
    if best is None:
        raise RuntimeError("The language model gave no usable notes for a line of lyrics.")
    if len(best) != n:
        log(f"      (adjusted {len(best)} notes to the {n} syllables)")
        best = _tidy(_fit(best, n), bars, bb)
    best = t.snap_to_key(best, key, RANGE)
    return best, bars


def syllabify(phones):
    """The phones split into syllables, one vowel each: a single consonant between vowels starts the next syllable, of
    two or more the first closes the previous one."""
    nuclei = [i for i, p in enumerate(phones) if p[-1].isdigit()]
    cuts = [0]
    for a, b in zip(nuclei, nuclei[1:]):
        cuts.append(b if b - a == 1 else b - 1 if b - a == 2 else a + 2)
    cuts.append(len(phones))
    return [phones[x:y] for x, y in zip(cuts, cuts[1:])]


TOKEN_STYLE = "syllable"    # "word": the whole word on each of its notes (as in SoulX's examples) | "syllable": one syllable each


def token(word, i, k):
    """SoulX-Singer's phoneme token for one note of a word, and its note type (2 = a new word, 3 = the word goes on)."""
    if TOKEN_STYLE == "syllable" and k > 1:
        return "en_" + "-".join(syllabify(word["phones"])[i]), 2
    return "en_" + "-".join(word["phones"]), 2 if i == 0 else 3


def metadata(lines, melodies, tempo, sig, starts=None):
    """Per line, the SoulX-Singer score-control segment: <SP> rests, one token per note, durations in seconds.
    `starts` are the lines' first beats in the melody (default: one line after the other from the start)."""
    bb, spb = t.bar_beats(sig), 60.0 / tempo
    segs, line_start = [], 0.0
    for i, (line, (notes, bars)) in enumerate(zip(lines, melodies)):
        if starts is not None:
            line_start = starts[i] * spb
        syl = [(w, i, len(w["stress"])) for w in line for i in range(len(w["stress"]))]
        toks, durs, pitch, types = ["<SP>"], [], [0], [1]
        text = ["<SP>"]
        first = notes[0][1] * spb
        durs.append(max(first, 0.04))
        cursor = notes[0][1]
        for (p, s, d), (w, i, k) in zip(notes, syl):
            if s - cursor > 0.01:                      # a rest inside the line
                toks.append("<SP>"); text.append("<SP>"); durs.append((s - cursor) * spb); pitch.append(0); types.append(1)
            tk, ty = token(w, i, k)
            toks.append(tk); text.append(w["word"]); durs.append(d * spb); pitch.append(p); types.append(ty)
            cursor = s + d
        tail = bars * bb - cursor
        toks.append("<SP>"); text.append("<SP>"); durs.append(max(tail * spb, 0.04)); pitch.append(0); types.append(1)
        segs.append({"index": f"line_{len(segs)}", "language": "English",
                     "time": [round(line_start * 1000), round((line_start + bars * bb * spb) * 1000)],
                     "duration": " ".join(f"{x:.3f}" for x in durs), "text": " ".join(text), "phoneme": " ".join(toks),
                     "note_pitch": " ".join(map(str, pitch)), "note_type": " ".join(map(str, types))})
        line_start += bars * bb * spb
    return segs


def sing(segments, out_wav, log=print, n_steps=32):
    ok, why = ready()
    if not ok:
        raise RuntimeError(why)
    job = Path(out_wav).with_suffix(".job.json")
    job.write_text(json.dumps({"segments": segments, "out": str(out_wav), "model": str(MODEL), "n_steps": n_steps,
                               "prompt_wav": str(VOICE) + ".wav", "prompt_meta": str(VOICE) + ".json"}))
    p = subprocess.Popen([str(SING_PY), "-I", str(SOULX / "sing.py"), str(job)], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, cwd=str(SOULX))
    tail = []
    for line in p.stdout:
        tail.append(line.rstrip())
        if line.startswith("progress"):
            log("      singing " + line.split()[1])
    if p.wait() != 0 or not Path(out_wav).exists():
        raise RuntimeError("The singing voice failed: " + " | ".join(tail[-3:]))
    return Path(out_wav)


def is_voice(midi):
    """Whether a melody file is for the Voice instrument."""
    import expand
    try:
        return expand.read_melody(midi)[3] == VOICE_PROGRAM
    except Exception:
        return False


def sidecar(midi):
    """The file beside a melody that says which lyric lines it is written to."""
    return Path(midi).with_suffix(".lyrics.json")


def used_lines(midi):
    """[{"text", "start_beat", "bars"}] of the lyric lines a melody file was written to (none if it has no lyrics)."""
    try:
        return json.loads(sidecar(midi).read_text())["lines"]
    except (OSError, ValueError, KeyError):
        return []


def carry(src, dest):
    """A melody made from another one (an edit) keeps that melody's lyric lines."""
    lines = used_lines(src)
    if lines:
        sidecar(dest).write_text(json.dumps({"lines": lines}))


def check(text):
    """What the lyrics box shows: ([(line, syllables or None)], [unknown words])."""
    rows, unknown = [], []
    for raw in raw_lines(text):
        bad = unknown_words(raw)
        unknown += bad
        rows.append((raw, None if bad else count(parse_line(raw))))
    return rows, sorted(set(unknown))


def write_section(raws, start, room_bars, sig, key, llm, level, mood, before, log):
    """Melody for the next lyric lines that fit in room_bars (always at least one, never more than the lyrics hold).
    Returns (notes in the section's own beats, [{"text", "start_beat", "bars"}], section length in beats)."""
    bb = t.bar_beats(sig)
    picks, used = [], 0
    for raw in raws[start:]:
        try:
            parsed = parse_line(raw)
        except ValueError:
            if not picks:
                raise
            break
        bars = max(1, math.ceil(count(parsed) / SYL_PER_BAR))
        if picks and used + bars > room_bars:
            break
        picks.append((raw, parsed))
        used += bars
    notes, meta, cursor = [], [], 0.0
    for raw, parsed in picks:
        ln, bars = line_melody(parsed, sig, key, llm, level, mood, before, log)
        notes += [(p, s + cursor, d) for p, s, d in ln]
        meta.append({"text": raw, "start_beat": cursor, "bars": bars})
        cursor += bars * bb
        before = ln
        log(f"      \u201c{raw}\u201d: {count(parsed)} syllables, {bars} bar(s)")
    return notes, meta, cursor


def lyric_motifs(text, bars, program, sig, bpm, key, llm, level, mood, count_, log=print, on_candidate=None):
    """Step 1 written to the lyrics: `count_` options, each the first lines that fit in `bars` bars."""
    raws = raw_lines(text)
    if not raws:
        raise ValueError("No lyrics found.")
    folder = t.ensure_session(raws[0][:40])
    t._write_meta(folder, key=list(key), prompt=mood or raws[0], level=level)
    name = time.strftime("%H%M%S") + "-lyr"
    out, fails = [], 0
    while len(out) < count_ and fails < t.MAX_FAILED_ATTEMPTS:
        try:
            notes, meta, total = write_section(raws, 0, bars, sig, key, llm, level, mood, None, log)
        except (RuntimeError, KeyError, TypeError) as e:
            fails += 1
            log(f"      (attempt unusable, retrying: {str(e).splitlines()[0][:70]})")
            continue
        k = len(out) + 1
        path = folder / f"{name}-opt{k}.mid"
        t.write_melody_midi(path, bpm, notes, program, sig, total)
        sidecar(path).write_text(json.dumps({"lines": meta}))
        info = {"index": k, "path": path, "tempo": bpm, "sig": sig, "score": None}
        out.append(info)
        log(f"      option {k}: {len(notes)} notes to {len(meta)} lyric line(s) of {len(raws)}")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError("No usable melody was written for the lyrics; try again")
    return out


def lyric_extend(source_midi, text, add_bars, llm, level, mood, count_, log=print, on_candidate=None):
    """Step 2 written to the lyrics: the next lines that fit in `add_bars` bars are added after the melody. Returns the
    options, or None when the melody already used all the lyrics."""
    import expand

    src = Path(source_midi)
    raws = raw_lines(text)
    done = used_lines(src)
    if len(done) >= len(raws):
        return None
    notes, tempo, sig, program, total = expand.read_melody(src)
    bb = t.bar_beats(sig)
    old_beats = max(1, math.ceil(total / bb - 0.01)) * bb
    key = t.key_for_file(src) or t.estimate_key(notes)
    tail = [(p, s - (old_beats - 2 * bb), d) for p, s, d in notes if s >= old_beats - 2 * bb]
    folder = t.ensure_session()
    name = time.strftime("%H%M%S") + "-lyrexp"
    out, fails = [], 0
    log(f"  \u25b8 Writing lyric lines {len(done) + 1} onward (of {len(raws)}) in {t.key_label(key)}.")
    while len(out) < count_ and fails < t.MAX_FAILED_ATTEMPTS:
        try:
            new, meta, length = write_section(raws, len(done), add_bars, sig, key, llm, level, mood, tail or None, log)
        except (RuntimeError, KeyError, TypeError) as e:
            fails += 1
            log(f"      (attempt unusable, retrying: {str(e).splitlines()[0][:70]})")
            continue
        old = list(notes)
        p, s_, dd = old[-1]
        if s_ + dd > old_beats:                       # let the last old note end where the new lines begin
            old[-1] = (p, s_, max(old_beats - s_, 0.25))
        k = len(out) + 1
        path = folder / f"{name}-opt{k}.mid"
        t.write_melody_midi(path, tempo, old + [(p, s + old_beats, d) for p, s, d in new], program, sig, old_beats + length)
        sidecar(path).write_text(json.dumps({"lines": done + [dict(m, start_beat=m["start_beat"] + old_beats) for m in meta]}))
        info = {"index": k, "path": path, "tempo": tempo, "sig": sig, "score": None}
        out.append(info)
        log(f"      option {k}: lyric lines {len(done) + 1}-{len(done) + len(meta)}")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError("No usable melody was written for the next lyric lines; try again")
    return out


def backing(full_midi):
    """A copy of a piece without its Voice melody (the real voice replaces it), for playing together with the sung .wav.
    Returns None if nothing else is in the piece."""
    import mido

    src = Path(full_midi)
    mid = mido.MidiFile(str(src))
    voice = set()
    for tr in mid.tracks:
        for m in tr:
            if m.type == "program_change" and m.program == VOICE_PROGRAM:
                voice.add(m.channel)
    out, others = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat), False
    for tr in mid.tracks:
        keep, carry = mido.MidiTrack(), 0
        for m in tr:
            if m.type in ("note_on", "note_off") and m.channel in voice:
                carry += m.time
                continue
            if m.type in ("note_on", "note_off"):
                others = others or m.type == "note_on"
            keep.append(m.copy(time=m.time + carry))
            carry = 0
        out.tracks.append(keep)
    if not others:
        return None
    dest = src.with_name(src.stem + "-backing.mid")
    out.save(str(dest))
    return dest


def sing_midi(source_midi, log=print):
    """Sing the lyric lines a melody was written to. Needs one note per syllable, as written (an edit may have broken that)."""
    import expand

    src = Path(source_midi)
    lines = used_lines(src)
    if not lines:
        raise RuntimeError("This melody was not written to lyrics (choose the Voice instrument when generating).")
    notes, tempo, sig, _program, _total = expand.read_melody(src)
    bb = t.bar_beats(sig)
    parsed, melodies, starts = [], [], []
    for i, ln in enumerate(lines, 1):
        words = parse_line(ln["text"])
        s0, bars = ln["start_beat"], ln["bars"]
        mine = [(p, s - s0, d) for p, s, d in notes if s0 - 1e-6 <= s < s0 + bars * bb - 1e-6]
        if len(mine) != count(words):
            raise RuntimeError(f"Line {i} has {len(mine)} notes for {count(words)} syllables (was the melody edited?), so it can't be sung.")
        parsed.append(words)
        melodies.append((mine, bars))
        starts.append(s0)
    log("Singing it (SoulX-Singer)...")
    wav = sing(metadata(parsed, melodies, tempo, sig, starts), src.with_suffix(".wav"), log)
    log(f"      sung: {wav}")
    return wav


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lyrics", help="the words; one line of text per musical phrase (use \\n)")
    ap.add_argument("--bars", type=int, default=4, help="how long the motif is; as many lines as fit are used")
    ap.add_argument("--key", default="C major")
    ap.add_argument("--bpm", type=int, default=90)
    ap.add_argument("--sig", default="4/4")
    ap.add_argument("--llm", default="7B")
    ap.add_argument("--mood", default="")
    ap.add_argument("--sing", action="store_true", help="also sing the first option")
    ap.add_argument("--session", default=None)
    a = ap.parse_args()
    if a.session:
        t.new_session(a.session)
    k = t.parse_key(a.key) or (0, "major")
    r = lyric_motifs(a.lyrics.replace("\\n", "\n"), a.bars, 0, a.sig, a.bpm, k, a.llm, 3, a.mood, 1)
    print(r[0]["path"])
    if a.sing:
        print(sing_midi(r[0]["path"]))
