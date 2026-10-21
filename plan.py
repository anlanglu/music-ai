"""The musical plan behind a melody: the phrases it is made of, the harmony under them, where each phrase cadences,
and the one idea the melody keeps coming back to.

Every engine uses the same plan, so they all articulate the same kind of form:
  - the theory composer builds a melody straight from it (`composer.py`),
  - the language model is given the chords and asked to end each phrase on its cadence (`text2music.py`),
  - the Anticipation sampler is given the chord of every bar and a target contour (`sample.py`).

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
FORM_LABELS = ["Free", "Harmony", "Cadences", "Full"]

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


def form_knobs(form):
    """The knob values the Form slider sets."""
    base = dict(DEFAULT_KNOBS)
    base.update(FORM_WEIGHTS.get(max(0, min(3, int(form))), {}))
    return base


def shape_knobs(shape):
    """The knob values the Melodic shape picker sets."""
    base = dict(DEFAULT_KNOBS)
    bias = SHAPE_BIAS.get(max(0, min(3, int(shape))), None)
    if bias:
        base.update(bias)
    return base


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


def presets():
    """Everything the app needs to show the sliders: the defaults, and what each preset sets them to."""
    return {"defaults": dict(DEFAULT_KNOBS),
            "form": {str(f): form_knobs(f) for f in range(4)},
            "shape": {str(s): shape_knobs(s) for s in range(3)},
            "labels": {"form": list(FORM_LABELS),
                       "shape": ["Stepwise", "Balanced", "Angular"]}}


def form_label(form):
    """The name of a setting of the 'form' slider."""
    return FORM_LABELS[max(0, min(3, int(form)))]


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
