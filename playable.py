"""What real instruments can actually play: practical sounding range and maximum simultaneous notes.

Ranges are in sounding (concert) MIDI pitches and favour the comfortable, idiomatic register over the absolute
extremes: a French horn *can* reach a low B1, but it's muddy and risky, so we stop at F2.
`max_poly` is how many notes one player can sound at once (a violin: 2, via double stops; a flute: 1; a harp: 6).
Section instruments (string ensembles, choirs, pads) stand for many players, so they're allowed full chords.
"""

DRUMS = 128

# GM program -> (lowest pitch, highest pitch, max simultaneous notes)
_TABLE = {}


def _set(programs, lo, hi, poly):
    for p in programs:
        _TABLE[p] = (lo, hi, poly)


_set(range(0, 8), 21, 108, 8)        # pianos, harpsichord, clavinet
_set([8], 60, 108, 4)                # celesta
_set([9], 79, 108, 2)                # glockenspiel
_set([10], 60, 108, 3)               # music box
_set([11], 53, 89, 4)                # vibraphone
_set([12], 45, 96, 4)                # marimba
_set([13], 65, 108, 2)               # xylophone
_set([14], 60, 77, 1)                # tubular bells
_set([15], 48, 96, 4)                # dulcimer
_set(range(16, 21), 36, 96, 8)       # organs
_set(range(21, 24), 41, 93, 6)       # accordion, harmonica
_set(range(24, 31), 40, 88, 6)       # guitars (E2 up; six strings)
_set([31], 52, 100, 2)               # guitar harmonics
_set(range(32, 36), 28, 64, 2)       # acoustic / electric basses (E1 up)
_set(range(36, 40), 28, 67, 1)       # slap / synth basses
_set([40], 55, 96, 2)                # violin (G3 up)
_set([41], 48, 88, 2)                # viola (C3 up)
_set([42], 36, 81, 2)                # cello (C2 up)
_set([43], 28, 55, 1)                # contrabass (E1 up)
_set([44, 45], 36, 96, 4)            # tremolo / pizzicato strings
_set([46], 24, 100, 6)               # harp
_set([47], 38, 57, 1)                # timpani
_set(range(48, 52), 36, 96, 8)       # string ensembles (a section)
_set(range(52, 55), 40, 84, 8)       # choir / voices (a section)
_set([55], 36, 84, 6)                # orchestra hit
_set([56], 54, 86, 1)                # trumpet (F#3 up)
_set([57], 40, 72, 1)                # trombone (E2 up)
_set([58], 29, 65, 1)                # tuba
_set([59], 54, 84, 1)                # muted trumpet
_set([60], 41, 77, 1)                # French horn (F2 up: the low register is muddy and unreliable)
_set([61], 48, 84, 4)                # brass section
_set([62, 63], 36, 84, 6)            # synth brass
_set([64], 56, 87, 1)                # soprano sax
_set([65], 49, 81, 1)                # alto sax
_set([66], 44, 76, 1)                # tenor sax
_set([67], 36, 69, 1)                # baritone sax
_set([68], 58, 93, 1)                # oboe
_set([69], 52, 81, 1)                # English horn
_set([70], 34, 75, 1)                # bassoon
_set([71], 50, 91, 1)                # clarinet
_set([72], 74, 108, 1)               # piccolo
_set([73], 60, 96, 1)                # flute (C4 up)
_set([74, 75, 76, 77], 60, 96, 1)    # recorder, pan flute, bottle, shakuhachi
_set([78], 72, 96, 1)                # whistle
_set([79], 60, 89, 1)                # ocarina
_set(range(80, 88), 48, 96, 1)       # synth leads
_set(range(88, 96), 36, 96, 8)       # synth pads
_set(range(96, 104), 24, 108, 6)     # synth effects
_set([104], 48, 89, 3)               # sitar
_set([105, 106, 107], 48, 89, 4)     # banjo, shamisen, koto
_set([108], 60, 91, 4)               # kalimba
_set([109], 58, 81, 2)               # bagpipe
_set([110], 55, 96, 2)               # fiddle
_set([111], 60, 84, 1)               # shanai
_set(range(112, 120), 36, 96, 4)     # percussive pitched sounds

DEFAULT = (24, 100, 6)


def limits(program):
    """(low, high, max_poly) for a GM program; drums are unconstrained."""
    if program == DRUMS:
        return (0, 127, 16)
    return _TABLE.get(program, DEFAULT)


def sampler_pool(allowed, balance=0.0, free=False, extra=None, pitch_classes=None, ranges=None):
    """The instrument-pool dict the Anticipation sampler understands: which instruments may play, each one's
    playable range, and how many simultaneous notes it may sound."""
    pool = {
        "allowed": list(allowed),
        "balance": balance,
        "free": free,
        "ranges": {i: limits(i)[:2] for i in allowed},
        "max_poly": {i: limits(i)[2] for i in allowed},
    }
    if ranges:  # narrower per-instrument ranges (e.g. "low register") inside the playable ones
        pool["ranges"].update(ranges)
    if pitch_classes:
        pool["pitch_classes"] = set(pitch_classes)
    if extra:
        pool.update(extra)
    return pool


def fold_pitch(pitch, program):
    """Move a pitch by octaves into the instrument's range (clamping only if the range is under an octave)."""
    lo, hi, _ = limits(program)
    if program == DRUMS:
        return pitch
    while pitch < lo and pitch + 12 <= 127:
        pitch += 12
    while pitch > hi and pitch - 12 >= 0:
        pitch -= 12
    return min(max(pitch, lo), hi)
