"""Lyrics to a sung melody (demo). The words are split into syllables (CMU pronouncing dictionary), the language model writes
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


def parse(text):
    """Lines of words: [[{"word", "phones", "stress"}...] ...]. Raises ValueError listing words the dictionary lacks."""
    lines, unknown = [], []
    for raw in text.splitlines():
        words = []
        for w in re.findall(r"[A-Za-z][A-Za-z']*", raw):
            ph = pronounce(w)
            if ph is None:
                unknown.append(w)
                continue
            words.append({"word": w, "phones": ph, "stress": syllable_stresses(ph)})
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


def token(word, i, k):
    """SoulX-Singer's phoneme token for one note of a word: the word's phones, and note type 2 (first note) or 3 (more)."""
    return "en_" + "-".join(word["phones"]), 2 if i == 0 else 3


def metadata(lines, melodies, tempo, sig):
    """Per line, the SoulX-Singer score-control segment: <SP> rests, one token per note, durations in seconds."""
    bb, spb = t.bar_beats(sig), 60.0 / tempo
    segs, line_start = [], 0.0
    for line, (notes, bars) in zip(lines, melodies):
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
    job.unlink(missing_ok=True)
    return Path(out_wav)


def make_song(text, key=(0, "major"), bpm=90, sig="4/4", llm="7B", level=3, mood="", name=None, log=print, sing_it=True,
              on_melody=None):
    """Lyrics in, a melody file (and a sung .wav) out, in the current session. Returns {"midi", "wav", "tempo", "notes"}."""
    lines = parse(text)
    folder = t.ensure_session(text.splitlines()[0][:40])
    stem = name or time.strftime("%H%M%S") + "-lyrics"
    log(f"Writing a melody for {len(lines)} line(s), {sum(count(l) for l in lines)} syllables...")
    melodies, before, start = [], None, 0.0
    allnotes = []
    bb = t.bar_beats(sig)
    for i, line in enumerate(lines, 1):
        notes, bars = line_melody(line, sig, key, llm, level, mood, before, log)
        melodies.append((notes, bars))
        allnotes += [(p, s + start, d) for p, s, d in notes]
        start += bars * bb
        before = notes
        log(f"      line {i}: {count(line)} syllables, {bars} bar(s)")
    midi = folder / f"{stem}.mid"
    t.write_melody_midi(midi, bpm, allnotes, 0, sig, start)
    res = {"midi": midi, "wav": None, "tempo": bpm, "notes": allnotes, "lines": lines}
    if on_melody:
        on_melody(res)
    if sing_it:
        log("Singing it (SoulX-Singer)...")
        res["wav"] = sing(metadata(lines, melodies, bpm, sig), folder / f"{stem}.wav", log)
        log(f"      sung: {res['wav']}")
    return res


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lyrics", help="the words; one line of text per musical phrase (use \\n)")
    ap.add_argument("--key", default="C major")
    ap.add_argument("--bpm", type=int, default=90)
    ap.add_argument("--sig", default="4/4")
    ap.add_argument("--llm", default="7B")
    ap.add_argument("--mood", default="")
    ap.add_argument("--no-sing", action="store_true", help="only write the melody")
    ap.add_argument("--session", default=None)
    a = ap.parse_args()
    if a.session:
        t.new_session(a.session)
    k = t.parse_key(a.key) or (0, "major")
    r = make_song(a.lyrics.replace("\\n", "\n"), k, a.bpm, a.sig, a.llm, mood=a.mood, sing_it=not a.no_sing)
    print(r["midi"], r["wav"] or "")
