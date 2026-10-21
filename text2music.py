"""Text prompt -> local LLM melody (MLX) -> MIDI -> Anticipatory Music Transformer accompaniment.

CLI:
    .venv/bin/python text2music.py "a sad, slow melody in D minor" --bars 8
GUI:
    open Composer.app   (Swift app; talks to backend.py)
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

import mido
import numpy as np
import torch

import plan
import variety
from llmjson import extract_json
from playable import DRUMS, fold_pitch, limits, sampler_pool
from plan import SCALE_MAJOR as _SCALE_MAJOR, SCALE_MINOR as _SCALE_MINOR

from anticipation.vocab import NOTE_OFFSET

OUT_DIR = Path(os.environ.get("MUSIC_AI_OUT") or Path(__file__).parent / "output")   # holds one folder per session
TMP_DIR = OUT_DIR / ".tmp"                    # scratch files (seed melodies, notation intermediates)

# How much of the piece's plan the melody follows (see plan.py): the harmony of every bar, the motif restated at
# each phrase, and a cadence at the end of every phrase. The "Form" slider in the app sets this.
DEFAULT_FORM = 2               # 0 free, 1 harmony, 2 cadences, 3 the whole plan
PLAN_TARGET_TOL = 1.0          # how far (in beats) a note may land from a target and still be pulled
PLAN_TARGET_SIGMA = 1.0        # how wide the pull is, in semitones (measured: a narrow pull brings the idea back,
                               # a wide one lets the model's own preferences win)

LLMS = {
    "7B": "mlx-community/Qwen2.5-7B-Instruct-4bit",
    "14B": "mlx-community/Qwen2.5-14B-Instruct-4bit",  # ~8 GB download, richer melodies, slower
    "3B": "mlx-community/Qwen2.5-3B-Instruct-4bit",    # ~1.8 GB: plenty for reading directions, light on memory
}
ANT = "stanford-crfm/music-{size}-800k"

SYSTEM = """You are a composer. Reply with ONLY a JSON object, no prose, in this form:
{"plan": "<one or two sentences: the motif, how it develops, where the climax is>",
 "key": "<tonic> <major|minor>, the key the notes are actually in (e.g. D minor)", "tempo": <bpm int>,
 "notes": [[midi_pitch, start_beat, duration_beats], ...]}
Rules: a single monophonic melody in {sig} time (one bar = {bar_beats:g} quarter-note beats), pitches
{lo}-{hi}, start_beat and duration_beats in quarter-note beats (multiples of 0.25), notes sorted by
start_beat and never overlapping; gaps between notes are rests. The first note starts at beat 0.
Cover exactly the requested number of bars and end on a stable note.
Style: {style}"""

COMPLEXITY = {
    1: ("Simple and singable: mostly quarter and half notes, stepwise motion.", 0.6, (57, 79)),
    2: ("Mostly stepwise with some small leaps; mix quarter and eighth notes; one repeated motif.", 0.9, (55, 81)),
    3: ("Varied rhythm (quarters, eighths, dotted rhythms, a few rests), occasional leaps up to a sixth. "
        "State a motif, repeat it varied, and build to a high point.", 1.3, (53, 84)),
    4: ("Rich and expressive: eighth and sixteenth notes, syncopation, dotted rhythms, rests, leaps up to an "
        "octave. Develop the motif by sequence, inversion and rhythmic variation across clear phrases (like "
        "A A' B A''), with a distinct climax and a range of about an octave and a half.", 1.8, (50, 88)),
    5: ("Virtuosic: dense sixteenth-note runs, arpeggios, chromatic passing tones, syncopation, rests and "
        "ornaments; at least two octaves of range; several contrasting phrases with a dramatic climax. "
        "Every bar should have different rhythm from its neighbour.", 2.4, (48, 91)),
}
TEMPERATURE = {1: 0.6, 2: 0.7, 3: 0.8, 4: 0.9, 5: 1.0}

# Accompaniment instruments in priority order (GM programs; 128 = drums). Asking for N instruments
# uses the first N of these, skipping whichever one is playing the melody.
ENSEMBLE = [32, 48, 0, 128, 42, 40, 73, 71, 60, 56, 46, 24, 41, 68, 12, 52]

TIME_SIGS = ["4/4", "3/4", "2/4", "6/8", "5/4", "7/8", "12/8"]


def bar_beats(sig):
    """Length of one bar in quarter-note beats."""
    num, den = (int(x) for x in sig.split("/"))
    return num * 4 / den

GM_NAMES = """Acoustic Grand Piano|Bright Acoustic Piano|Electric Grand Piano|Honky-tonk Piano|Electric Piano 1|Electric Piano 2|Harpsichord|Clavinet|Celesta|Glockenspiel|Music Box|Vibraphone|Marimba|Xylophone|Tubular Bells|Dulcimer|Drawbar Organ|Percussive Organ|Rock Organ|Church Organ|Reed Organ|Accordion|Harmonica|Tango Accordion|Nylon Guitar|Steel Guitar|Jazz Guitar|Clean Guitar|Muted Guitar|Overdriven Guitar|Distortion Guitar|Guitar Harmonics|Acoustic Bass|Finger Bass|Pick Bass|Fretless Bass|Slap Bass 1|Slap Bass 2|Synth Bass 1|Synth Bass 2|Violin|Viola|Cello|Contrabass|Tremolo Strings|Pizzicato Strings|Orchestral Harp|Timpani|String Ensemble 1|String Ensemble 2|Synth Strings 1|Synth Strings 2|Choir Aahs|Voice Oohs|Synth Voice|Orchestra Hit|Trumpet|Trombone|Tuba|Muted Trumpet|French Horn|Brass Section|Synth Brass 1|Synth Brass 2|Soprano Sax|Alto Sax|Tenor Sax|Baritone Sax|Oboe|English Horn|Bassoon|Clarinet|Piccolo|Flute|Recorder|Pan Flute|Blown Bottle|Shakuhachi|Whistle|Ocarina|Square Lead|Saw Lead|Calliope Lead|Chiff Lead|Charang Lead|Voice Lead|Fifths Lead|Bass + Lead|New Age Pad|Warm Pad|Polysynth Pad|Choir Pad|Bowed Pad|Metallic Pad|Halo Pad|Sweep Pad|Rain FX|Soundtrack FX|Crystal FX|Atmosphere FX|Brightness FX|Goblins FX|Echoes FX|Sci-Fi FX|Sitar|Banjo|Shamisen|Koto|Kalimba|Bagpipe|Fiddle|Shanai|Tinkle Bell|Agogo|Steel Drums|Woodblock|Taiko Drum|Melodic Tom|Synth Drum|Reverse Cymbal|Guitar Fret Noise|Breath Noise|Seashore|Bird Tweet|Telephone Ring|Helicopter|Applause|Gunshot""".split("|")


def conductor_track(tempo, sig):
    """Tempo / time-signature only. Logic treats the first track of a MIDI file as the tempo track, so
    notes must not live there."""
    num, den = (int(x) for x in sig.split("/"))
    trk = mido.MidiTrack()
    trk.append(mido.MetaMessage("track_name", name="Tempo", time=0))
    trk.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo), time=0))
    trk.append(mido.MetaMessage("time_signature", numerator=num, denominator=den, time=0))
    return trk


def track_label(trk):
    """Name a track after the GM instrument its first program_change selects (channel 10 = drums)."""
    for m in trk:
        if m.type == "program_change":
            return "Drums" if m.channel == 9 else GM_NAMES[m.program]
        if m.type in ("note_on", "note_off") and m.channel == 9:
            return "Drums"
    return "Track"


# ---- sessions: one folder per project, holding every file it produces -------------------------------------

_session = None  # Path of the current session folder (created lazily on first use)


def slugify(text, limit=40):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-")
    return slug or "untitled"


def _write_meta(path, **fields):
    meta_path = path / "session.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.update(fields)
    meta_path.write_text(json.dumps(meta, indent=2))


def session_info(path):
    path = Path(path)
    try:
        meta = json.loads((path / "session.json").read_text())
    except (OSError, ValueError):
        meta = {}
    files = [f for f in path.iterdir() if f.is_file() and f.suffix in (".mid", ".pdf", ".musicxml")]
    return {"name": meta.get("name") or path.name, "path": str(path), "created": meta.get("created", ""),
            "files": len(files)}


def _unique_dir(stamp, name):
    path = OUT_DIR / f"{stamp}_{slugify(name)}"
    k = 2
    while path.exists():
        path = OUT_DIR / f"{stamp}_{slugify(name)}-{k}"
        k += 1
    return path


def drop_if_empty(path):
    """Remove a session folder nothing was ever written to (only its own metadata)."""
    try:
        if path and path.exists() and {f.name for f in path.iterdir()} <= {"session.json"}:
            for f in path.iterdir():
                f.unlink()
            path.rmdir()
    except OSError:
        pass


def new_session(name=None, prompt=""):
    """Start a fresh session folder: output/<YYYY-MM-DD_HHMM>_<name>/. Defaults the name to the prompt."""
    global _session
    drop_if_empty(_session)
    name = (name or prompt or "Untitled session").strip()
    name = name if len(name) <= 60 else name[:57].rstrip() + "..."
    OUT_DIR.mkdir(exist_ok=True)
    path = _unique_dir(time.strftime("%Y-%m-%d_%H%M"), name)
    path.mkdir()
    _write_meta(path, name=name, created=time.strftime("%Y-%m-%d %H:%M:%S"))
    _session = path
    return path


def seed_everything(seed):
    """Make a run repeatable: seed every random number generator the pipeline draws from (Python, NumPy, PyTorch for
    the music model, MLX for the language model)."""
    import random

    random.seed(seed)
    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)
    try:
        import mlx.core as mx

        mx.random.seed(seed)
    except ImportError:
        pass


def current_session():
    return _session


def reset_session():
    """Forget the current session; the next piece of work starts a new folder."""
    global _session
    drop_if_empty(_session)
    _session = None


def ensure_session(prompt=""):
    return _session if _session is not None else new_session(prompt=prompt)


def rename_session(name):
    """Rename the current session and move its folder to match. Returns (old_path, new_path)."""
    global _session
    if _session is None:
        raise RuntimeError("There is no session to rename yet")
    name = name.strip() or "Untitled session"
    old = _session
    stamp = old.name.split("_", 2)
    prefix = "_".join(stamp[:2]) if len(stamp) >= 2 and stamp[0][:2] == "20" else time.strftime("%Y-%m-%d_%H%M")
    new = _unique_dir(prefix, name) if slugify(name) != old.name.split("_", 2)[-1] else old
    if new != old:
        old.rename(new)
    _write_meta(new, name=name)
    _session = new
    return old, new


def list_sessions():
    if not OUT_DIR.exists():
        return []
    items = []
    for d in OUT_DIR.iterdir():
        if not (d.is_dir() and (d / "session.json").exists()):
            continue
        info = session_info(d)
        if info["files"] == 0 and d != _session:  # leftovers from a run that failed before writing anything
            drop_if_empty(d)
            continue
        items.append(info)
    return sorted(items, key=lambda i: i["path"], reverse=True)


def open_session(path):
    global _session
    path = Path(path)
    if OUT_DIR.resolve() not in path.resolve().parents:   # only folders inside the output directory are sessions
        raise ValueError(f"{path} is not inside the output folder")
    if not (path / "session.json").exists():
        raise FileNotFoundError(f"{path} is not a session folder")
    if _session != path:
        drop_if_empty(_session)
    _session = path
    return path


# Loading either model takes a while, so keep them around between runs (matters for the GUI).
_llm = {}
_ant = {}


def _get_llm(key="7B"):
    if key not in _llm:
        from mlx_lm import load

        _llm.clear()  # only one LLM fits comfortably in memory next to the music model
        _llm[key] = load(LLMS[key])
    return _llm[key]


def _get_ant(size):
    if size not in _ant:
        from transformers import AutoModelForCausalLM

        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        _ant[size] = AutoModelForCausalLM.from_pretrained(ANT.format(size=size)).to(dev)
    return _ant[size]


def llm_melody(prompt, bars, sig="4/4", level=3, llm="7B", before=None, phrase=(1, 1), tempo=None, key=None, chords=None):
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler

    model, tok = _get_llm(llm)
    bb = bar_beats(sig)
    style, density, (lo, hi) = COMPLEXITY[level]
    system = (
        SYSTEM.replace("{sig}", sig).replace("{bar_beats:g}", f"{bb:g}")
        .replace("{lo}", str(lo)).replace("{hi}", str(hi)).replace("{style}", style)
    )
    user = f"{prompt}\nTime signature: {sig}. Length: {bars} bars ({bars * bb:g} quarter-note beats)."
    if key:
        user += f"\nKey: {key_label(key)}. Every note must belong to the {key_label(key)} scale."
    if chords:
        names = ", ".join(chords)
        user += (f"\nThe harmony, one chord per bar: {names}. Start and end the phrase on a chord tone of the chord "
                 f"in that bar, and end on the tonic of the key.")
    if tempo and not before:
        user += f"\nTempo: {tempo} bpm (use exactly this tempo in the JSON)."
    if phrase[1] > 1:
        user += f"\nThis is phrase {phrase[0]} of {phrase[1]} of a longer melody; start_beat restarts at 0 for this phrase."
    if before:
        prev = [[p, s, d] for p, s, d in before]
        user += (f"\nThe melody so far (the previous phrase, same format): {json.dumps(prev)}.\nContinue it: answer, "
                 f"develop or vary its motif (sequence, inversion, new rhythm); do not copy it.")
        user += f" Use tempo {tempo}." if tempo else ""
    if phrase[0] == phrase[1] and phrase[1] > 1:
        user += " This is the final phrase: build to the climax if not yet reached, then resolve and end on a stable note."
    elif phrase[0] == 1 and phrase[1] > 1:
        user += " This is the opening phrase: state the main motif clearly."
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    text = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
    max_tokens = 600 + int(bars * bb * density * 18)
    raw = generate(model, tok, prompt=text, max_tokens=max_tokens,
                   sampler=make_sampler(temp=TEMPERATURE[level], top_p=0.95))
    data = _json_with_notes(raw)
    if data is None:
        raise ValueError(f"LLM returned no usable JSON:\n{raw}")
    notes = _clean_notes(data["notes"])
    if not notes:
        raise ValueError(f"LLM returned no valid notes:\n{raw}")
    try:
        tempo_out = int(float(data.get("tempo", 100)))
    except (TypeError, ValueError):
        tempo_out = 100
    return tempo_out, notes, str(data.get("plan", "")), str(data.get("key", ""))


def _json_with_notes(raw):
    """The first JSON object in the LLM's reply that has a "notes" list (see llmjson.extract_json)."""
    obj = extract_json(raw, "notes")
    return obj if obj is not None and isinstance(obj["notes"], list) else None


def _clean_notes(items):
    """[(pitch, start, dur)] from whatever the LLM put in "notes": [p, s, d] lists or {"pitch", "start", "duration"}
    objects. Entries that aren't usable are skipped rather than failing the whole melody."""
    notes = []
    for it in items:
        try:
            if isinstance(it, dict):
                get = lambda *ks: next(it[k] for k in ks if k in it)
                p, s, d = get("pitch", "note", "p"), get("start", "time", "onset", "s"), get("duration", "dur", "length", "d")
            else:
                p, s, d = it[0], it[1], it[2]
            p, s, d = int(p), float(s), float(d)
        except (TypeError, ValueError, KeyError, IndexError, StopIteration):
            continue
        if 0 <= p <= 127 and s >= 0 and d > 0:
            notes.append((p, s, d))
    notes.sort(key=lambda n: n[1])
    return notes


def conform_melody(notes, bars, sig):
    """Force the LLM's notes onto the bar grid: snap to 16ths, drop anything past the last bar, remove
    overlaps. Returns (notes, coverage) where coverage is the fraction of the piece the melody spans."""
    total = bars * bar_beats(sig)
    snapped = {}
    for p, s, d in notes:
        s = round(s * 4) / 4
        d = max(round(d * 4) / 4, 0.25)
        if s < total and s not in snapped:  # one note per onset: the melody is monophonic
            snapped[s] = (p, s, d)
    out = sorted(snapped.values(), key=lambda n: n[1])
    out = [(p, s, min(d, (out[i + 1][1] - s) if i + 1 < len(out) else d, total - s)) for i, (p, s, d) in enumerate(out)]
    out = [n for n in out if n[2] > 0]
    if not out:
        return [], 0.0
    coverage = (out[-1][1] + out[-1][2]) / total
    if coverage >= 0.75:  # ended a little early: let the last note ring to the final barline
        p, s, _ = out[-1]
        out[-1] = (p, s, total - s)
        coverage = 1.0
    return out, coverage


# Krumhansl-Kessler key profiles (how well each pitch class fits a major / minor key built on C)
_KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


_PC = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def parse_key(prompt):
    """Tonic pitch class (and mode) named in a prompt like 'a sad tune in D minor' or 'Bb major', else None.
    A lone lowercase 'a' is the article, not the note, so A must be capitalised."""
    for m in re.finditer(r"\b([A-Ga-g])\s?(#|sharp|♯|b|flat|♭)?[ -]?(major|minor|maj|min)\b", prompt or ""):
        letter, acc, mode = m.groups()
        if letter == "a" and not (acc or "").strip():
            continue
        tonic = _PC[letter.lower()]
        if acc and acc.lower() in ("#", "sharp", "♯"):
            tonic += 1
        elif acc and acc.lower() in ("b", "flat", "♭"):
            tonic -= 1
        return tonic % 12, ("major" if mode.lower().startswith("maj") else "minor")
    return None


PC_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def key_label(key):
    return f"{PC_NAMES[key[0]]} {key[1]}"


def scale_pcs(key):
    """Pitch classes that belong to a key (tonic pitch class, 'major'|'minor')."""
    return {(key[0] + x) % 12 for x in (_SCALE_MAJOR if key[1] == "major" else _SCALE_MINOR)}


def snap_to_key(notes, key, span=(0, 127)):
    """Move every out-of-key note to the nearest note of the scale (the lower one on a tie), staying inside `span`
    (the instrument's playable range; always inside MIDI's 0..127)."""
    pcs = scale_pcs(key)
    lo, hi = max(span[0], 0), min(span[1], 127)
    out = []
    for p, s_, d in notes:
        if p % 12 not in pcs:
            near = [x for x in range(p - 2, p + 3) if lo <= x <= hi and x % 12 in pcs] or \
                   [x for x in range(p - 6, p + 7) if lo <= x <= hi and x % 12 in pcs]
            if near:
                p = min(near, key=lambda x: (abs(x - p), x))
        out.append((p, s_, d))
    return out


def session_key():
    """The key the current session was generated in (set when melodies were made with a key), or None."""
    return key_for_file(current_session() / "x") if current_session() else None


def key_for_file(path):
    """Key stored in the session.json of the folder holding `path` (or its parent, for parts/ files)."""
    for folder in (Path(path).parent, Path(path).parent.parent):
        meta = folder / "session.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text())
            except (OSError, ValueError):
                return None
            key = data.get("key")
            if key:
                return (int(key[0]), key[1])
            # older sessions, or the command line, may only have the plan: it carries the key too
            plan_data = data.get("plan")
            if plan_data and plan_data.get("key"):
                return (int(plan_data["key"][0]), plan_data["key"][1])
            return None
    return None


def plan_json(theplan, form=DEFAULT_FORM):
    """The plan in a form that can be written to session.json."""
    return {"key": list(theplan["key"]), "bars": theplan["bars"], "sig": theplan["sig"], "bb": theplan["bb"],
            "level": theplan["level"], "form": int(form), "chords": list(theplan["chords"]),
            "phrases": theplan["phrases"], "cadences": theplan["cadences"],
            "boundaries": [list(b) for b in theplan["boundaries"]]}


def plan_for_file(path):
    """The plan of the piece `path` belongs to (see plan.py), or None if it has none."""
    for folder in (Path(path).parent, Path(path).parent.parent):
        meta = folder / "session.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text()).get("plan")
            except (OSError, ValueError):
                return None
            if not data:
                return None
            data = dict(data)
            data["key"] = tuple(data["key"])
            data["boundaries"] = [tuple(b) for b in data.get("boundaries", [])]
            return data
    return None


def estimate_key(notes):
    """Best-fit (tonic pitch class, 'major'|'minor') for [(pitch, start, dur)], weighting notes by duration."""
    hist = [0.0] * 12
    for p, _, d in notes:
        hist[p % 12] += d
    # tunes tend to start and finish on their home note, so those two notes count extra
    hist[notes[0][0] % 12] += 3
    hist[notes[-1][0] % 12] += 3
    best = None
    for tonic in range(12):
        for mode, profile in (("major", _KK_MAJOR), ("minor", _KK_MINOR)):
            rotated = [profile[(pc - tonic) % 12] for pc in range(12)]
            mh, mr = sum(hist) / 12, sum(rotated) / 12
            num = sum((h - mh) * (r - mr) for h, r in zip(hist, rotated))
            den = (sum((h - mh) ** 2 for h in hist) * sum((r - mr) ** 2 for r in rotated)) ** 0.5 or 1.0
            if best is None or num / den > best[0]:
                best = (num / den, tonic, mode)
    return best[1], best[2]


def resolve_ending(notes, bars, sig, key_hint=None, span=(0, 127)):
    """Make the melody end on its tonic. Only the final note changes: it moves to the nearest tonic (nearest to the
    note before it, so the approach stays smooth) that the instrument can play (`span`); its timing and every other
    note are left exactly as they were. If the range holds no tonic at all, the note is left alone."""
    if not notes:
        return notes
    tonic_pc = key_hint[0] if key_hint else estimate_key(notes)[0]
    p, s, d = notes[-1]
    if p % 12 == tonic_pc:
        return notes  # already home
    anchor = notes[-2][0] if len(notes) > 1 else p
    lo, hi = max(span[0], 0), min(span[1], 127)
    options = [x for x in range(anchor - 7, anchor + 8) if x % 12 == tonic_pc and lo <= x <= hi] or \
              [x for x in range(lo, hi + 1) if x % 12 == tonic_pc]
    if not options:
        return notes
    tonic = min(options, key=lambda x: (abs(x - anchor), abs(x - p)))
    return notes[:-1] + [(tonic, s, d)]


def write_melody_midi(path, tempo, notes, program, sig="4/4", total_beats=None):
    tpb = 480
    mid = mido.MidiFile(type=1, ticks_per_beat=tpb)
    mid.tracks.append(conductor_track(tempo, sig))
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("program_change", program=program, channel=0, time=0))
    events = []
    for p, s, d in notes:
        events.append((round(s * tpb), 1, p))
        events.append((round((s + d) * tpb), 0, p))  # note-off sorts before note-on at same tick
    events.sort()
    last = 0
    for tick, on, p in events:
        track.append(
            mido.Message("note_on" if on else "note_off", note=p, velocity=90 if on else 0, channel=0, time=tick - last)
        )
        last = tick
    if total_beats is not None:  # pad the file out to the final barline so its length is exactly N bars
        track.append(mido.MetaMessage("end_of_track", time=max(round(total_beats * tpb) - last, 0)))
    mid.save(str(path))
    return mid.length  # seconds


def retime(path, tempo, sig):
    """Anticipation's output has no tempo or time signature (its clock is plain seconds, i.e. 120 bpm).
    Rewrite it with the melody's real tempo/signature so Logic's bar grid lines up, keeping every note
    at the same absolute time."""
    src = mido.MidiFile(str(path))
    tpb = 480
    per_sec = tpb * tempo / 60
    out = mido.MidiFile(type=1, ticks_per_beat=tpb)
    out.tracks.append(conductor_track(tempo, sig))
    for trk in src.tracks:
        # absolute seconds for each message in this track, using the source's constant 120 bpm clock
        new, t_sec, last = mido.MidiTrack(), 0.0, 0
        new.append(mido.MetaMessage("track_name", name=track_label(trk), time=0))
        for msg in trk:
            t_sec += mido.tick2second(msg.time, src.ticks_per_beat, 500000)
            if msg.is_meta and msg.type in ("set_tempo", "time_signature", "track_name"):
                continue
            tick = round(t_sec * per_sec)
            new.append(msg.copy(time=tick - last))
            last = tick
        out.tracks.append(new)
    out.save(str(path))


def compose(prompt, bars, sig, level, llm, log, bpm=None, key=None, harmony=None):
    """Write the melody a short phrase at a time: small LLMs lose the plot (and stop early) over long
    spans, and each phrase can then answer / develop the one before it.
    `harmony` is the piece's plan (see plan.py); the chords of the bars this phrase covers are given to the LLM."""
    bb = bar_beats(sig)
    chunk = 2 if level >= 4 else 4
    n_chunks = -(-bars // chunk)
    all_notes, plans, tempo, prev, declared = [], [], bpm, None, ""
    chord_names = None
    if harmony:
        tonic, mode = harmony["key"]
        chord_names = [plan.chord_name(tonic, mode, deg) for deg in harmony["chords"]]
    for c in range(n_chunks):
        n_bars = min(chunk, bars - c * chunk)
        target = COMPLEXITY[level][1] * n_bars * bb
        here = chord_names[c * chunk: c * chunk + n_bars] if chord_names else None
        best = None
        for attempt in range(1, 4):
            try:
                t, raw, idea, said_key = llm_melody(prompt, n_bars, sig, level, llm, prev, (c + 1, n_chunks), tempo, key, here)
            except ValueError as e:   # unusable reply: ask again instead of failing the whole melody
                log(f"      phrase {c + 1}/{n_chunks} attempt {attempt}: unusable reply ({str(e).splitlines()[0][:50]}), retrying")
                continue
            notes, coverage = conform_melody(raw, n_bars, sig)
            richness = min(len(notes) / target, 1.0)
            score = 2 * coverage + richness + min(len({d for _, _, d in notes}) / (level + 1), 1.0)
            if best is None or score > best[0]:
                best = (score, t, notes, idea, said_key)
            if coverage >= 0.95 and richness >= 0.7:
                break
            log(f"      phrase {c + 1}/{n_chunks} attempt {attempt}: {len(notes)} notes (want ~{target:.0f}), "
                f"fills {coverage:.0%}, retrying")
        if best is None:
            raise RuntimeError("The LLM did not produce a usable melody; try again or reword the prompt")
        _, t, notes, idea, said_key = best
        declared = declared or said_key  # the key the LLM says it wrote the opening phrase in
        if not notes:
            raise RuntimeError("The LLM did not produce a usable melody; try again or reword the prompt")
        tempo = tempo or max(40, min(t, 220))  # a requested bpm wins over whatever the LLM wrote
        if idea:
            plans.append(idea)
        shift = c * chunk * bb
        all_notes += [(p, s + shift, d) for p, s, d in notes]
        prev = notes
        log(f"      phrase {c + 1}/{n_chunks}: {len(notes)} notes")
    return all_notes, tempo, plans, declared


def melody_score(notes, total_beats, target_density):
    """Heuristic 'interestingness' of a monophonic melody [(pitch, start, dur)] -> (score, details)."""
    import math
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


def apply_breaths(notes, bars, sig, phrasing):
    """Leave rests between ideas (every two bars) and phrases (every four) so the melody doesn't run on without
    pause. phrasing 0 = flowing, 1 = balanced, 2 = spacious."""
    import composer

    bb = bar_beats(sig)
    return composer.breathe(notes, [(b * bb, "phrase" if b % 4 == 0 else "idea") for b in range(2, bars, 2)], phrasing)


# Melodic shape (0 stepwise, 1 balanced, 2 angular): how hard the Anticipation sampler is nudged away from scale runs,
# repeated notes and shuffling among a few pitches, and towards leaps that are answered by a step back.
SHAPE_BIAS = {0: None,
              1: {"run": 1.5, "leap": 0.5, "repeat": 0.7, "variety": 0.7},
              2: {"run": 2.5, "leap": 1.0, "repeat": 1.2, "variety": 1.2}}


def melody_candidates(prompt, bars, sig, level, llm, program, size, count, engine, log, bpm=None, resolve=True, key=None,
                      phrasing=1, shape=1, form=DEFAULT_FORM, theplan=None):
    """Yield up to `count` distinct melodies as dicts {notes, tempo, score}.

    hybrid: the LLM writes an opening bar (a fresh one for every two candidates, so the options differ in
    character, not just in detail) and Anticipation, trained on real MIDI so its rhythms are natural, develops
    it into the full melody on the metrical grid.  llm: the LLM writes each whole melody.
    `form` is how much of the piece's plan the melody follows (0 free ... 3 the whole plan)."""
    bb = bar_beats(sig)
    total_beats = bars * bb
    target = COMPLEXITY[level][1]
    made = 0
    enforce = key or parse_key(prompt)   # a key picked in the app wins over one mentioned in the prompt
    pcs = scale_pcs(enforce) if enforce else None
    weights = plan.form_weights(form)
    if engine == "theory":   # rule-based composer: no LLM, no model (see composer.py)
        import random

        import composer

        lo, hi = COMPLEXITY[level][2]
        plo, phi = limits(program)[:2]
        lo, hi = (max(lo, plo), min(hi, phi)) if max(lo, plo) <= min(hi, phi) else (plo, phi)
        base = random.randrange(1 << 30)
        tempo = bpm or 90
        for k in range(count):
            rng = random.Random(base + 7919 * k)
            thekey = enforce or composer.auto_key(prompt, rng)
            theplan = theplan or plan.build_plan(bars, sig, thekey, rng)
            notes, info = composer.compose(bars, sig, thekey, level, rng, lo, hi, phrasing, shape,
                                           chords=theplan["chords"])
            notes, _ = conform_melody(notes, bars, sig)
            notes = [(fold_pitch(p, program), s_, d) for p, s_, d in notes]
            if enforce:
                notes = snap_to_key(notes, enforce, limits(program)[:2])
            # the theory composer builds its own cadences (a stepwise approach onto the target), so the plan's
            # phrase endings are not forced on top of it
            if resolve:
                notes = resolve_ending(notes, bars, sig, enforce, limits(program)[:2])
            log(f"      option {k + 1}: form {info['form']}, chords {' '.join(map(str, [c + 1 for c in info['chords']]))}")
            yield {"notes": notes, "tempo": tempo, "score": melody_score(notes, bars * bar_beats(sig), COMPLEXITY[level][1])[0]}
        return
    if engine == "llm":
        import random

        for _ in range(count):
            rng = random.Random()
            thekey = enforce or parse_key(prompt) or (0, "major")
            theplan = theplan or plan.build_plan(bars, sig, thekey, rng)
            notes, tempo, _plans, declared = compose(prompt, bars, sig, level, llm, log, bpm, enforce, harmony=theplan)
            notes, _ = conform_melody(notes, bars, sig)
            notes = [(fold_pitch(p, program), s_, d) for p, s_, d in notes]
            if enforce:
                notes = snap_to_key(notes, enforce, limits(program)[:2])
            notes = apply_breaths(notes, bars, sig, phrasing)
            if weights.get("cadence"):
                notes = plan.resolve_phrase_endings(notes, theplan, enforce or parse_key(declared) or thekey,
                                                    limits(program)[:2])
            if resolve:
                notes = resolve_ending(notes, bars, sig, enforce or parse_key(declared), limits(program)[:2])
            made += 1
            yield {"notes": notes, "tempo": tempo, "score": melody_score(notes, total_beats, target)[0]}
        return

    from anticipation.convert import midi_to_events
    from anticipation.sample import generate
    from anticipation.tokenize import extract_instruments

    model = _get_ant(size)
    num, den = (int(x) for x in sig.split("/"))
    lo, hi = COMPLEXITY[level][2]
    plo, phi = limits(program)[:2]
    lo, hi = (max(lo, plo), min(hi, phi)) if max(lo, plo) <= min(hi, phi) else (plo, phi)  # stay inside the instrument
    import random

    thekey = enforce or parse_key(prompt) or (0, "major")
    theplan = theplan or plan.build_plan(bars, sig, thekey, random.Random())
    pool = {"allowed": [program], "balance": 0, "ranges": {program: (lo, hi)}}
    if SHAPE_BIAS.get(shape):
        pool["melodic"] = dict(SHAPE_BIAS[shape], instrument=program)
    if pcs:
        pool["pitch_classes"] = pcs  # the sampler may only choose notes of the scale
    tries = 0
    while made < count and tries < count * 3:
        try:
            seed, tempo, plans, declared = compose(prompt, 1, sig, level, llm, log, bpm, enforce)
        except (ValueError, KeyError, TypeError, RuntimeError) as e:   # the LLM sometimes answers with unusable JSON: just ask again
            tries += 1
            log(f"      (opening bar unusable, retrying: {str(e).splitlines()[0][:60]})")
            continue
        seed = [(fold_pitch(p, program), s_, d) for p, s_, d in seed]
        if enforce:
            seed = snap_to_key(seed, enforce, limits(program)[:2])
        # the opening bar is the piece's motif: it is kept as the language model wrote it (in key), because snapping
        # it to the chord would take away the passing notes that make an idea worth coming back to
        motif = [(fold_pitch(p, program), s_, d) for p, s_, d in seed]
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        seed_path = TMP_DIR / "_seed.mid"
        seed_sec = write_melody_midi(seed_path, tempo, motif, program, sig, bb)
        seed_events, _ = extract_instruments(midi_to_events(str(seed_path)), [])  # whole seed is the prompt
        length = total_beats * 60 / tempo
        q = 6000.0 / tempo
        grid = {
            "step": q / 4, "beat": q * 1.5 if den == 8 and num % 3 == 0 else q, "bar": q * bb,
            "max_gap": q * 2, "max_chord": 1, "beat_boost": 0.5, "bar_boost": 1.0,
        }
        # the harmony: each bar's chord is what the melody prefers, but passing notes are still allowed
        if weights.get("chord"):
            pool["regions"] = plan.plan_regions(theplan, tempo)
            pool["chord_weight"] = weights["chord"]
        targets = []
        near = sum(p for p, _, _ in motif) / max(len(motif), 1)
        if weights.get("motif"):
            # the motif is what the first phrase already says, so only the later phrases restate it
            for i in range(1, len(theplan["phrases"])):
                targets += plan.motif_targets(motif, theplan, tempo, i)
        if weights.get("cadence"):
            targets += plan.cadence_targets(theplan, near, tempo)
        if targets:
            pool.setdefault("melodic", {}).update({
                "targets": targets, "target_weight": max(weights.get("motif", 0), weights.get("cadence", 0)),
                "target_tol": q * PLAN_TARGET_TOL, "target_sigma": PLAN_TARGET_SIGMA,
            })
        for _ in range(2):
            if made >= count:
                break
            tries += 1
            ev = generate(model, seed_sec, length, inputs=seed_events, top_p=min(0.85 + 0.03 * level, 0.99),
                          grid=grid, instruments=pool)
            raw = [((n - NOTE_OFFSET) % 128, t / 100 * tempo / 60, d / 100 * tempo / 60)  # ticks -> seconds -> beats
                   for t, d, n in zip(ev[0::3], ev[1::3], ev[2::3])]
            notes, _ = conform_melody(raw, bars, sig)
            if len(notes) < 4:
                continue
            if enforce:
                notes = snap_to_key(notes, enforce, limits(program)[:2])
            notes = apply_breaths(notes, bars, sig, phrasing)
            if weights.get("cadence"):
                notes = plan.resolve_phrase_endings(notes, theplan, enforce or parse_key(declared) or thekey,
                                                    limits(program)[:2])
            if resolve:
                notes = resolve_ending(notes, bars, sig, enforce or parse_key(declared), limits(program)[:2])
            made += 1
            yield {"notes": notes, "tempo": tempo, "score": melody_score(notes, total_beats, target)[0]}


def hybrid_melody(prompt, bars, sig, level, llm, program, size, top_p, candidates, tempo_hint, log, resolve=True, key=None,
                  engine="hybrid", phrasing=1, shape=1, form=DEFAULT_FORM, theplan=None):
    """Best-scoring melody out of `candidates` (used by the command-line one-shot mode; any engine)."""
    best = None
    for k, c in enumerate(melody_candidates(prompt, bars, sig, level, llm, program, size, candidates, engine, log, tempo_hint,
                                            resolve, key, phrasing, shape, form, theplan), 1):
        log(f"      melody candidate {k}/{candidates}: {len(c['notes'])} notes, score {c['score']:.1f}")
        if best is None or c["score"] > best["score"]:
            best = c
    if best is None:
        raise RuntimeError("Anticipation produced no melody")
    return best["notes"], best["tempo"]


def make_melodies(prompt, bars=8, program=0, size="small", sig="4/4", complexity=3, llm="7B", count=4,
                  engine="hybrid", name=None, log=print, on_candidate=None, bpm=None, resolve=True, key=None, phrasing=1,
                  shape=1, form=DEFAULT_FORM):
    """Stage 1: write `count` melody options to output/<name>-optN.mid. Returns a list of dicts
    {index, path, tempo, sig, score}; on_candidate(dict) is called as each one is ready."""
    folder = ensure_session(prompt)
    name = name or time.strftime("%H%M%S")
    total_beats = bars * bar_beats(sig)
    out = []
    enforce = key or parse_key(prompt)
    if engine == "theory" and not enforce:  # the composer needs a key; choose one for the whole batch from the prompt's mood
        import random

        import composer

        enforce = composer.auto_key(prompt, random.Random())
        key = enforce
    _write_meta(folder, key=list(enforce) if enforce else None)   # accompaniment and sheet music follow this key
    theplan = None
    if enforce and plan.form_weights(form):
        theplan = plan.build_plan(bars, sig, enforce)
        # the accompaniment and the sheet music follow the same plan
        _write_meta(folder, plan=plan_json(theplan, form), form=int(form))
    log(f"Composing {count} melody options" + (f" in {key_label(enforce)}" if enforce else "") + "...")
    for c in melody_candidates(prompt, bars, sig, complexity, llm, program, size, count, engine, log, bpm, resolve, key, phrasing, shape,
                               form=form, theplan=theplan):
        k = len(out) + 1
        path = folder / f"{name}-opt{k}.mid"
        write_melody_midi(path, c["tempo"], c["notes"], program, sig, total_beats)
        info = {"index": k, "path": path, "tempo": c["tempo"], "sig": sig, "score": c["score"]}
        out.append(info)
        log(f"      option {k}: {len(c['notes'])} notes, {c['tempo']} bpm")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError("No usable melody was produced; try again")
    return out


def midi_info(path):
    """Tempo (bpm), time signature ('n/d') and length in seconds of a MIDI file (first tempo/signature wins)."""
    mid = mido.MidiFile(str(path))
    tempo, sig = None, None
    for trk in mid.tracks:
        for m in trk:
            if m.type == "set_tempo" and tempo is None:
                tempo = mido.tempo2bpm(m.tempo)
            if m.type == "time_signature" and sig is None:
                sig = f"{m.numerator}/{m.denominator}"
    return int(round(tempo or 120)), sig or "4/4", mid.length


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


def instrument_name(program):
    return "Drums" if program == DRUMS else GM_NAMES[program]


def add_part_options(source_midi, program, count=3, size="small", top_p=0.98, attempts=8, enforce_meter=True,
                     beat_boost=1.0, playable=True, name=None, log=print, on_candidate=None, texture_style=None, in_key=True,
                     direction="", llm_directions=True):
    """Write `count` alternative parts for ONE instrument, each as a complete piece: the existing piece (held fixed)
    plus a freshly generated part for `program`. Files go to <session>/parts/. on_candidate(info) fires as each is
    ready. Approving an option just means using its file as the piece for the next round."""
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
    d = interpret_direction(direction, instrument_name(program), context, log, llm_directions)
    if not d["empty"]:
        log("      " + dr.describe(d))
    style = d["style"] or texture_style   # a pattern named in the direction overrides the pattern picker
    if style:  # rule-based chords / arpeggios / bass lines instead of a model-generated part
        return texture_options(source_midi, style, count, name, log, on_candidate, program, d)

    from anticipation import ops
    from anticipation.convert import events_to_midi, midi_to_events
    from anticipation.sample import generate
    from anticipation.tokenize import extract_instruments

    folder = ensure_session(Path(source_midi).stem)
    parts_dir = folder / "parts"
    parts_dir.mkdir(exist_ok=True)
    name = name or time.strftime("%H%M%S")
    label = instrument_name(program)

    tempo, sig, length = midi_info(source_midi)
    length = min(length, 100.0)  # Anticipation's clock tops out at 100 s
    events = midi_to_events(str(source_midi))
    present = set(ops.get_instruments(events))
    if program in present:
        raise RuntimeError(f"{label} is already in the piece. Pick a different instrument.")
    if len(present) >= 16:
        raise RuntimeError("The piece already has 16 instruments, the most Anticipation can handle.")
    _, controls = extract_instruments(events, list(present))  # the whole existing piece is held fixed
    if length >= 100:
        controls = ops.clip(controls, 0, length, clip_duration=True)

    q = 6000.0 / tempo
    num, den = (int(x) for x in sig.split("/"))
    poly = limits(program)[2] if playable else 4
    # What the direction can steer in a model-generated part: how busy it is, where in the instrument's range it
    # sits, how long its notes are, whether it leans on or away from the beat, whether it answers the melody
    # instead of playing with it, and how adventurous the sampling is.
    dens, reg, ln = d["density"], d["register"], d["length"]
    boost = {-2: -2.5, -1: -1.5, 1: 2.0, 2: 3.5}.get(d["rhythm"], beat_boost)
    grid = {
        "step": (q / 4 if dens > 0 else q / 2) if enforce_meter else 1,
        "beat": q * 1.5 if den == 8 and num % 3 == 0 else q, "bar": q * bar_beats(sig),
        "max_gap": q * {-2: 6, -1: 4, 0: 2, 1: 1, 2: 0.5}[dens], "max_chord": 1 if dens < 0 else max(1, poly),
        "beat_boost": boost if enforce_meter else 0.0, "bar_boost": boost * 2 if enforce_meter else 0.0,
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
    top_p = {-2: 0.6, -1: 0.85, 1: 1.0, 2: 1.0}.get(d["adventurous"], top_p)
    skey = key_for_file(source_midi) if in_key else None
    if skey and program != DRUMS:
        pool["pitch_classes"] = scale_pcs(skey)
        log(f"      keeping the part in {key_label(skey)}")
    theplan = plan_for_file(source_midi)
    if theplan is not None and program != DRUMS:
        w = plan.form_weights(theplan.get("form", DEFAULT_FORM))
        if w.get("chord"):
            pool["regions"] = plan.plan_regions(theplan, tempo)   # ... and on the melody's own chords
            pool["chord_weight"] = w["chord"]
            log("      the part follows the melody's chords")
    min_notes = {-2: max(3, int(0.08 * length)), -1: max(4, int(0.15 * length)), 0: max(6, int(0.5 * length)),
                 1: max(10, int(1.0 * length)), 2: max(14, int(1.5 * length))}[dens]
    model = _get_ant(size)

    log(f"Writing {count} options for {label}...")
    out = []
    for k in range(1, count + 1):
        best = []
        for attempt in range(1, attempts + 1):
            part = generate(model, 0, length, inputs=[], controls=controls, top_p=top_p, grid=grid, instruments=pool)
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
        path = parts_dir / f"{name}-{slugify(label)}-{k}.mid"
        events_to_midi(ops.combine(best, controls)).save(str(path))
        retime(path, tempo, sig)
        if velocity_for(d):
            apply_velocity(path, program, velocity_for(d))
        info = {"index": len(out) + 1, "path": path, "program": program, "added": len(best) // 3}
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
    """Set how hard one instrument is played (MIDI velocity) in a file written by this app."""
    mid = mido.MidiFile(str(path))
    for trk in mid.tracks:
        notes = [m for m in trk if m.type == "note_on"]
        if not notes:
            continue
        drums = notes[0].channel == 9
        prog = next((m.program for m in trk if m.type == "program_change"), 0)
        if (drums and program == DRUMS) or (not drums and prog == program):
            for m in notes:
                if m.velocity > 0:
                    m.velocity = velocity
    mid.save(str(path))


def texture_options(source_midi, style="mix", count=3, name=None, log=print, on_candidate=None, program=0, d=None):
    """Chords / arpeggios / bass lines for one instrument under the existing piece, by music theory (see texture.py).
    The piece is left exactly as it is; each option is the whole piece plus a different pattern, written to
    <session>/parts/. If the instrument is already there and is a keyboard (e.g. the melody is on piano), the new
    notes join that same instrument, like a left hand."""
    import texture as tx
    from anticipation import ops
    from anticipation.convert import events_to_midi, midi_to_events
    from anticipation.tokenize import extract_instruments
    from anticipation.vocab import DUR_OFFSET, NOTE_OFFSET, TIME_OFFSET

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
    already = program in present
    if already and program not in tx.LEFT_HAND:
        raise RuntimeError(f"{instrument_name(program)} is already in the piece. Pick a different instrument.")
    _, controls = extract_instruments(events, present)
    label0 = instrument_name(program)
    log(f"Writing {count} {label0.lower()} options ({key_label(key)})...")
    out = []
    theplan = plan_for_file(source_midi)
    plan_chords = None
    if theplan is not None and plan.form_weights(theplan.get("form", DEFAULT_FORM)).get("chord"):
        plan_chords = theplan["chords"]
    for k in range(1, count + 1):
        label, part = tx.option(notes, tempo, sig, key, style, k, program, d, plan_chords)
        ticks = lambda beats: round(beats * 60.0 / tempo * 100)
        tokens = []
        for pitch, start, dur in sorted(part, key=lambda n: (n[1], n[0])):
            tokens += [TIME_OFFSET + ticks(start), DUR_OFFSET + max(1, ticks(dur)), NOTE_OFFSET + program * 128 + pitch]
        path = parts_dir / f"{name}-{slugify(label0)}-{slugify(label)}-{k}.mid"
        events_to_midi(ops.combine(tokens, controls)).save(str(path))
        retime(path, tempo, sig)
        if velocity_for(d) and not already:
            apply_velocity(path, program, velocity_for(d))
        info = {"index": k, "path": path, "program": program, "added": len(part), "label": label}
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


def interpret_direction(text, instrument, context, log=print, use_llm=True, model_key=None):
    """Free-text direction -> controls. A language model reads it (so "something dreamy that floats underneath" works);
    the keyword matcher is the fallback when the LLM is off, unavailable, or returns nothing usable."""
    import direction as dr

    keywords = dr.parse(text)
    if keywords["empty"] or not use_llm:
        return keywords
    from mlx_lm import generate

    # Prefer whichever LLM is already in memory; otherwise the 7B (reads directions best), then the lighter 3B.
    order = [model_key] if model_key else (list(_llm) or []) + [k for k in ("7B", "3B") if k not in _llm]
    for key in order:
        try:
            model, tok = _get_llm(key)
            msgs = [{"role": "system", "content": dr.SYSTEM}, {"role": "user", "content": dr.build_prompt(text, instrument, context)}]
            raw = generate(model, tok, prompt=tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False), max_tokens=320)
            got = dr.from_llm(text, raw)
            if got is None:
                log(f"      (the {key} LLM's reply wasn't usable)")
                continue   # try the next model in the chain before giving up on the LLM
            return got   # may have no effect (e.g. "purple"): describe() then says so rather than guessing
        except Exception as e:
            log(f"      (the {key} LLM couldn't run: {type(e).__name__})")
    log("      (no LLM available, so keyword matching was used instead)")
    return keywords


def make_music(
    prompt,
    bars=8,
    program=0,
    size="small",
    top_p=0.98,
    attempts=8,
    min_density=1.5,
    melody_only=False,
    name=None,
    log=print,
    sig="4/4",
    enforce_meter=True,
    beat_boost=1.0,
    complexity=3,
    llm="7B",
    ensemble=0,
    melody_engine="hybrid",
    candidates=5,
    source_midi=None,
    bpm=None,
    resolve=True,
    playable=True,
    key=None,
    in_key=True,
    phrasing=1,
    shape=1,
    form=DEFAULT_FORM,
):
    """Run the whole pipeline. Returns (melody_path, full_path or None).

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
        thekey = key or parse_key(prompt)
        theplan = plan.build_plan(bars, sig, thekey) if thekey and plan.form_weights(form) else None
        if thekey:
            _write_meta(folder, key=list(thekey))     # the accompaniment and the sheet music follow this key
        notes, tempo = hybrid_melody(prompt, bars, sig, complexity, llm, program, size, top_p,
                                     candidates if melody_engine != "llm" else 1, bpm, log, resolve, key,
                                     melody_engine, phrasing, shape, form, theplan)
        if theplan is not None:
            _write_meta(folder, plan=plan_json(theplan, form), form=int(form))
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
        "beat_boost": beat_boost,
        "bar_boost": beat_boost * 2,
    } if enforce_meter else None
    if grid is None and ensemble >= 4:
        grid = {"step": 1, "beat": q, "bar": q * bar_beats(sig), "max_gap": q}

    pool = None
    if ensemble > 0:
        chosen = [i for i in ENSEMBLE if i not in present][: max(0, min(ensemble, 16 - len(present)))]
        pool = sampler_pool(chosen, balance=1.5) if playable else {"allowed": chosen, "balance": 1.5}
        log(f"      ensemble: {len(present)} source/melody + {len(chosen)} added instruments")
    elif playable:  # model's choice of instruments, but each still held to its own range and polyphony
        pool = sampler_pool([i for i in range(129) if i not in present], free=True)
    if pool is not None and playable and grid is not None:
        grid["max_chord"] = min(24, max(4, 3 * len(pool["allowed"])))  # per-instrument limits do the real work
    skey = key_for_file(melody_path) if in_key else None   # the key the melody was generated in, if one was chosen
    if skey and pool is not None:
        pool["pitch_classes"] = scale_pcs(skey)
        log(f"      keeping the added parts in {key_label(skey)}")
    theplan = plan_for_file(melody_path)
    if theplan is not None and pool is not None:
        w = plan.form_weights(theplan.get("form", DEFAULT_FORM))
        if w.get("chord"):
            pool["regions"] = plan.plan_regions(theplan, tempo)   # ... and on the melody's own chords
            pool["chord_weight"] = w["chord"]
            log("      the added parts follow the melody's chords")

    log("[3/3] Generating accompaniment (this can take a few minutes)...")
    # The model sometimes picks a first event past the end of the clip and returns nothing (or a
    # very sparse part), so resample until the accompaniment has a reasonable note density.
    min_notes = int(min_density * length)
    if ensemble > 0:  # a big ensemble should give every instrument a real part, not a single note each
        min_notes = max(min_notes, 6 * len(pool["allowed"]))
    best = []
    for attempt in range(1, attempts + 1):
        accomp = generate(model, 0, length, inputs=events, controls=melody, top_p=top_p, grid=grid, instruments=pool)
        n = len(accomp) // 3
        if n > len(best) // 3:
            best = accomp
        if n >= min_notes:
            break
        log(f"      attempt {attempt}: only {n} accompaniment notes (want >= {min_notes}), retrying")
    if not best:
        raise RuntimeError("Anticipation produced no accompaniment; try more attempts")
    if playable:
        best, (folded, trimmed, dropped) = make_playable(best)
        log(f"      playability pass: {folded} notes folded into range, {trimmed} overlaps trimmed, {dropped} dropped")
    used = sorted(ops.get_instruments(best))
    log(f"      accompaniment instruments used: {len(used)}")
    full = ops.combine(best, melody)
    out = folder / f"{name}-full.mid"
    events_to_midi(full).save(str(out))
    retime(out, tempo, sig)
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
    ap.add_argument("--top-p", type=float, default=0.98)
    ap.add_argument("--attempts", type=int, default=8, help="max resamples if the accompaniment is empty/sparse")
    ap.add_argument("--min-density", type=float, default=1.5, help="min accompaniment notes per second")
    ap.add_argument("--no-meter", action="store_true", help="don't constrain the accompaniment to the metrical grid")
    ap.add_argument("--beat-boost", type=float, default=1.0, help="how strongly onsets favour beats (0 = grid only)")
    ap.add_argument("--complexity", type=int, default=3, choices=[1, 2, 3, 4, 5], help="melody complexity (1 simple .. 5 virtuosic)")
    ap.add_argument("--llm", default="7B", choices=list(LLMS), help="melody LLM (14B downloads ~8 GB on first use)")
    ap.add_argument("--ensemble", type=int, default=0, help="accompaniment instruments, 1-15 (0 = model's choice)")
    ap.add_argument("--melody-engine", default="hybrid", choices=["hybrid", "llm", "theory"],
                    help="hybrid: LLM seeds one bar, Anticipation develops it (more natural); llm: LLM writes it all")
    ap.add_argument("--candidates", type=int, default=5, help="melodies to generate and score (hybrid engine)")
    ap.add_argument("--source-midi", default=None, help="orchestrate this MIDI file instead of generating a melody")
    ap.add_argument("--key", default=None, help='force a key, e.g. "D minor" or "Bb major"')
    ap.add_argument("--phrasing", type=int, default=1, choices=[0, 1, 2], help="rests between ideas: 0 flowing, 1 balanced, 2 spacious")
    ap.add_argument("--shape", type=int, default=1, choices=[0, 1, 2], help="melodic shape: 0 stepwise, 1 balanced, 2 angular")
    ap.add_argument("--form", type=int, default=DEFAULT_FORM, choices=[0, 1, 2, 3],
                    help="how much of the piece's plan the melody follows: 0 free, 1 harmony, 2 cadences, 3 the whole plan")
    ap.add_argument("--seed", type=int, default=None, help="random seed, for repeatable output")
    ap.add_argument("--no-resolve", action="store_true", help="don't force the melody to end on the tonic")
    ap.add_argument("--no-playable", action="store_true", help="skip the instrument range / polyphony limits")
    ap.add_argument("--melody-only", action="store_true", help="Skip Anticipation, just save the LLM melody")
    ap.add_argument("--name", default=None, help="file name stem for the outputs")
    ap.add_argument("--session", default=None, help="session (folder) name; defaults to the prompt")
    a = ap.parse_args()
    if a.seed is not None:
        seed_everything(a.seed)
    new_session(a.session, a.prompt)  # each command-line run is its own session
    make_music(
        a.prompt, a.bars, a.program, a.size, a.top_p, a.attempts, a.min_density, a.melody_only, a.name,
        sig=a.sig, enforce_meter=not a.no_meter, beat_boost=a.beat_boost,
        complexity=a.complexity, llm=a.llm, ensemble=a.ensemble,
        melody_engine=a.melody_engine, candidates=a.candidates, source_midi=a.source_midi,
        bpm=max(40, min(a.bpm, 220)), resolve=not a.no_resolve, playable=not a.no_playable, key=parse_key(a.key or ""),
        phrasing=a.phrasing, shape=a.shape, form=a.form,
    )


if __name__ == "__main__":
    main()
