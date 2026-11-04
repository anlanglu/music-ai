"""Lyrics to a sung melody (demo). The lyrics are kept apart from the melody: the melody is written to as many lines as
fit the bars asked for (the motif or an extension), and the next extension carries on with the next line. The words are split into syllables (CMU pronouncing dictionary), the language model writes
one note per syllable for each line, and SoulX-Singer (soulx/, run in .venv-sing) sings it: a zero-shot singing voice whose
timbre is cloned from a short reference recording (soulx/prompt/).

    .venv/bin/python lyrics.py "Twinkle twinkle little star" --key "C major" --bpm 90 --llm cc-sonnet-5-5"""
import json
import os
import math
import re
import shutil
import subprocess
import time
from pathlib import Path

import plan
import text2music as t
from llmjson import extract_json

ROOT = Path(__file__).resolve().parent
SING_PY = ROOT / ".venv-sing" / "bin" / "python"
SOULX = ROOT / "soulx"
_T7_MODELS = Path("/Volumes/T7/AIModels/music-ai-models")
MODELS = Path(os.environ.get("MUSIC_AI_MODELS") or (_T7_MODELS if _T7_MODELS.is_dir() else ROOT / "models"))   # weights: T7 drive if plugged in
MODEL = MODELS / "soulx" / "model.pt"
VOICES = {"en": "Singer A (English reference)", "zh": "Singer B (Mandarin reference)"}   # SoulX's two example recordings
VOICE_FILES = {"en": SOULX / "prompt" / "en_prompt", "zh": SOULX / "prompt" / "zh_prompt"}   # each: <name>.wav + .json
VOICE_PROGRAM = 53                              # the "Voice" instrument: GM Voice Oohs in the MIDI file; such melodies are written to lyrics
SYL_PER_BAR = 4                                 # about this many syllables to a bar when the model picks the rhythm
RANGE = (57, 74)                                # a comfortable singing range (the voice model also shifts to its singer)

_cmu = None


def ready():
    """(ok, what_is_missing)"""
    if not SING_PY.exists():
        return False, "The singing environment (.venv-sing) is not installed."
    if not MODEL.exists():
        return False, f"The singing model ({MODEL}) is not downloaded (or the drive holding it is not plugged in)."
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


CJK = r"[\u4e00-\u9fff]"
_phoneset = None


def is_zh(raw):
    """Whether a lyric line is Chinese (Mandarin)."""
    return re.search(CJK, raw) is not None


def raw_lines(text):
    """The lyric lines that have words in them."""
    return [l.strip() for l in text.splitlines() if re.search(r"[A-Za-z]|" + CJK, l)]


def zh_token(pinyin):
    """SoulX-Singer's Mandarin token for one syllable (tone digit last, the umlaut written u:), or None if it has none."""
    global _phoneset
    if _phoneset is None:
        _phoneset = set(json.loads((SOULX / "soulxsinger" / "utils" / "phoneme" / "phone_set.json").read_text()))
    tok = "zh_" + pinyin.replace("lve", "lu:e").replace("nve", "nu:e").replace("v", "u:")
    return tok if tok in _phoneset else None


def zh_syllables(raw):
    """[(character, SoulX token or None)] for the Chinese characters of a line (punctuation and spaces are not sung)."""
    from pypinyin import Style, lazy_pinyin

    chars = "".join(re.findall(CJK, raw))
    pys = lazy_pinyin(chars, style=Style.TONE3, neutral_tone_with_five=True, v_to_u=False)
    return [(c, zh_token(p)) for c, p in zip(chars, pys)]


def unknown_words(raw):
    if is_zh(raw):                  # Latin words in a Chinese line, and characters SoulX has no syllable for
        return re.findall(r"[A-Za-z][A-Za-z']*", raw) + [c for c, tok in zh_syllables(raw) if tok is None]
    return [w for w in re.findall(r"[A-Za-z][A-Za-z']*", raw) if pronounce(w) is None]


def parse_line(raw):
    """One line as words: [{"word", "phones", "stress"}...] (for Chinese, one "word" per character, with its "token").
    Raises ValueError listing words the dictionary lacks."""
    unknown = unknown_words(raw)
    if unknown:
        raise ValueError("I don't know how to pronounce: " + ", ".join(sorted(set(unknown))) + ". Respell these words (for example phonetically).")
    if is_zh(raw):
        return [{"word": c, "phones": [tok], "stress": [0 if tok[-1] == "5" else 1], "token": tok} for c, tok in zh_syllables(raw)]
    return [{"word": w, "phones": pronounce(w), "stress": syllable_stresses(pronounce(w))}
            for w in re.findall(r"[A-Za-z][A-Za-z']*", raw)]


def parse(text):
    """Lines of words: [[{"word", "phones", "stress"}...] ...]. Raises ValueError listing words the dictionary lacks."""
    raws = raw_lines(text)
    unknown = sorted({w for raw in raws for w in unknown_words(raw)})
    if unknown:
        raise ValueError("I don't know how to pronounce: " + ", ".join(unknown) + ". Respell these words (for example phonetically).")
    if not raws:
        raise ValueError("No lyrics found.")
    return [parse_line(raw) for raw in raws]


def count(line):
    return sum(len(w["stress"]) for w in line)


SYSTEM = """You write a singable melody for one line of lyrics. Reply with ONLY a JSON object:
{{"plan": "<one sentence: contour and where the stressed words fall>", "notes": [[midi_pitch, start_beat, duration_beats], ...]}}
Rules: exactly {n} notes, one for each syllable, in order; time signature {sig} (one bar = {bb:g} quarter-note beats), the line
fills {bars} bar(s) and start_beat counts from 0 at the start of the line; pitches {lo}-{hi}; start_beat and duration_beats are
multiples of 0.25; notes never overlap; leave a rest only where the line breathes. Stressed syllables go on strong beats
and get longer notes; unstressed syllables are shorter. Sing in {key}, using only these notes: {scale}. Mostly stepwise with
a few leaps, a clear shape, and the last note of the line long and settled."""


def _line_prompt(line, bars, sig, key, mood, before, chords=None, piano=None):
    words = " ".join(f"{w['word']}({'/'.join('STRONG' if s == 1 else 'weak' for s in w['stress'])})" for w in line)
    msg = f"Lyric line ({count(line)} syllables): {words}"
    if line[0].get("token"):
        msg += "\n(Chinese: each character is one syllable and gets one note.)"
    if mood:
        msg += f"\nMood and style: {mood}"
    if chords:
        msg += "\nUnder this line the piano plays one chord per bar (notes of each): " + "; ".join(
            f"bar {i}: {' '.join(t.PC_NAMES[pc] for pc in sorted(c))}" for i, c in enumerate(chords, 1)) + \
            ". Put chord notes on the stressed syllables and the long notes."
    if piano:
        msg += f"\nThe piano melody in these bars (same format): {json.dumps([list(n) for n in piano])}. Fit with it; do not copy it."
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


def line_melody(line, sig, key, llm, level, mood, before, log, chords=None, piano=None):
    n, bb = count(line), t.bar_beats(sig)
    bars = max(1, math.ceil(n / SYL_PER_BAR))
    model, tok = t._get_llm(llm)
    msgs = [{"role": "system", "content": SYSTEM.format(n=n, sig=sig, bb=bb, bars=bars, lo=RANGE[0], hi=RANGE[1],
                                                         key=t.key_label(key), scale=t.scale_names(key))},
            {"role": "user", "content": _line_prompt(line, bars, sig, key, mood, before, chords and chords[:bars], piano)}]
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
    if chords:
        best = _toward_chords(best, chords, bb, key)
    return best, bars


def _toward_chords(notes, chords, bb, key):
    """Stressed places (the first note of a bar, any note of two beats or more) move up to two semitones to the nearest
    note of that bar's chord, if it is in the key. `chords` is one set of pitch classes per bar."""
    scale = t.scale_pcs(key)
    out = []
    for p, s, d in notes:
        pcs = chords[min(int(s // bb), len(chords) - 1)]
        if (s % bb < 1e-6 or d >= 2) and p % 12 not in pcs:
            near = sorted((c for c in range(p - 2, p + 3) if c % 12 in pcs and c % 12 in scale), key=lambda c: (abs(c - p), c))
            if near:
                p = near[0]
        out.append((p, s, d))
    return out


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
    if word.get("token"):                           # Chinese: one character, one syllable
        return word["token"], 2
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
        segs.append({"index": f"line_{len(segs)}", "language": "Mandarin" if line[0].get("token") else "English",
                     "time": [round(line_start * 1000), round((line_start + bars * bb * spb) * 1000)],
                     "duration": " ".join(f"{x:.3f}" for x in durs), "text": " ".join(text), "phoneme": " ".join(toks),
                     "note_pitch": " ".join(map(str, pitch)), "note_type": " ".join(map(str, types))})
        line_start += bars * bb * spb
    return segs


PREP_PY = ROOT / ".venv-prep" / "bin" / "python"
PREP_MODELS = MODELS / "soulx-preprocess"
USER_VOICES = Path(os.environ.get("MUSIC_AI_VOICES") or ROOT / "voices")                  # one folder per voice you added: voice.wav, voice.json, voice.txt (name, language)
PREP_NEEDS = {"English": "parakeet-tdt-0.6b-v2/parakeet-tdt-0.6b-v2.nemo",
              "Mandarin": "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/model.pt"}


def voice_base(key):
    """The path (without .wav/.json) of a voice's reference recording, or None."""
    if key in VOICE_FILES:
        return VOICE_FILES[key]
    base = USER_VOICES / key.removeprefix("u-") / "voice"
    return base if key.startswith("u-") and base.with_suffix(".wav").exists() and base.with_suffix(".json").exists() else None


def voice_list():
    """[(key, name)]: the two voices that come with SoulX, then the ones you added."""
    out = list(VOICES.items())
    if USER_VOICES.exists():
        for d in sorted(USER_VOICES.iterdir()):
            if (d / "voice.wav").exists() and (d / "voice.json").exists():
                try:
                    name = json.loads((d / "voice.txt").read_text())["name"]
                except (OSError, ValueError, KeyError):
                    name = d.name
                out.append(("u-" + d.name, name))
    return out


def prep_ready(language):
    """(ok, what_is_missing) for making a voice from a recording."""
    if not PREP_PY.exists():
        return False, "The voice-making environment (.venv-prep) is not installed (see DEPLOY.md 4c)."
    for need in ["rmvpe/rmvpe.pt", "rosvot/rosvot/model.pt", "rosvot/rwbd/model.pt", PREP_NEEDS.get(language, "?")]:
        if not (PREP_MODELS / need).exists():
            return False, f"A voice-making model is not downloaded ({PREP_MODELS / need}) or its drive is not plugged in; see DEPLOY.md 4c."
    return True, ""


def add_voice(audio, name, language="English", log=print):
    """Make a new singer from a recording of a voice singing alone (10 to 30 seconds is best). Returns its key."""
    ok, why = prep_ready(language)
    if not ok:
        raise RuntimeError(why)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "voice"
    folder = USER_VOICES / slug
    if folder.exists():
        raise RuntimeError(f"There is already a voice called {name!r}; choose another name.")
    work = folder / "work"
    work.mkdir(parents=True)
    try:
        src = work / "source.wav"       # the pipeline reads wav; afconvert opens mp3, m4a, aiff, flac and wav
        c = subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", "-c", "1", str(audio), str(src)], capture_output=True, text=True)
        if c.returncode != 0:
            raise RuntimeError("I couldn't open that audio file: " + (c.stderr.strip().splitlines() or ["unknown format"])[-1])
        job = work / "job.json"
        job.write_text(json.dumps({"audio": str(src), "save_dir": str(work / "out"), "language": language, "models": str(PREP_MODELS)}))
        log("Working out the words and notes of the recording (this takes a minute)...")
        p = subprocess.run([str(PREP_PY), "-I", str(SOULX / "prep.py"), str(job)], capture_output=True, text=True, cwd=str(SOULX))
        meta = work / "out" / "metadata.json"
        if p.returncode != 0 or not meta.exists():
            raise RuntimeError("Working out the recording failed: " + " | ".join((p.stdout + p.stderr).strip().splitlines()[-3:]))
        items = json.loads(meta.read_text())
        notes = [int(x) for x in items[0]["note_pitch"].split() if int(x) > 0] if items else []
        if len(notes) < 4:
            raise RuntimeError(f"I found only {len(notes)} sung note(s) in that recording. It needs about 10 to 30 seconds of a voice singing alone, with words (speaking or humming does not work).")
        wav = work / "out" / "long_cut_wavs" / (items[0]["index"] + ".wav")
        shutil.copyfile(wav, folder / "voice.wav")
        (folder / "voice.json").write_text(json.dumps(items[:1], ensure_ascii=False))
        (folder / "voice.txt").write_text(json.dumps({"name": name, "language": language}))
        log(f"      voice {name!r}: {len(notes)} notes found, {items[0]['time'][1] / 1000 - items[0]['time'][0] / 1000:.0f} s used")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    shutil.rmtree(work, ignore_errors=True)
    return "u-" + slug


def sing(segments, out_wav, log=print, n_steps=32, voice="en"):
    ok, why = ready()
    if not ok:
        raise RuntimeError(why)
    job = Path(out_wav).with_suffix(".job.json")
    job.write_text(json.dumps({"segments": segments, "out": str(out_wav), "model": str(MODEL), "n_steps": n_steps,
                               "prompt_wav": str(voice_base(voice)) + ".wav", "prompt_meta": str(voice_base(voice)) + ".json"}))
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


def sidecar(midi):
    """The file beside a melody that says which lyric lines it is written to."""
    return Path(midi).with_suffix(".lyrics.json")


def used_lines(midi, track=None):
    """[{"text", "start_beat", "bars", ...}] of the lyric lines a melody file was written to (none if it has no lyrics).
    Lines of the sung part over the melody have "track": "vocal"; `track` "vocal" or "melody" picks one kind."""
    try:
        lines = json.loads(sidecar(midi).read_text())["lines"]
    except (OSError, ValueError, KeyError):
        return []
    if track is None:
        return lines
    return [ln for ln in lines if (ln.get("track") == "vocal") == (track == "vocal")]


def line_problem(notes, lines, bb):
    """Why a sung line can no longer be sung (a lyric line whose notes are not one per syllable), or None."""
    for i, ln in enumerate(lines, 1):
        n = sum(1 for _p, st, _d in notes if ln["start_beat"] - 1e-6 <= st < ln["start_beat"] + ln["bars"] * bb - 1e-6)
        want = count(parse_line(ln["text"]))
        if n != want:
            return (f"The sung line \u201c{ln['text']}\u201d has {want} syllables, so it needs {want} notes (it would have {n}). "
                    "Move, lengthen or retune notes, but keep one note per syllable.")
    return None


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


def write_section(raws, start, room_bars, sig, key, llm, level, mood, before, log, chords=None, piano=None):
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
        first_bar = int(cursor // bb)
        here = [(p, s - cursor, d) for p, s, d in (piano or []) if cursor - 1e-6 <= s < cursor + max(1, math.ceil(count(parsed) / SYL_PER_BAR)) * bb - 1e-6]
        ln, bars = line_melody(parsed, sig, key, llm, level, mood, before, log, chords and chords[first_bar:], here or None)
        notes += [(p, s + cursor, d) for p, s, d in ln]
        meta.append({"text": raw, "start_beat": cursor, "bars": bars})
        cursor += bars * bb
        before = ln
        log(f"      \u201c{raw}\u201d: {count(parsed)} syllables, {bars} bar(s)")
    return notes, meta, cursor


HARMONY_STEPS = [0, 2, -2, 4]       # scale steps from the lead: the lead, a third above, a third below, a fifth above
MAX_SINGERS = len(HARMONY_STEPS)


def singers_of(voice, voices=None):
    """The lead first, then the others, each once, at most MAX_SINGERS."""
    out = []
    for v in [voice] + list(voices or []):
        if v and v not in out:
            out.append(v)
    return out[:MAX_SINGERS]


def harmonize(notes, key, steps):
    """The notes moved `steps` steps along the key's scale (a third is 2 steps): a harmony part that stays in the key.
    A note outside the scale is first taken to the nearest scale note."""
    pcs = t.scale_pcs(key)
    scale = [p for p in range(128) if p % 12 in pcs]
    out = []
    for p, s, d in notes:
        i = min(range(len(scale)), key=lambda k: abs(scale[k] - p))
        out.append((scale[max(0, min(len(scale) - 1, i + steps))], s, d))
    return out


def line_singers(ln, fallback="en"):
    """([singers], harmonize?) stored with a lyric line. Lines from before choirs have just "voice", or nothing."""
    return singers_of(ln.get("voice") or fallback, ln.get("voices")), bool(ln.get("harmony"))


def lyric_motifs(text, bars, program, sig, bpm, key, llm, level, mood, count_, log=print, on_candidate=None, voice="en", voices=None, harmony=False):
    """Step 1 written to the lyrics: `count_` options, each the first lines that fit in `bars` bars, sung by `voice` and any
    `voices` with it: the same notes, or (`harmony`) a part each."""
    singers = singers_of(voice, voices)
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
        sidecar(path).write_text(json.dumps({"lines": [dict(m, voice=singers[0], voices=singers, harmony=harmony) for m in meta]}))
        info = {"index": k, "path": path, "tempo": bpm, "sig": sig, "score": None}
        out.append(info)
        log(f"      option {k}: {len(notes)} notes to {len(meta)} lyric line(s) of {len(raws)}")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError("No usable melody was written for the lyrics; try again")
    return out


def lyric_extend(source_midi, text, add_bars, llm, level, mood, count_, log=print, on_candidate=None, voice="en", voices=None, harmony=False):
    """Step 2 written to the lyrics: the next lines that fit in `add_bars` bars are added after the melody, as a part for
    the Voice instrument sung by `voice` and any `voices` with it (the same notes, or `harmony`); the melody may have been
    another instrument until now. Returns the options, or None when the melody already used all the lyrics."""
    singers = singers_of(voice, voices)
    import expand

    src = Path(source_midi)
    raws = raw_lines(text)
    everything = used_lines(src)
    done = used_lines(src, "melody")
    if len(done) >= len(raws):
        return None
    vocal = expand.read_vocal(src)                  # a sung part over the melody stays as it is
    notes, tempo, sig, _first, total = expand.read_melody(src)
    programs = expand.read_programs(src)
    programs += [] if programs[-1][1] == VOICE_PROGRAM else [(None, VOICE_PROGRAM)]      # the new part is the voice
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
        t.write_melody_midi(path, tempo, old + [(p, s + old_beats, d) for p, s, d in new],
                            [(old_beats if b is None else b, pr) for b, pr in programs], sig, old_beats + length, vocal=vocal, acc=t.read_acc(src))
        sidecar(path).write_text(json.dumps({"lines": everything + [dict(m, start_beat=m["start_beat"] + old_beats, voice=singers[0], voices=singers, harmony=harmony) for m in meta]}))
        info = {"index": k, "path": path, "tempo": tempo, "sig": sig, "score": None}
        out.append(info)
        log(f"      option {k}: lyric lines {len(done) + 1}-{len(done) + len(meta)}")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError("No usable melody was written for the next lyric lines; try again")
    return out


def sing_part(source_midi, text, start_bar=None, bars=4, llm="7B", level=3, mood="", count_=3, log=print, on_candidate=None,
              voice="en", voices=None, harmony=False):
    """Add a sung line over a melody (step 4): the next lyric lines that fit in `bars` bars, starting at bar `start_bar`
    (default: after the last sung bar), written to the melody's key and chords and sung by `voice` and any `voices` with it.
    The melody and the bars already sung are kept exactly. Returns the options."""

    import expand

    src = Path(source_midi)
    raws = raw_lines(text)
    if not raws:
        raise ValueError("No lyrics found.")
    notes, tempo, sig, _first, total = expand.read_melody(src)
    programs = expand.read_programs(src)
    vocal = expand.read_vocal(src)
    everything = used_lines(src)
    sung = used_lines(src, "vocal")
    bb = t.bar_beats(sig)
    n_bars = max(1, math.ceil(total / bb - 0.01))
    after = max([ln["start_beat"] + ln["bars"] * bb for ln in sung], default=0.0)
    first = int(start_bar) if start_bar else int(round(after / bb)) + 1
    if first < 1 or first > n_bars:
        raise RuntimeError(f"The melody has {n_bars} bars, so there is no bar {first} to sing. Extend the melody first (step 2).")
    room = min(int(bars), n_bars - first + 1)
    lo, hi = (first - 1) * bb, (first - 1 + room) * bb
    if any(ln["start_beat"] < hi - 1e-6 and ln["start_beat"] + ln["bars"] * bb > lo + 1e-6 for ln in sung):
        raise RuntimeError(f"Bars {first}-{first + room - 1} already have singing. Choose bars after it, or change it in step 3.")
    if len(sung) >= len(raws):
        raise RuntimeError("The sung part has already used all the lyrics. Add more lines in the Lyrics box.")
    singers = singers_of(voice, voices)
    key = t.key_for_file(src) or t.estimate_key(notes)
    theplan = t.plan_for_file(src)
    chords = [set(c) for c in plan.plan_chord_pcs(theplan)] if theplan and len(theplan["chords"]) >= n_bars else \
        [set(plan.chord_pcs(key[0], key[1], d)) for d in plan.infer_chords(notes, key, sig, n_bars)]
    chords = chords[first - 1:first - 1 + room]
    piano = [(p, s - lo, d) for p, s, d in notes if lo - 1e-6 <= s < hi - 1e-6]
    tail = [(p, s - (sung[-1]["start_beat"] if sung else 0), d) for p, s, d in vocal if sung and s >= sung[-1]["start_beat"]] or None
    folder = t.ensure_session()
    name = time.strftime("%H%M%S") + "-sing"
    out, fails = [], 0
    log(f"  \u25b8 Writing a sung line for bars {first}-{first + room - 1} (lyric lines {len(sung) + 1} onward of {len(raws)}), "
        f"over the melody, in {t.key_label(key)}.")
    while len(out) < count_ and fails < t.MAX_FAILED_ATTEMPTS:
        try:
            new, meta, length = write_section(raws, len(sung), room, sig, key, llm, level, mood, tail, log, chords=chords, piano=piano)
        except (RuntimeError, KeyError, TypeError) as e:
            fails += 1
            log(f"      (attempt unusable, retrying: {str(e).splitlines()[0][:70]})")
            continue
        k = len(out) + 1
        path = folder / f"{name}-opt{k}.mid"
        t.write_melody_midi(path, tempo, notes, programs, sig, n_bars * bb,
                            vocal=vocal + [(p, s + lo, d) for p, s, d in new], acc=t.read_acc(src))
        sidecar(path).write_text(json.dumps({"lines": everything + [
            dict(m, start_beat=m["start_beat"] + lo, voice=singers[0], voices=singers, harmony=harmony, track="vocal") for m in meta]}))
        info = {"index": k, "path": path, "tempo": tempo, "sig": sig, "score": None}
        out.append(info)
        log(f"      option {k}: {len(new)} sung notes to {len(meta)} lyric line(s)")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError("No usable sung line was written; try again")
    return out


def backing(full_midi):
    """A copy of a piece without the stretches played by the Voice instrument (the real voices replace them), for playing
    together with the sung .wav. Stretches of the melody for other instruments stay. Returns None if nothing else is in
    the piece."""
    import mido

    src = Path(full_midi)
    mid = mido.MidiFile(str(src))
    out, others = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat), False
    for tr in mid.tracks:
        keep, carry, now, held = mido.MidiTrack(), 0, {}, set()
        for m in tr:
            if m.type == "program_change":
                now[m.channel] = m.program
            if m.type in ("note_on", "note_off"):
                key, starting = (m.channel, m.note), m.type == "note_on" and m.velocity > 0
                if starting:
                    gone = now.get(m.channel) == VOICE_PROGRAM
                    if gone:
                        held.add(key)
                else:
                    gone = key in held
                    held.discard(key)
                if gone:
                    carry += m.time
                    continue
                others = others or starting
            keep.append(m.copy(time=m.time + carry))
            carry = 0
        out.tracks.append(keep)
    if not others:
        return None
    dest = src.with_name(src.stem + "-backing.mid")
    out.save(str(dest))
    return dest


def _mix_wavs(parts, dest):
    """Add the sung parts (one 16-bit mono wav per singer, each as long as its own last line) into one file."""
    import wave

    import numpy as np

    waves, rate = [], None
    for p in parts:
        with wave.open(str(p)) as f:
            rate = f.getframerate()
            w = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768
            waves.append(w.reshape(-1, f.getnchannels()).mean(axis=1))
    mix = np.zeros(max(len(w) for w in waves), dtype=np.float32)
    for w in waves:
        mix[: len(w)] += w
    peak = float(np.abs(mix).max())
    if peak > 0.99:
        mix *= 0.99 / peak
    with wave.open(str(dest), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
        f.writeframes((mix * 32767).astype(np.int16).tobytes())


def sing_midi(source_midi, log=print, voice="en"):
    """Sing the lyric lines a melody was written to, each line by the singers it was written for (`voice` is only for lines
    from before singers were stored). With several singers they sing the same notes, or each a harmony part (a third above,
    a third below...). Needs one note per syllable, as written (an edit may have broken that)."""
    import expand

    src = Path(source_midi)
    lines = used_lines(src)
    if not lines:
        raise RuntimeError("This melody was not written to lyrics (choose the Voice instrument when generating).")
    notes, tempo, sig, _program, _total = expand.read_melody(src)
    vocal = expand.read_vocal(src)
    key = t.key_for_file(src) or t.estimate_key(notes)
    bb = t.bar_beats(sig)
    groups = {}                                   # singer -> (parsed lines, melodies, starts)
    for i, ln in enumerate(lines, 1):
        who_all, harmony = line_singers(ln, voice)
        words = parse_line(ln["text"])
        s0, bars = ln["start_beat"], ln["bars"]
        source = vocal if ln.get("track") == "vocal" else notes
        mine = [(p, s - s0, d) for p, s, d in source if s0 - 1e-6 <= s < s0 + bars * bb - 1e-6]
        if len(mine) != count(words):
            raise RuntimeError(f"Line {i} has {len(mine)} notes for {count(words)} syllables (was the melody edited?), so it can't be sung.")
        for k, who in enumerate(who_all):
            if voice_base(who) is None:
                raise RuntimeError(f"Line {i} is for the voice {who!r}, which is not there any more.")
            part = harmonize(mine, key, HARMONY_STEPS[k]) if harmony and k else mine
            g = groups.setdefault(who, ([], [], []))
            g[0].append(words); g[1].append((part, bars)); g[2].append(s0)
    log("Singing it (SoulX-Singer)" + (f", {len(groups)} singers" if len(groups) > 1 else "") + "...")
    if len(groups) == 1:
        who = next(iter(groups))
        parsed, melodies, starts = groups[who]
        wav = sing(metadata(parsed, melodies, tempo, sig, starts), src.with_suffix(f".{who}.wav" if who != "en" else ".wav"), log, voice=who)
    else:
        parts = []
        for who, (parsed, melodies, starts) in groups.items():
            log(f"  \u25b8 {len(parsed)} line(s) in {dict(voice_list()).get(who, who)}")
            parts.append(sing(metadata(parsed, melodies, tempo, sig, starts), src.with_suffix(f".{who}.part.wav"), log, voice=who))
        wav = src.with_suffix(".voices.wav")
        _mix_wavs(parts, wav)
        for p in parts:
            p.unlink(missing_ok=True)
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
