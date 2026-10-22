"""The musical plan behind a melody: the phrases it is made of, the harmony under them, where each phrase cadences,
and the one idea the melody keeps coming back to.

Everything uses the same plan, so it all articulates the same kind of form:
  - the language model is given the chords and asked to end each phrase on its cadence (`text2music.py`),
  - the Anticipation sampler, when it expands a melody (`expand.py`), is given the chord of every bar and a target
    contour (`sample.py`),
  - the accompaniment is steered by the chords too.

A plan looks like this::

    {"key": (7, "major"), "bars": 8, "sig": "4/4", "bb": 4.0,
     "chords": [0, 3, 1, 4, 0, 3, 4, 0],                    # scale degree of the chord in each bar
     "phrases": [{"index": 0, "start_bar": 0, "bars": 4, "role": "ante",
                  "start_beat": 0.0, "end_beat": 16.0, "chords": [0, 3, 1, 4],
                  "cadence": [4, 1]}, ...],
     "cadences": [{"beat": 16.0, "role": "ante", "targets": [4, 1]}, ...],
     "boundaries": [(8.0, "idea"), (16.0, "phrase"), ...]}

Scale degrees are 0..6 (0 = tonic, 4 = dominant). A phrase's role says what it is for: "ante" asks a question and
ends on the dominant, "cons" answers it and comes home, "cont" goes somewhere else, "final" closes.
"""
import random

# The two scales, in one place. The seven-degree scales are used for chord arithmetic (which degree is the third
# of which chord); the key scales are the pitch classes a note is allowed to be in (minor adds the raised 7th).
DEG_MAJOR = [0, 2, 4, 5, 7, 9, 11]
DEG_MINOR = [0, 2, 3, 5, 7, 8, 10]
SCALE_MAJOR = [0, 2, 4, 5, 7, 9, 11]
SCALE_MINOR = [0, 2, 3, 5, 7, 8, 10, 11]     # natural minor plus the raised 7th (the leading tone)

# Chord progressions per phrase role, as scale degrees of the chord roots (0 = I / i ... 4 = V ...).
PROGRESSIONS = {
    "ante": [[0, 3, 1, 4], [0, 5, 3, 4], [0, 2, 3, 4], [0, 0, 3, 4]],     # a question: ends on the dominant
    "cons": [[0, 3, 4, 0], [0, 5, 4, 0], [0, 1, 4, 0], [0, 2, 3, 0]],     # the answer: ends home
    "cont": [[5, 3, 0, 4], [3, 4, 5, 2], [1, 4, 0, 5], [3, 0, 4, 5]],     # contrast: leaves home, ends away from it
    "final": [[0, 3, 4, 0], [0, 5, 1, 0], [0, 1, 4, 0], [3, 5, 4, 0]],
}
CADENCE_TARGETS = {"ante": [4, 1], "cons": [0, 2], "cont": [4, 2, 5], "final": [0]}   # scale degrees to end a phrase on


def deg_scale(mode):
    """The seven degrees of a mode, used for chord arithmetic."""
    return DEG_MAJOR if mode == "major" else DEG_MINOR


def key_scale(mode):
    """The pitch classes a note may be in, relative to the tonic."""
    return SCALE_MAJOR if mode == "major" else SCALE_MINOR


def key_pcs(tonic, mode):
    """The pitch classes of a key, as absolute MIDI pitch classes."""
    return {(tonic + x) % 12 for x in key_scale(mode)}


def chord_pcs(tonic, mode, degree):
    """The pitch classes of the triad on a scale degree. In a minor key the dominant is the major V (harmonic
    minor), so a cadence can land on it."""
    scale = deg_scale(mode)
    d = degree % 7
    root = scale[d]
    if mode == "minor" and d == 4:      # harmonic-minor dominant: a major V
        third, fifth = root + 4, root + 7
    else:
        third = scale[(d + 2) % 7] + (12 if (d + 2) >= 7 else 0)
        fifth = scale[(d + 4) % 7] + (12 if (d + 4) >= 7 else 0)
    return {(tonic + root) % 12, (tonic + third) % 12, (tonic + fifth) % 12}


def degree_pcs(tonic, mode, degree):
    """The pitch class of a scale degree."""
    return (tonic + deg_scale(mode)[degree % 7]) % 12


_NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def chord_name(tonic, mode, degree):
    """'G major', 'B minor', ... - the name of the chord on a scale degree."""
    scale = deg_scale(mode)
    d = degree % 7
    root = (tonic + scale[d]) % 12
    if mode == "minor" and d == 4:      # harmonic-minor dominant: a major V
        third = scale[d] + 4
    else:
        third = scale[(d + 2) % 7] + (12 if (d + 2) >= 7 else 0)
    quality = "minor" if (third - scale[d]) % 12 == 3 else "major"
    return f"{_NOTE_NAMES[root]} {quality}"


def chord_triple(tonic, mode, degree):
    """(root pitch class, third offset, fifth offset) - the shape a chord is voiced from."""
    scale = deg_scale(mode)
    d = degree % 7
    root = scale[d]
    if mode == "minor" and d == 4:      # harmonic-minor dominant: a major V
        third, fifth = root + 4, root + 7
    else:
        third = scale[(d + 2) % 7] + (12 if (d + 2) >= 7 else 0)
        fifth = scale[(d + 4) % 7] + (12 if (d + 4) >= 7 else 0)
    return (tonic + root) % 12, third - root, fifth - root


def step_semitones(mode, steps):
    """How many semitones `steps` scale degrees covers (steps may be negative)."""
    scale = deg_scale(mode)
    if steps >= 0:
        octaves, rest = divmod(steps, 7)
        return octaves * 12 + (scale[rest] - scale[0])
    octaves, rest = divmod(-steps, 7)
    if rest == 0:
        return -12 * octaves
    return -12 * octaves + (scale[(7 - rest) % 7] - 12)


def transpose_motif(motif, mode, steps):
    """Move a motif [(pitch, start, dur)] up or down by `steps` scale degrees, keeping its intervals exactly."""
    if not steps:
        return [(p, s, d) for p, s, d in motif]
    n = step_semitones(mode, steps)
    return [(p + n, s, d) for p, s, d in motif]


def fit_motif_to_chord(motif, tonic, mode, pcs, prefer=0):
    """The transposition of a motif (by scale degrees) that best fits a chord: as many of its notes inside the
    chord's pitch classes as possible, preferring a shift near `prefer`."""
    if not motif:
        return []
    best = None
    for steps in range(-3, 4):
        shifted = transpose_motif(motif, mode, steps)
        hits = sum(1 for p, _, _ in shifted if p % 12 in pcs)
        score = (hits, -abs(steps - prefer))
        if best is None or score > best[0]:
            best = (score, steps)
    return transpose_motif(motif, mode, best[1])


def phrase_plan(bars):
    """How the bars are grouped into phrases, and what each phrase is for: [(bars, role), ...]."""
    lens = [4] * (bars // 4)
    rem = bars % 4
    if not lens:
        lens = [bars]
    elif rem >= 2:
        lens.append(rem)
    elif rem:
        lens[-1] += rem
    n = len(lens)
    roles = {1: ["final"], 2: ["ante", "cons"], 3: ["ante", "cont", "cons"], 4: ["ante", "cons", "cont", "final"]}.get(n)
    if roles is None:
        roles = []
        while len(roles) < n - 1:
            roles += ["ante", "cons", "cont"][: n - 1 - len(roles)]
        roles.append("final")
    return list(zip(lens, roles))


def chord_progression(role, length, rng):
    """The chord degrees of one phrase of `length` bars."""
    prog = list(rng.choice(PROGRESSIONS[role]))
    if length != 4:
        prog = (prog[: length - 1] + [prog[-1]]) if length > 1 else [prog[-1]]
    return prog[:length]


def build_plan(bars, sig, key, rng=None, level=3):
    """The phrases, harmony and cadences of a piece. `key` is (tonic pitch class, 'major'|'minor').
    `rng` is anything with a .choice method; the global random module is used by default, so a seeded run is
    repeatable."""
    rng = random if rng is None else rng
    num, den = (int(x) for x in sig.split("/"))
    bb = num * 4 / den
    groups = phrase_plan(bars)
    chords, phrases, cadences, boundaries = [], [], [], []
    bar = 0
    for i, (length, role) in enumerate(groups):
        prog = chord_progression(role, length, rng)
        chords += prog
        end_beat = (bar + length) * bb
        phrases.append({
            "index": i, "start_bar": bar, "bars": length, "role": role,
            "start_beat": bar * bb, "end_beat": end_beat, "chords": list(prog),
            "cadence": list(CADENCE_TARGETS[role]),
        })
        cadences.append({"beat": end_beat, "role": role, "targets": list(CADENCE_TARGETS[role]),
                         "bar": bar + length - 1, "phrase": i})
        if length >= 4:
            boundaries.append(((bar + 2) * bb, "idea"))
        if i < len(groups) - 1:
            boundaries.append((end_beat, "phrase"))
        bar += length
    chords[-1] = 0                       # the piece ends on the tonic chord
    cadences[-1]["targets"] = [0]        # ... and on the tonic
    return {
        "key": tuple(key), "bars": bars, "sig": sig, "bb": bb, "level": level,
        "chords": chords, "phrases": phrases, "cadences": cadences, "boundaries": boundaries,
    }


def plan_chord_pcs(plan):
    """The pitch classes of each bar's chord, as a list parallel to plan['chords']."""
    tonic, mode = plan["key"]
    return [chord_pcs(tonic, mode, deg) for deg in plan["chords"]]


def plan_regions(plan, tempo, start_tick=0):
    """The plan as time-varying pitch-class regions for the sampler: [(start_tick, end_tick, pitch classes), ...].
    The sampler's clock runs at 100 ticks a second, so a beat is 60/tempo seconds."""
    per_beat = 60.0 / tempo * 100.0
    regions = []
    for i, pcs in enumerate(plan_chord_pcs(plan)):
        start = start_tick + round(i * plan["bb"] * per_beat)
        end = start_tick + round((i + 1) * plan["bb"] * per_beat)
        regions.append((start, end, pcs))
    return regions


# How much of the plan a melody follows, from free (0) to the whole plan (3). The weights are in logit units: the
# model's own preferences are worth a few, so these are a firm but not absolute hand.

# The knobs the app can turn. Each is a number the user can move; the shape and form presets below are just
# starting points for them. The defaults are what the app used before the knobs existed.
DEFAULT_KNOBS = {
    # the plan
    "chord": 2.2,        # how strongly the melody prefers the chord of the bar it is in
    "cadence": 5.0,      # how strongly it is pulled towards the note a phrase cadences on
    "motif": 8.0,        # how strongly it is pulled towards the opening idea at each phrase's start
    "sigma": 1.0,        # how wide that pull is, in semitones (narrow = the idea comes back more exactly)
    "tol": 1.0,          # how near a note has to land to be pulled, in beats
    # the melodic shape: how much the line avoids runs, repeats and shuffling
    "run": None,
    "leap": None,
    "repeat": None,
    "variety": None,
    # the metrical grid
    "beat": 0.5,         # how strongly onsets prefer the beat (and the barline twice as much)
}

# The plan's presets: what the Form slider sets the knobs to.
FORM_WEIGHTS = {
    0: {},
    1: {"chord": 2.2},
    2: {"chord": 2.2, "cadence": 5.0},
    3: {"chord": 2.2, "cadence": 5.0, "motif": 8.0},
}

# The melodic shape's presets: what the Melodic shape picker sets the knobs to. 0 means "no bias at all".
SHAPE_BIAS = {0: None,
              1: {"run": 1.5, "leap": 0.5, "repeat": 0.7, "variety": 0.7},
              2: {"run": 2.5, "leap": 1.0, "repeat": 1.2, "variety": 1.2}}


def knobs(over=None):
    """The knobs to use: the defaults, with whatever the caller asked to change on top."""
    out = dict(DEFAULT_KNOBS)
    for k, v in (over or {}).items():
        if k in out and v is not None:
            out[k] = v
    return out


def form_weights(form, k=None):
    """What a melody is told to do at each setting of the 'form' slider:
    0 free (no plan at all), 1 the harmony of every bar, 2 plus a cadence at the end of every phrase,
    3 plus the motif coming back at the start of every phrase.
    The knobs decide *how strongly* each of those is applied; they cannot switch one on that the setting does not
    use, so 'Free' really is free."""
    k = k or {}
    base = dict(FORM_WEIGHTS.get(max(0, min(3, int(form))), {}))
    for name in list(base):
        if k.get(name) is not None:
            base[name] = k[name]
    return base


def shape_weights(shape, k=None):
    """The melodic bias the sampler applies: the shape's preset, with any of its numbers overridden."""
    k = k or {}
    bias = dict(SHAPE_BIAS.get(max(0, min(3, int(shape))), None) or {})
    for name in ("run", "leap", "repeat", "variety"):
        if name in k and k[name] is not None:
            bias[name] = k[name]
    return bias or None


def shape_amount_knobs(amount):
    """The shape numbers for a point on the Stepwise (0) .. Balanced (1) .. Angular (2) line: the presets, blended.
    This is what the single 'Melodic shape' slider sends."""
    x = max(0.0, min(2.0, float(amount)))
    lo, hi, t = (None, SHAPE_BIAS[1], x) if x <= 1 else (SHAPE_BIAS[1], SHAPE_BIAS[2], x - 1)
    return {name: round((lo[name] if lo else 0.0) * (1 - t) + hi[name] * t, 3) for name in hi}


# What the app's sliders (and the command line's) send: all run 0..10, and this is the one place that turns them into
# the numbers the sampler uses. The defaults are what the app did before they were sliders.
SLIDER_DEFAULTS = {"shape": 5.0, "pulse": 1.67, "harmony": 2.75, "endings": 3.33, "theme": 0.0, "runs": 5.0}


def clean_sliders(sliders=None):
    """The six slider positions, each clamped to 0..10; missing or None ones take their defaults. A slider sent as "auto"
    (or listed under the "auto" key, which is how a cleaned set remembers it) keeps its default number for the
    sampler, and is named in s["auto"] so that the LLM is not given an instruction for it."""
    s = dict(SLIDER_DEFAULTS)
    given = sliders or {}
    auto = set(given.get("auto") or ())
    for name, v in given.items():
        if name in s:
            if isinstance(v, str) and v.lower() == "auto":
                auto.add(name)
            elif v is not None:
                s[name] = max(0.0, min(10.0, float(v)))
    s["auto"] = sorted(auto & set(SLIDER_DEFAULTS))
    return s


def from_sliders(sliders=None):
    """The slider positions (0..10; any that are missing or None take their defaults) -> {shape, form, knobs}:
    the melodic shape level (0..2), how much of the plan is in use, and the sampler's strengths."""
    s = clean_sliders(sliders)
    knobs_ = {**shape_amount_knobs(s["shape"] / 5), "chord": s["harmony"] * 0.8, "cadence": s["endings"] * 1.5,
              "motif": s["theme"] * 2.0, "beat": s["pulse"] * 0.3,
              "run": round(s["runs"] * 0.3, 3)}     # penalty on one-way scale runs; 5 = what the shape slider gave
    return {"shape": int(round(s["shape"] / 5)), "form": form_from_knobs(knobs_), "knobs": knobs_}


def form_from_knobs(k):
    """How much of the plan is in use, read from the strengths: the idea coming back needs the whole plan (3), a
    cadence needs the phrases (2), a chord preference needs the harmony (1); all three at zero is a free melody."""
    k = knobs(k)
    return 3 if k["motif"] > 0 else 2 if k["cadence"] > 0 else 1 if k["chord"] > 0 else 0


def cadence_pitch(plan, phrase_index, near):
    """The pitch a phrase should cadence on: the scale degree the phrase ends on, in the octave nearest `near`."""
    tonic, mode = plan["key"]
    ph = plan["phrases"][phrase_index]
    degrees = ph["cadence"] or [0]
    best = None
    for deg in degrees:
        pc = degree_pcs(tonic, mode, deg)
        for oct_ in range(-1, 9):
            p = pc + 12 * oct_
            d = abs(p - near)
            if best is None or d < best[0]:
                best = (d, p)
    return best[1]


def motif_targets(motif, plan, tempo, phrase_index, start_tick=0):
    """The target pitches that make a phrase state the motif: the motif transposed onto that phrase's first chord,
    placed at the phrase's start. Returns [(tick, pitch), ...]."""
    ph = plan["phrases"][phrase_index]
    if not motif:
        return []
    tonic, mode = plan["key"]
    pcs = chord_pcs(tonic, mode, ph["chords"][0])
    shifted = fit_motif_to_chord(motif, tonic, mode, pcs, prefer=ph["chords"][0])
    per_beat = 60.0 / tempo * 100.0
    return [(start_tick + round((ph["start_beat"] + s) * per_beat), p) for p, s, _ in shifted]


def cadence_targets(plan, near_pitch, tempo, start_tick=0):
    """The target pitches at the ends of the phrases: each phrase lands on its cadence degree."""
    per_beat = 60.0 / tempo * 100.0
    out = []
    for i, cad in enumerate(plan["cadences"]):
        p = cadence_pitch(plan, i, near_pitch)
        out.append((start_tick + round(cad["beat"] * per_beat), p))
    return out


def resolve_phrase_endings(notes, plan, key, span=(0, 127)):
    """Make every phrase end on its cadence: the last note before each phrase's end moves to the scale degree the
    phrase cadences on, in the octave nearest the note before it. The final note is left to the caller (the piece
    ends on the tonic)."""
    if not notes:
        return notes
    tonic, mode = key
    lo, hi = max(span[0], 0), min(span[1], 127)
    out = list(notes)
    for cad in plan["cadences"][:-1]:          # the last phrase ends on the tonic, which resolve_ending handles
        beat = cad["beat"]
        idx = None
        for i in range(len(out) - 1, -1, -1):
            if out[i][1] < beat - 1e-9:
                idx = i
                break
        if idx is None or idx == 0:
            continue
        anchor = out[idx - 1][0]
        best = None
        for deg in cad["targets"]:
            pc = degree_pcs(tonic, mode, deg)
            for oct_ in range(-1, 9):
                p = pc + 12 * oct_
                if not (lo <= p <= hi):
                    continue
                d = abs(p - anchor)
                if best is None or d < best[0]:
                    best = (d, p)
        if best is not None:
            out[idx] = (best[1], out[idx][1], out[idx][2])
    return out


def infer_chords(notes, key, sig, bars):
    """The chord (scale degree) of each bar of a melody that was written without a plan: the triad that shares the most
    sounding time with that bar's notes. Ties go to the tonic, then the dominant and subdominant."""
    tonic, mode = key
    bb = _beats_per_bar(sig)
    prefer = {0: 0.3, 4: 0.2, 3: 0.1}
    out = []
    for b in range(bars):
        lo, hi = b * bb, (b + 1) * bb
        weight = {}
        for p, st, d in notes:
            overlap = min(st + d, hi) - max(st, lo)
            if overlap > 0:
                weight[p % 12] = weight.get(p % 12, 0.0) + overlap
        best = max(range(7), key=lambda deg: (sum(weight.get(pc, 0.0) for pc in chord_pcs(tonic, mode, deg))
                                              + prefer.get(deg, 0.0)))
        out.append(best)
    return out


def _beats_per_bar(sig):
    num, den = (int(x) for x in sig.split("/"))
    return num * 4 / den


def extend(base, key, sig, old_bars, new_bars, old_notes=None, rng=None, level=3):
    """Grow a plan by `new_bars` bars. Returns (whole, section): the plan for the old and new bars together, and the
    plan of the new bars on their own (starting at bar 0, which is how the melody generators want it).
    `base` is the plan the melody had (it is cut back to `old_bars` first, in case it was written for a longer piece);
    with no plan, the old bars get the chords their notes suggest."""
    section = build_plan(new_bars, sig, key, rng, level)
    bb = _beats_per_bar(sig)
    if base:
        keep = [dict(p) for p in base["phrases"] if p["start_bar"] + p["bars"] <= old_bars]
        chords = list(base["chords"][:old_bars])
    else:
        keep, chords = [], infer_chords(old_notes or [], key, sig, old_bars)
        bar = 0
        for i, (length, role) in enumerate(phrase_plan(old_bars)):
            keep.append({"index": i, "start_bar": bar, "bars": length, "role": role, "start_beat": bar * bb,
                         "end_beat": (bar + length) * bb, "chords": chords[bar:bar + length],
                         "cadence": list(CADENCE_TARGETS[role])})
            bar += length
    old_bars_used = sum(p["bars"] for p in keep)
    n = len(keep)
    phrases, cadences, boundaries = list(keep), [], []
    for p in keep:
        cadences.append({"beat": p["end_beat"], "role": p["role"], "targets": list(p["cadence"]),
                         "bar": p["start_bar"] + p["bars"] - 1, "phrase": p["index"]})
        if p["bars"] >= 4:
            boundaries.append(((p["start_bar"] + 2) * bb, "idea"))
        boundaries.append((p["end_beat"], "phrase"))
    for p in section["phrases"]:
        q = dict(p, index=p["index"] + n, start_bar=p["start_bar"] + old_bars_used,
                 start_beat=p["start_beat"] + old_bars_used * bb, end_beat=p["end_beat"] + old_bars_used * bb)
        phrases.append(q)
    for c in section["cadences"]:
        cadences.append(dict(c, beat=c["beat"] + old_bars_used * bb, bar=c["bar"] + old_bars_used, phrase=c["phrase"] + n))
    for beat, kind in section["boundaries"]:
        boundaries.append((beat + old_bars_used * bb, kind))
    whole = {"key": tuple(key), "bars": old_bars_used + new_bars, "sig": sig, "bb": bb, "level": level,
             "chords": chords[:old_bars_used] + section["chords"], "phrases": phrases, "cadences": cadences,
             "boundaries": boundaries}
    return whole, section


# Words that suggest a mode, for when a request names no key and the language model gives none.
_MOODS_MINOR = ("sad", "melanchol", "dark", "tense", "mysterious", "somber", "sorrow", "lonely", "haunting", "ominous",
                "dramatic", "war", "epic")
_MOODS_MAJOR = ("happy", "bright", "playful", "hopeful", "upbeat", "joy", "cheerful", "sunny", "triumph", "festive", "gentle")


def mode_for(prompt):
    """'minor' or 'major' from the mood words in a request (major when there are none, or both kinds)."""
    p = (prompt or "").lower()
    minor, major = any(w in p for w in _MOODS_MINOR), any(w in p for w in _MOODS_MAJOR)
    return "minor" if minor and not major else "major"
