"""Keys, modes and scales: reading them from text, estimating them from notes, holding a melody to them."""
import json
import re
from pathlib import Path


import plan
from config import DEFAULT_FORM
from sessions import current_session


# Krumhansl-Kessler key profiles (how well each pitch class fits a major / minor key built on C)
_KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


_PC = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def parse_key(prompt):
    """Tonic pitch class (and mode) named in a prompt like 'a sad tune in D minor', 'Bb major' or 'F# lydian', else None.
    A lone lowercase 'a' is the article, not the note, so A must be capitalised. When a prompt names more than one
    ('C major scale in C lydian'), a modal one (dorian, lydian...) wins over major or minor, as the more specific."""
    found = []
    for m in re.finditer(r"\b([A-Ga-g])\s?(#|sharp|♯|b|flat|♭)?[ -]?(major|minor|maj|min|ionian|aeolian|dorian|phrygian|lydian|mixolydian|locrian)\b",
                         prompt or "", re.I):
        letter, acc, mode = m.groups()
        if letter == "a" and not (acc or "").strip():
            continue
        tonic = _PC[letter.lower()]
        if acc and acc.lower() in ("#", "sharp", "♯"):
            tonic += 1
        elif acc and acc.lower() in ("b", "flat", "♭"):
            tonic -= 1
        mode = mode.lower()
        found.append((tonic % 12, plan.MODE_ALIASES.get(mode, mode)))
    return next((k for k in found if plan.is_modal(k[1])), found[0] if found else None)


def parse_mode(prompt):
    """A mode named in a prompt with or without a note ('in lydian mode', 'a dorian tune'), else None. Used to override
    the mode of whatever key the prompt or the app gave, and to set the mode when no note is named."""
    m = re.search(r"\b(dorian|phrygian|lydian|mixolydian|locrian|ionian|aeolian)\b", prompt or "", re.I)
    if not m:
        return None
    name = m.group(1).lower()
    return plan.MODE_ALIASES.get(name, name)


PC_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def key_label(key):
    return f"{PC_NAMES[key[0]]} {key[1]}"


def scale_names(key):
    """The notes of a key's scale, from its tonic up, as names ('G A B C# D E F#'). Worked out from the mode's table
    (plan.MODES), not by the language model: this is what 'allowed' means, and what the LLM is shown."""
    return " ".join(PC_NAMES[(key[0] + x) % 12] for x in plan.MODES.get(key[1], plan.DEG_MAJOR))


def scale_pcs(key):
    """Pitch classes that belong to a key (tonic pitch class, 'major'|'minor')."""
    return plan.key_pcs(key[0], key[1])


def snap_to_key(notes, key, span=(0, 127), chord_at=None, scale_at=None):
    """Move every out-of-key note to the nearest note of the scale (the lower one on a tie), staying inside `span`
    (the instrument's playable range; always inside MIDI's 0..127). `chord_at(beat)`, when given, is the pitch classes of
    the chord sounding then: a note that belongs to that chord stays, even if the key lacks it. `scale_at(beat)`, when given,
    is the scale in force at that beat (bars with a mode of their own), in place of the key's."""
    pcs = scale_pcs(key)
    lo, hi = max(span[0], 0), min(span[1], 127)
    out = []
    for p, s_, d in notes:
        here = scale_at(s_) if scale_at else pcs
        if p % 12 not in here and not (chord_at and p % 12 in chord_at(s_)):
            near = [x for x in range(p - 2, p + 3) if lo <= x <= hi and x % 12 in here] or \
                   [x for x in range(p - 6, p + 7) if lo <= x <= hi and x % 12 in here]
            if near:
                p = min(near, key=lambda x: (abs(x - p), x))
        out.append((p, s_, d))
    return out


def limit_outside(notes, key, strict=10.0, span=(0, 127), chord_at=None, scale_at=None):
    """Bring a melody into its key, as strictly as asked. strict 10: every note outside the scale moves to the nearest
    scale note. Lower: that many fewer move, up to a quarter of the notes at 0, so chromatic passing notes survive. The
    ones that stay are the least harmful: short notes, off the beat, never the first or the last note."""
    snapped = snap_to_key(notes, key, span, chord_at, scale_at)
    allowed = round(len(notes) * (10 - max(0.0, min(10.0, strict))) / 10 * 0.25)
    if allowed <= 0:
        return snapped
    pcs = scale_pcs(key)
    outside = [i for i in range(1, len(notes) - 1) if notes[i][0] % 12 not in (scale_at(notes[i][1]) if scale_at else pcs)
               and not (chord_at and notes[i][0] % 12 in chord_at(notes[i][1]))]
    outside.sort(key=lambda i: notes[i][2] + (1.0 if abs(notes[i][1] % 1) < 1e-6 else 0.0))   # short, weak first
    keep = set(outside[:allowed])
    return [notes[i] if i in keep else snapped[i] for i in range(len(notes))]


def outside_share(notes, key):
    """The fraction of the notes that are not in the key's scale."""
    pcs = scale_pcs(key)
    return sum(1 for p, _, _ in notes if p % 12 not in pcs) / max(len(notes), 1)


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
            **({"harmony": [list(b) for b in theplan["harmony"]]} if theplan.get("harmony") else {}),
            **({"modes": list(theplan["modes"])} if theplan.get("modes") else {}),
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
