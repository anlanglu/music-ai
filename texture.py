"""Piano texture: chords and arpeggios under an existing melody, by music theory rather than by model.

The piece's notes are left exactly as they are. We (1) find the key, (2) choose a diatonic chord for each short
segment of the piece so that the melody's notes mostly belong to it, and (3) play those chords as a piano
accompaniment pattern (block chords, arpeggios, Alberti bass, oom-pah, sustained). The accompaniment stays in the
left-hand register and below the melody, with at most four notes at once, so it is always playable.
"""
import math

import mido

from playable import fold_pitch, limits
from plan import DEG_MAJOR as _MAJOR, DEG_MINOR as _MINOR, deg_scale, key_pcs, mode_family
from plan import chord_triple

STYLES = ["block", "arpeggio", "alberti", "oompah", "sustained", "bassline", "walking", "pulse", "double"]
STYLE_LABELS = {
    "block": "Block chords", "arpeggio": "Arpeggios", "alberti": "Alberti bass",
    "oompah": "Bass + chords", "sustained": "Sustained chords", "bassline": "Bass line", "walking": "Walking bass",
    "pulse": "Pulsing chords", "double": "Melody doubling",
}
MIX = ["block", "arpeggio", "alberti", "oompah", "sustained"]   # what "mix" cycles through
LEFT_HAND = set(range(0, 9)) | set(range(16, 21))                # keyboards: tuck under the melody like a left hand


def piece_notes(path):
    """Every pitched note of a MIDI file as (pitch, start_beat, dur_beats, program); drums are skipped. Beats are
    quarter notes (ticks / ticks-per-beat), so tempo doesn't matter here."""
    mid = mido.MidiFile(str(path))
    tpb = mid.ticks_per_beat
    notes = []
    for trk in mid.tracks:
        t, open_, prog = 0, {}, 0
        for m in trk:
            t += m.time
            if m.type == "program_change":
                prog = m.program
            elif m.type == "note_on" and m.velocity > 0 and m.channel != 9:
                open_.setdefault((m.channel, m.note), []).append(t)
            elif m.type in ("note_off", "note_on") and open_.get((m.channel, m.note)):
                start = open_[(m.channel, m.note)].pop(0)
                notes.append((m.note, start / tpb, (t - start) / tpb, prog))
    return notes


def _chords(tonic, mode):
    """Diatonic triads of the key: (root pitch class, third, fifth, prior weight). Minor also allows a major V."""
    scale = deg_scale(mode)
    triads = []
    for d in range(7):
        root = scale[d]
        third = scale[(d + 2) % 7] + (12 if (d + 2) >= 7 else 0)
        fifth = scale[(d + 4) % 7] + (12 if (d + 4) >= 7 else 0)
        triads.append((root, third - root, fifth - root, d))
    prior = {"major": {0: 1.0, 4: 0.8, 3: 0.7, 5: 0.6, 1: 0.4, 2: 0.3, 6: 0.1},
             "minor": {0: 1.0, 4: 0.6, 3: 0.7, 5: 0.6, 2: 0.5, 6: 0.4, 1: 0.1}}[mode_family(mode)]
    out = [((tonic + r) % 12, t, f, prior[d], d) for r, t, f, d in triads]
    if mode == "minor":  # harmonic-minor dominant: major triad on the 5th degree
        out.append(((tonic + 7) % 12, 4, 7, 0.8, 4))
    return out


def choose_chords(notes, key, seg_beats, total_beats, plan_chords=None, bar_beats=4.0):
    """One chord per segment: melody fit + a prior for common chords + a preference for good root motion, solved
    with a small Viterbi search; the last segment is forced to the tonic and the one before leans to the dominant.
    `plan_chords` (scale degrees, one per bar) overrides the search, so the accompaniment can follow the melody's
    own harmony."""
    tonic, mode = key
    if plan_chords:
        n_seg = max(1, math.ceil(total_beats / seg_beats - 1e-9))
        out = []
        for k in range(n_seg):
            bar = int((k * seg_beats) // bar_beats) if bar_beats else 0
            out.append(chord_triple(tonic, mode, plan_chords[min(bar, len(plan_chords) - 1)]))
        return out
    chords = _chords(tonic, mode)
    scale_pcs = {(tonic + s) % 12 for s in deg_scale(mode)}
    n_seg = max(1, math.ceil(total_beats / seg_beats - 1e-9))
    emit = []
    for k in range(n_seg):
        lo, hi = k * seg_beats, (k + 1) * seg_beats
        weights = {}
        for pitch, start, dur, _ in notes:
            ov = min(hi, start + dur) - max(lo, start)
            if ov > 0:
                weights[pitch % 12] = weights.get(pitch % 12, 0) + ov
        total = sum(weights.values()) or 1.0
        row = []
        for root, third, fifth, prior, degree in chords:
            tones = {root, (root + third) % 12, (root + fifth) % 12}
            fit = sum(w * (1.0 if pc in tones else 0.15 if pc in scale_pcs else -0.6) for pc, w in weights.items())
            bonus = prior * 0.3
            if k == 0 and degree == 0:
                bonus += 0.5
            if k == n_seg - 1:
                bonus += 2.0 if degree == 0 and (root, third) == chords[0][:2] else -2.0
            elif k == n_seg - 2 and n_seg > 2:
                bonus += 0.5 if degree == 4 else 0.3 if degree == 3 else 0.0
            row.append(fit / total + bonus)
        emit.append(row)

    def move(a, b):  # reward for the harmonic motion from chord a to chord b
        diff = (chords[b][0] - chords[a][0]) % 12
        return 0.15 if a == b else {5: 0.25, 9: 0.15, 2: 0.1, 7: 0.1}.get(diff, 0.0)

    score = [emit[0][:]]
    back = []
    for k in range(1, n_seg):
        row, ptr = [], []
        for b in range(len(chords)):
            best = max(range(len(chords)), key=lambda a: score[-1][a] + move(a, b))
            row.append(score[-1][best] + move(best, b) + emit[k][b])
            ptr.append(best)
        score.append(row)
        back.append(ptr)
    path = [max(range(len(chords)), key=lambda i: score[-1][i])]
    for ptr in reversed(back):
        path.append(ptr[path[-1]])
    return [chords[i][:3] for i in reversed(path)]


def _voicing(chord, variant, ceiling):
    """(bass, [three chord tones, ascending]) for a chord, placed so the whole voicing sits at or below `ceiling`.
    `variant` picks the shape: close, first inversion, open, second inversion."""
    root, third, fifth = chord
    t, f = third % 12 or 12, fifth % 12 or 12
    shape = [[0, t, f], [t, f, 12], [0, f, t + 12], [f, 12, t + 12]][variant % 4]
    pc = root % 12
    base = ceiling - max(shape)
    base -= (base - pc) % 12          # highest pitch with the root's pitch class that still fits under the ceiling
    while base + min(shape) < 36 and base + 12 + max(shape) <= ceiling:  # lift out of the mud, but never into the melody
        base += 12
    voices = [base + x for x in shape]
    bass = voices[0] - 3
    bass -= (bass - pc) % 12          # the root, as low as is comfortable under the chord
    while bass < 28:
        bass += 12
    return bass, voices


def melody_program(notes, exclude=None):
    """The instrument that carries the tune: the one with the highest average pitch."""
    by = {}
    for p, _, _, prog in notes:
        if prog != exclude:
            by.setdefault(prog, []).append(p)
    return max(by, key=lambda k: sum(by[k]) / len(by[k])) if by else None


def _double(notes, key, program, d, variant):
    """Play the melody itself at an interval (an octave below, a sixth below, ...), kept in the key and in the
    instrument's range, one note at a time."""
    mel = melody_program(notes, exclude=program)
    if mel is None:
        return []
    interval = d.get("double_interval") or -12
    alts = [interval, -12 if interval != -12 else 12, 12 if interval != 12 else -24, -24 if interval != -24 else 24]
    shift = alts[variant % len(alts)]
    tonic, mode = key
    pcs = key_pcs(tonic, mode)
    out = []
    for p, st, du, prog in sorted((n for n in notes if n[3] == mel), key=lambda n: n[1]):
        q = p + shift
        if q % 12 not in pcs:
            q = min((x for x in range(q - 2, q + 3) if x % 12 in pcs), key=lambda x: (abs(x - q), x))
        q = fold_pitch(q, program)
        if out and st < out[-1][1] + out[-1][2]:       # monophonic: end the previous note where this one begins
            out[-1] = (out[-1][0], out[-1][1], max(0.25, st - out[-1][1]))
        out.append((q, st, du))
    return out


def build(notes, tempo, sig, key, style, variant=0, program=0, d=None, chords=None):
    """The accompaniment as [(pitch, start_beat, dur_beats)] for one instrument.

    Keyboards play it as a left hand tucked under the melody; any other instrument gets a window inside its own
    playable range and never more simultaneous notes than it can sound. `d` is a parsed direction (see
    direction.py): density (step size), register (high / low part of the range), length ('short' = staccato).
    `chords` (scale degrees, one per bar) makes the part follow the melody's own harmony."""
    d = d or {}
    if style == "double":
        return _double(notes, key, program, d, variant)
    num, den = (int(x) for x in sig.split("/"))
    bar = num * 4 / den
    compound = den == 8 and num % 3 == 0
    seg = bar / 2 if compound else (2.0 if bar >= 4 else bar)
    beat = 1.5 if compound else 1.0
    base = 0.5 if tempo <= 130 else 1.0
    unit = max(0.25, base * {-2: 4, -1: 2, 0: 1, 1: 0.5, 2: 0.5}[d.get("density", 0)])    # arpeggio / Alberti / pulse step
    cut = {-2: 0.35, -1: 0.5}.get(d.get("length", 0), 1.0)                              # staccato shortens note lengths
    lo, hi, poly = limits(program)
    left_hand = program in LEFT_HAND
    width = 19                                                                          # widest voicing we use
    frac = min(1.0, max(0.0, 0.35 + 0.22 * d.get("register", 0)))
    wlo = int(lo + max(0, hi - lo - width) * frac)
    total = max((st + du for _, st, du, _ in notes), default=bar)
    total = math.ceil(total / bar - 1e-9) * bar
    chords = choose_chords(notes, key, seg, total, plan_chords=chords, bar_beats=bar)
    out = []
    for k, chord in enumerate(chords):
        s0, s1 = k * seg, min((k + 1) * seg, total)
        if left_hand:
            floor = min((p for p, st, du, _ in notes if st < s1 and st + du > s0), default=None)
            ceiling = min(64, floor - 2) if floor is not None else 62   # keep the left hand under the melody
        else:
            ceiling = min(hi, wlo + width)
        bass, voices = _voicing(chord, variant, ceiling)
        if not left_hand:  # stay inside the instrument's own range
            while min(voices) < lo and max(voices) + 12 <= hi:
                voices = [v + 12 for v in voices]
            while max(voices) > hi and min(voices) - 12 >= lo:
                voices = [v - 12 for v in voices]
            while bass < lo:
                bass += 12
        bass = max(bass, 28) if left_hand else bass
        a, b, c = voices
        stack = ([bass] + voices)[:max(1, poly)]     # lowest first; never more notes than the instrument can sound
        span = s1 - s0
        if style == "sustained":
            out += [(p, s0, span) for p in stack]
        elif style in ("block", "pulse"):
            step = beat if style == "block" else unit
            x = s0
            while x < s1 - 1e-9:
                out += [(p, x, min(step * cut, s1 - x)) for p in stack]
                x += step
        elif style == "oompah":
            x = s0
            while x < s1 - 1e-9:
                notes_now = [stack[0]] if x == s0 else (stack[1:] or [a])
                out += [(p, x, min(beat * cut, s1 - x)) for p in notes_now]
                x += beat
        elif style == "bassline":
            x, i = s0, 0
            while x < s1 - 1e-9:
                out.append((bass if i % 2 == 0 else min(bass + 7, hi), x, min(beat * cut, s1 - x)))
                x += beat
                i += 1
        elif style == "walking":
            nxt = _voicing(chords[k + 1], variant, ceiling)[0] if k + 1 < len(chords) else bass
            steps = int(round(span / beat)) or 1
            third, fifth = chord[1] % 12 or 12, chord[2] % 12 or 12
            walk = [bass, bass + third, bass + fifth, bass + third]
            for i in range(steps):
                approach = nxt - 1 if nxt - 1 >= lo else nxt + 1   # a half step into the next chord's bass note
                pitch = approach if (i == steps - 1 and k + 1 < len(chords)) else walk[i % 4]
                out.append((pitch, s0 + i * beat, min(beat * cut, s1 - (s0 + i * beat))))
        else:
            cycle = [bass, a, b, c, b, a] if style == "arpeggio" else [bass, c, b, c]
            x, i = s0, 0
            while x < s1 - 1e-9:
                out.append((cycle[i % len(cycle)], x, min(unit * cut, s1 - x)))
                x += unit
                i += 1
    return [(fold_pitch(p, program), st, du) for p, st, du in out]


def style_order(sig):
    num, den = (int(x) for x in sig.split("/"))
    if den == 8 and num % 3 == 0:
        return ["arpeggio", "block", "sustained", "oompah", "alberti"]
    if num == 3:
        return ["oompah", "arpeggio", "block", "alberti", "sustained"]
    return ["arpeggio", "alberti", "block", "oompah", "sustained"]


def option(notes, tempo, sig, key, style, k, program=0, d=None, chords=None):
    """Option number k (1-based). style='mix' cycles through the styles; otherwise the same style gets a different
    voicing for each option. Returns (label, [(pitch, start_beat, dur_beats)])."""
    if style == "mix":
        order = style_order(sig)
        chosen, variant = order[(k - 1) % len(order)], (k - 1) // len(order)
    else:
        chosen, variant = style, k - 1
    if chosen == "double":
        ivs = [(d or {}).get("double_interval") or -12, -12, 12, -24]
        names = {-12: "an octave below", 12: "an octave above", -24: "two octaves below", -3: "a third below",
                 -4: "a third below", -8: "a sixth below", -9: "a sixth below", 7: "a fifth above", 5: "a fourth above"}
        iv = ([ivs[0]] + [x for x in (-12, 12, -24) if x != ivs[0]])[variant % 4]
        label = "Doubling " + names.get(iv, f"{iv:+d} semitones")
        return label, build(notes, tempo, sig, key, chosen, variant, program, d, chords)
    label = STYLE_LABELS[chosen] + (f" (voicing {variant + 1})" if variant else "")
    return label, build(notes, tempo, sig, key, chosen, variant, program, d, chords)
