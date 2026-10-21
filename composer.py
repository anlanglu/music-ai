"""Theory composer: melodies written with phrase structure, harmony and motif development, no AI model.

How it works (the way a student of composition would):
  1. FORM    the bars are split into phrases (question / answer / contrast / final) with cadences
  2. HARMONY a chord for every bar, from progression templates that suit each phrase's role
  3. MOTIF   one bar of rhythm + melodic contour, tied to the opening chord
  4. DEVELOP each later bar restates the motif (repeat, sequence up/down, inversion, rhythmic variation) over its
             own chord, so the tune is recognisable yet never static; strong beats land on chord tones
  5. SHAPE   a climax in the last third, stepwise approach into each cadence, breaths between phrases
  6. ORNAMENT passing tones, neighbour tones and anticipations, more of them at higher complexity levels

Everything is in scale degrees until the very end, so the melody cannot leave its key. Returns notes as
(pitch, start_beat, dur_beats) in quarter-note beats, monophonic, on a sixteenth-note grid.
"""
import math

from plan import (  # the scales, the chord progressions and the phrase plan live in plan.py: every engine uses them
    CADENCE_TARGETS as _CADENCE_TARGETS,
    DEG_MAJOR as _MAJOR,
    DEG_MINOR as _MINOR,
    PROGRESSIONS as _PROGRESSIONS,
    chord_progression,
    phrase_plan as _phrase_plan,
)

# Rhythm cells that fill one beat (or two, for span 2). durations are in quarter notes; negative = rest.
# (name, durations, span_in_beats, weight at complexity 1..5)
_SIMPLE_CELLS = [
    ("Q", [1.0], 1, [6, 5, 4, 3, 2]),
    ("EE", [0.5, 0.5], 1, [3, 5, 6, 6, 6]),
    ("DS", [0.75, 0.25], 1, [0, 1, 2, 3, 3]),
    ("SSE", [0.25, 0.25, 0.5], 1, [0, 0, 1, 2, 3]),
    ("ESS", [0.5, 0.25, 0.25], 1, [0, 0, 1, 2, 3]),
    ("SSSS", [0.25] * 4, 1, [0, 0, 0, 1, 2]),
    ("RE", [-0.5, 0.5], 1, [0, 1, 2, 2, 2]),
    ("RQ", [-1.0], 1, [0, 0, 1, 2, 2]),
    ("H", [2.0], 2, [4, 3, 2, 1, 1]),
    ("DQE", [1.5, 0.5], 2, [0, 2, 4, 4, 3]),
    ("SYN", [0.5, 1.0, 0.5], 2, [0, 1, 3, 5, 5]),
]
_COMPOUND_CELLS = [  # 6/8, 12/8: the beat is a dotted quarter (1.5)
    ("D", [1.5], 1, [6, 5, 4, 3, 2]),
    ("EEE", [0.5, 0.5, 0.5], 1, [3, 5, 6, 6, 6]),
    ("QE", [1.0, 0.5], 1, [0, 3, 4, 4, 4]),
    ("EQ", [0.5, 1.0], 1, [0, 2, 4, 4, 4]),
    ("SSEE", [0.25, 0.25, 0.5, 0.5], 1, [0, 0, 1, 3, 4]),
    ("DD", [3.0], 2, [2, 2, 1, 1, 1]),
]
_MOODS_MINOR = ("sad", "melanchol", "dark", "tense", "mysterious", "somber", "sorrow", "lonely", "haunting", "ominous", "dramatic")
_MOODS_MAJOR = ("happy", "bright", "playful", "hopeful", "upbeat", "joy", "cheerful", "sunny", "triumph", "festive", "gentle")


def auto_key(prompt, rng):
    """A key for a prompt that doesn't name one: minor for dark moods, major for bright ones, else a coin flip."""
    p = (prompt or "").lower()
    minor = any(w in p for w in _MOODS_MINOR)
    major = any(w in p for w in _MOODS_MAJOR)
    mode = "minor" if minor and not major else "major" if major and not minor else rng.choice(["major", "minor"])
    return rng.choice([0, 2, 5, 7, 10, 4] if mode == "major" else [9, 2, 4, 7, 0, 6]), mode


def _weighted(rng, items, weights):
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
    r = rng.random() * total
    for item, w in zip(items, weights):
        r -= w
        if r <= 0:
            return item
    return items[-1]


def _rhythm(bb, compound, level, rng, tail=None, rest_boost=1.0):
    """One bar of rhythm as a list of durations (negative = rest). If `tail` is given, the bar ends on a single held
    note of that length (used for cadences)."""
    cells = _COMPOUND_CELLS if compound else _SIMPLE_CELLS
    beat = 1.5 if compound else 1.0
    body = bb - (tail or 0)
    out, pos = [], 0.0
    while pos < body - 1e-9:
        left = body - pos
        fit = [c for c in cells if c[2] * beat <= left + 1e-9 and (c[2] == 1 or pos % beat < 1e-9)]
        if pos == 0:   # a bar starts on a sounding note, never a rest
            fit = [c for c in fit if c[1][0] > 0] or fit
        if not fit:    # an odd leftover (e.g. the last half beat of 7/8)
            out.append(round(left, 4))
            break
        cell = _weighted(rng, fit, [(c[3][level - 1] + 0.05) * (rest_boost if min(c[1]) < 0 else 1.0) for c in fit])
        out += cell[1]
        pos += cell[2] * beat
    if tail:
        out.append(tail)
    return out


# Melodic gestures in scale-degree steps (a "2" is a third, so [2, 2, -2] outlines 1-3-5-3): the building blocks of
# motifs that are more than a bit of scale.
_GESTURES = [
    [2, 2, -2],            # arpeggio up and back
    [2, 2, 1, -3],         # arpeggio, a step on, then drop
    [4, -1, -1],           # leap up, step down
    [-3, 1, 1],            # drop, climb back by step
    [3, -1, 2, -2],        # leap, turn, third up, third down
    [1, -1, 2, -1],        # zigzag
    [0, 2, -1, 3],         # repeated note, then reaching up
    [5, -2, -1, -1],       # octave-ish leap and a descending answer
    [-2, -2, 4],           # fall by thirds, jump back up
    [1, 1, 3, -2],         # climb, skip, sink
]


def _contour(n, level, rng, shape=1):
    """n-1 melodic steps in scale degrees. shape 0: mostly stepwise (thirds, a few leaps answered by a step back).
    1 and 2 build it from gestures (arpeggio, leap-and-step-back, zigzag...) joined by short stepwise stretches, with
    fewer long scale runs; 2 uses wider and more frequent leaps."""
    k = max(0, n - 1)
    if shape >= 1 and k >= 2:
        steps = []
        while len(steps) < k:
            if rng.random() < (0.55 if shape == 1 else 0.8):
                g = list(rng.choice(_GESTURES))
                if rng.random() < 0.5:
                    g = [-x for x in g]            # the same gesture upside down
                steps += g
            else:
                steps += [rng.choice([1, -1])] * rng.choice([1, 1, 2])   # at most two steps the same way
        steps = steps[:k]
        # break any scale run of three or more the same way
        for i in range(2, len(steps)):
            if abs(steps[i]) == 1 and steps[i] == steps[i - 1] == steps[i - 2]:
                steps[i] = rng.choice([-steps[i], 2 * steps[i]]) if shape == 1 else rng.choice([-2 * steps[i], 2 * steps[i], -steps[i]])
        return steps
    leap = [0.02, 0.05, 0.09, 0.13, 0.17][level - 1]
    steps, prev = [], 0
    for _ in range(k):
        if abs(prev) >= 3 and rng.random() < 0.8:
            step = -1 if prev > 0 else 1                      # recover from a leap by stepping back
        else:
            r = rng.random()
            if r < leap:
                step = rng.choice([3, 4, 5, -3, -4, -5])
            elif r < leap + 0.22:
                step = rng.choice([2, -2])
            elif r < leap + 0.22 + (0.05 if level <= 2 else 0.02):
                step = 0
            else:
                step = rng.choice([1, -1])
                if prev and rng.random() < 0.55:
                    step = 1 if prev > 0 else -1               # keep going the same way
        steps.append(step)
        prev = step
    return steps


# The register of each bar within a four-bar phrase, in units of a third: the phrase rises, falls, arches or dips.
_ARCS = [(0, 1, 2, 1), (0, 1, 1, 2), (2, 1, 1, 0), (1, 0, 0, 1), (0, 2, 0, 2), (1, 2, 1, 0)]


def _chord_tones(root):
    return {root % 7, (root + 2) % 7, (root + 4) % 7}


def _fit(deg, root, avoid=None):
    """Nearest scale degree to `deg` that is a tone of the chord built on `root` (and, if given, not `avoid`, so a
    bar doesn't collapse into one repeated note)."""
    tones = _chord_tones(root)
    for off in (0, 1, -1, 2, -2, 3, -3):
        if (deg + off) % 7 in tones and deg + off != avoid:
            return deg + off
    return deg


def _phrase_plan(bars):
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


def breathe(notes, boundaries, phrasing=1):
    """Make ideas audibly separate. `boundaries` is a list of (time_in_beats, 'idea' | 'phrase'). Just before each one,
    leave a rest: the note sounding there is shortened and notes that would start inside the rest are dropped.
    phrasing 0 = flowing (no rests), 1 = balanced, 2 = spacious. Safe to apply twice."""
    idea_rest, phrase_rest = {0: (0.0, 0.0), 1: (0.5, 1.0), 2: (1.0, 1.5)}[phrasing]
    out = [list(n) for n in notes]
    for time, kind in boundaries:
        rest = phrase_rest if kind == "phrase" else idea_rest
        cut = time - rest
        if rest <= 0:
            continue
        kept_before = [n for n in out if time - 4.0 <= n[1] < cut - 1e-9]
        if len(kept_before) < 2:   # never hollow out a bar
            continue
        nxt = []
        for n in out:
            if cut - 1e-9 <= n[1] < time - 1e-9:
                continue
            if n[1] < cut - 1e-9 < n[1] + n[2]:
                n[2] = cut - n[1]
                if n[2] < 0.25 - 1e-9:
                    continue
            nxt.append(n)
        out = nxt
    return [tuple(n) for n in out]


def compose(bars, sig, key, level, rng, lo=55, hi=84, phrasing=1, shape=1, chords=None):
    """Write a melody. Returns (notes, info) with notes = [(pitch, start_beat, dur_beats)].
    `chords` (scale degrees, one per bar) may be given so the caller can share the harmony with the other engines."""
    tonic, mode = key
    scale = _MAJOR if mode == "major" else _MINOR
    num, den = (int(x) for x in sig.split("/"))
    bb = num * 4 / den
    compound = den == 8 and num % 3 == 0
    beat = 1.5 if compound else 1.0
    level = max(1, min(5, level))
    rest_boost = {0: 0.0, 1: 1.0, 2: 2.5}[phrasing]

    def pitch(deg, raised=False):
        octave, d = divmod(deg, 7)
        pc = 11 if (mode == "minor" and d == 6 and raised) else scale[d]
        return 60 + tonic + pc + 12 * octave

    center = min(range(-14, 29), key=lambda d: abs(pitch(d) - min(max((lo + hi) // 2, 62), 74)))

    plan = _phrase_plan(bars)
    # ---- harmony: one chord per bar
    if chords is None:
        chords = []
        for length, role in plan:
            chords += chord_progression(role, length, rng)
    chords = list(chords)
    chords[-1] = 0   # the piece ends on the tonic chord

    # ---- the motif: a rhythm and a contour
    motif_rhythm = _rhythm(bb, compound, level, rng, rest_boost=rest_boost)
    motif_contour = _contour(sum(1 for d in motif_rhythm if d > 0), level, rng, shape)
    motif_start = rng.choice([0, 2, 4])   # begin on the root, third or fifth of the chord

    def realize(rhythm, contour, start_deg, root, invert=False):
        degs, cur, i = [], start_deg, 0
        for dur in rhythm:
            if dur < 0:
                degs.append(None)
                continue
            if i > 0:
                step = contour[(i - 1) % len(contour)] if contour else 0
                cur += -step if invert else step
            degs.append(cur)
            i += 1
        # strong beats and long notes must be chord tones
        pos, prev = 0.0, None
        for k, dur in enumerate(rhythm):
            if degs[k] is not None:
                if pos % (2 * beat) < 1e-9 or abs(dur) >= beat:
                    degs[k] = _fit(degs[k], root, prev)
                elif degs[k] == prev and (k < 2 or degs[k - 2] == prev):   # no more than two of the same note in a row
                    degs[k] += rng.choice([1, -1])
                prev = degs[k]
            pos += abs(dur)
        return degs

    def rhythm_for(lv):
        return _rhythm(bb, compound, max(1, min(5, lv)), rng, rest_boost=rest_boost)

    bars_out, bar_ops = [], []     # per bar: (rhythm, degs), and what was done to it
    prev_last, prev_start = center, center
    pos_bar = 0
    for length, role in plan:
        # A phrase states an idea, develops it, restates it, and closes. A contrast phrase brings a new idea.
        if role == "cont" and pos_bar > 0:
            cur_rhythm = rhythm_for(level + rng.choice([-1, 0, 1]))
            cur_contour = _contour(sum(1 for d in cur_rhythm if d > 0), level, rng, shape)
            cur_off, fresh = rng.choice([0, 2, 4]), True
        else:
            cur_rhythm, cur_contour, cur_off, fresh = motif_rhythm, motif_contour, motif_start, False
        arc = rng.choice(_ARCS)
        arc_amount = (0, 2, 3)[shape]

        def arc_at(j):
            return arc[j % len(arc)] * arc_amount

        for j in range(length):
            root = chords[pos_bar]
            last_in_phrase = j == length - 1
            target = None
            if last_in_phrase:
                cands = [t + 7 * o for t in _CADENCE_TARGETS[role] for o in (-1, 0, 1)]
                target = min(cands, key=lambda t: abs(t - prev_last))
            drift = round((center - prev_last) * 0.2) + (arc_at(j) - arc_at(j - 1) if j > 0 else 0)
            if last_in_phrase and length > 1 or (bars == 1):
                if compound:
                    tail = 1.5                                   # a dotted quarter: one whole beat
                else:
                    tail = 2.0 if role in ("cons", "final") and bb >= 3 else 1.0
                    tail = min(tail, bb - 1.0) if bb > 1 else bb
                rhythm = _rhythm(bb, compound, level, rng, tail=tail, rest_boost=rest_boost)
                n_notes = sum(1 for d in rhythm if d > 0)
                approach = 1 if prev_last < target else -1
                degs_rev = [target]
                for _ in range(n_notes - 1):   # walk backwards from the target by step, so the cadence is approached
                    degs_rev.append(degs_rev[-1] - (approach if len(degs_rev) == 1 else rng.choice([approach, -approach, approach])))
                seq = list(reversed(degs_rev))
                degs, it = [], iter(seq)
                for dur in rhythm:
                    degs.append(None if dur < 0 else next(it))
                bars_out.append((rhythm, degs))
                bar_ops.append("cadence")
            else:
                if pos_bar == 0:
                    op = "motif"
                elif j == 0:
                    op = "new_idea" if fresh else "restate"
                elif j == 1 and fresh:
                    op = "restate_seq"                         # the new idea is said twice, a step higher the second time
                elif j % 2 == 0:
                    op = "restate"                             # the idea returns, unmistakably
                else:
                    op = _weighted(rng, ["seq_up", "seq_down", "invert", "vary", "new"],
                                   {1: [4, 4, 1, 2, 0], 2: [3, 3, 2, 3, 0], 3: [3, 3, 2, 4, 1],
                                    4: [2, 3, 3, 4, 2], 5: [2, 3, 3, 4, 3]}[level])
                bar_ops.append(op)
                anchor = center + cur_off - 2
                if op in ("motif", "new_idea", "restate"):
                    start = _fit(anchor + arc_at(j) + (rng.choice([0, 0, 0, 1, -1]) if op == "restate" else 0), root)
                    bars_out.append((cur_rhythm, realize(cur_rhythm, cur_contour, start, root)))
                elif op == "restate_seq":
                    bars_out.append((cur_rhythm, realize(cur_rhythm, cur_contour, _fit(prev_start + 1, root), root)))
                elif op == "invert":
                    bars_out.append((cur_rhythm, realize(cur_rhythm, cur_contour, _fit(prev_start + drift, root), root, invert=True)))
                elif op == "vary":     # same melodic shape, a different rhythm (and so a different density)
                    rhythm = rhythm_for(level + rng.choice([-1, 0, 0, 1]))
                    bars_out.append((rhythm, realize(rhythm, cur_contour or [1, -1], _fit(prev_start + drift, root), root)))
                elif op == "new":
                    rhythm = rhythm_for(level)
                    contour = _contour(sum(1 for d in rhythm if d > 0), level, rng, shape)
                    bars_out.append((rhythm, realize(rhythm, contour, _fit(prev_start + drift, root), root)))
                else:                  # seq_up / seq_down: the idea, a step higher or lower
                    step = 1 if op == "seq_up" else -1
                    bars_out.append((cur_rhythm, realize(cur_rhythm, cur_contour, _fit(prev_start + step + drift, root), root)))
            real = [d for d in bars_out[-1][1] if d is not None]
            prev_last, prev_start = real[-1], real[0]
            pos_bar += 1

    # ---- register: keep every bar near the centre of the tune so it never wanders off into extreme registers
    half = [6, 7, 8, 9, 10][level - 1]
    for b in range(len(bars_out)):
        rhythm, degs = bars_out[b]
        real = [d for d in degs if d is not None]
        if real:
            mean = sum(real) / len(real)
            move = 7 * round((center - mean) / 7) if abs(mean - center) > half * 0.6 else 0
            degs = [None if d is None else d + move for d in degs]
            degs = [None if d is None else (d if abs(d - center) <= half else
                                            d - (1 if d > center else -1) * 7 * math.ceil((abs(d - center) - half) / 7))
                    for d in degs]
            bars_out[b] = (rhythm, degs)

    # ---- shape: a climax in the last third of the piece, in a bar that is not a cadence
    if bars >= 4:
        cadence_bars = set(sum(([sum(l for l, _ in plan[: i + 1]) - 1] for i in range(len(plan))), []))
        c = next((b for b in range(int(bars * 0.62), bars) if b not in cadence_bars), None)
        if c is not None:
            others = [d for k, (_, ds) in enumerate(bars_out) if k != c for d in ds if d is not None]
            mine = [d for d in bars_out[c][1] if d is not None]
            if others and mine and max(mine) <= max(others):
                lift = min(max(others) - max(mine) + 2, 4, center + half + 2 - max(mine))
                lift = max(lift, 0)
                bars_out[c] = (bars_out[c][0], [None if d is None else _fit(d + lift, chords[c]) for d in bars_out[c][1]])

    # ---- lay the bars out as timed notes
    events = []   # [deg, start, dur, bar_index]
    for b, (rhythm, degs) in enumerate(bars_out):
        t = b * bb
        for dur, deg in zip(rhythm, degs):
            if deg is not None and dur > 0:
                events.append([deg, t, dur, b])
            t += abs(dur)

    # ---- ornament: passing tones, neighbour tones, anticipations, breaths
    p_pass = [0, 0.1, 0.3, 0.45, 0.55][level - 1] * {0: 1.0, 1: 0.8, 2: 0.5}[phrasing]
    plain = {"motif", "new_idea", "restate", "restate_seq", "cadence"}   # idea statements and cadences stay unornamented
    out = []
    for i, (deg, start, dur, b) in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        if nxt and bar_ops[b] not in plain and dur >= 1.0 and nxt[1] <= start + dur + 1e-9 and rng.random() < p_pass:
            gap = nxt[0] - deg
            if abs(gap) == 2:
                out += [[deg, start, dur - 0.5, b], [deg + gap // 2, start + dur - 0.5, 0.5, b]]
                continue
            if gap == 0:
                out += [[deg, start, dur - 0.5, b], [deg + 1, start + dur - 0.5, 0.5, b]]
                continue
        out.append([deg, start, dur, b])
    events = out
    if level >= 4:   # anticipation: sound the next bar's first note an eighth early (a syncopation across the barline)
        for i in range(len(events) - 2, -1, -1):   # backwards, so inserting never shifts what we haven't visited
            cur, nxt = events[i], events[i + 1]
            if (nxt[3] == cur[3] + 1 and bar_ops[nxt[3]] not in plain and abs(nxt[1] - (cur[1] + cur[2])) < 1e-9
                    and cur[2] >= 1.0 and rng.random() < 0.3):
                cur[2] -= 0.5
                events.insert(i + 1, [nxt[0], nxt[1] - 0.5, 0.5, cur[3]])
    # ---- to pitches, inside the instrument's range
    def pitches(shift):
        res = []
        for k, (deg, start, dur, b) in enumerate(events):
            nxt = events[k + 1][0] + shift if k + 1 < len(events) else None
            d = deg + shift
            raised = mode == "minor" and d % 7 == 6 and (chords[b] % 7 == 4 or (nxt is not None and nxt % 7 == 0 and nxt > d))
            res.append(pitch(d, raised))
        return res

    shift = 0
    ps = pitches(0)
    while max(ps) > hi and shift > -21:
        shift -= 7
        ps = pitches(shift)
    while min(ps) < lo and shift < 21 and max(pitches(shift + 7)) <= hi:
        shift += 7
        ps = pitches(shift)
    notes = [(max(lo, min(hi, p)), s, d) for p, (_, s, d, _) in zip(ps, events)]
    boundaries, start_bar = [], 0
    for k, (length, _) in enumerate(plan):
        if length >= 4:
            boundaries.append(((start_bar + 2) * bb, "idea"))          # between the two halves of a phrase
        if k < len(plan) - 1:
            boundaries.append(((start_bar + length) * bb, "phrase"))   # between phrases
        start_bar += length
    notes = breathe(notes, boundaries, phrasing)
    form = "".join(role[0].upper() for _, role in plan)
    return notes, {"form": form, "chords": chords, "plan": plan, "ops": bar_ops}
