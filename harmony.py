"""Chord names: what a chord symbol means as notes, and a progression written as text.

A chord is read from its name ("Dm7", "Cmaj9", "G7#9", "Bbm7b5", "C/E", "F#dim7", "Csus4") into its root and the
notes above it. A progression is bars separated by `|`, and up to two chords in a bar for a change at the half bar:
"Dm7 G7 | Cmaj7 | Am7 D7". With no `|` every chord is a bar of its own. `%` repeats the chord before it.
"""
import re

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_LETTER = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
MAX_PER_BAR = 2

_TOKEN = re.compile(r"maj|min|mi|dim|aug|sus2|sus4|sus|add\d+|ø|Δ|°|\+|-|M|m|o|b\d+|#\d+|\d+|\(|\)|,")


def _note(text):
    """('C#' -> 1) the pitch class of a note name, or None."""
    m = re.fullmatch(r"([A-Ga-g])([#b♯♭]?)", text)
    if not m:
        return None
    pc = _LETTER[m.group(1).upper()]
    if m.group(2) in ("#", "♯"):
        pc += 1
    elif m.group(2) in ("b", "♭"):
        pc -= 1
    return pc % 12


class Chord:
    """`symbol` as written; `root` and `bass` are pitch classes (bass is None unless it is a slash chord);
    `intervals` the semitones above the root, in the order root, third, fifth, then the colour tones (a ninth is 14)."""

    def __init__(self, symbol, root, intervals, bass=None, remark=""):
        self.symbol, self.root, self.intervals, self.bass, self.remark = symbol, root, intervals, bass, remark
        # the shape a keyboard voicing is built from; a power chord (root and fifth) has its fifth as the "third"
        self.third = intervals[1]
        self.fifth = intervals[2] if len(intervals) > 2 and intervals[2] < 12 else 12

    @property
    def pcs(self):
        out = []
        for p in [(self.root + i) % 12 for i in self.intervals] + ([self.bass] if self.bass is not None else []):
            if p not in out:
                out.append(p)
        return out

    @property
    def extras(self):
        """The notes beyond the triad: the seventh or sixth first, then the ninth, eleventh, thirteenth."""
        return tuple(self.intervals[3:] if len(self.intervals) > 2 else ())

    def notes(self):
        """'D F A C', the note names of the chord, as a sound-check for what a symbol came out as."""
        return " ".join(NOTE_NAMES[p] for p in self.pcs)

    def __repr__(self):
        return f"Chord({self.symbol!r})"


def _degree_name(i):
    return {0: "root", 2: "2nd", 3: "minor 3rd", 4: "major 3rd", 5: "4th", 6: "flat 5th", 7: "5th", 8: "sharp 5th",
            9: "6th", 10: "7th", 11: "major 7th", 13: "b9", 14: "9th", 15: "#9", 16: "b11", 17: "11th", 18: "#11",
            20: "b13", 21: "13th", 22: "#13"}.get(i, f"{i} semitones")


def parse(symbol):
    """The Chord a name stands for. Raises ValueError, saying what could not be read."""
    text = symbol.strip().replace("♯", "#").replace("♭", "b")
    if not text:
        raise ValueError("an empty chord")
    m = re.fullmatch(r"([A-Ga-g][#b]?)(.*)", text)
    if not m:
        raise ValueError(f"'{symbol}' doesn't start with a note name (A to G)")
    root = _note(m.group(1))
    rest = m.group(2)
    bass = None
    slash = re.search(r"/([A-Ga-g][#b]?)$", rest)
    if slash:
        bass, rest = _note(slash.group(1)), rest[:slash.start()]
    rest = rest.replace("6/9", "69")
    third, fifth, seventh, sixth = 4, 7, None, False
    ext = []
    pending_maj = False
    pos = 0
    while pos < len(rest):
        tok = _TOKEN.match(rest, pos)
        if not tok:
            raise ValueError(f"can't read '{rest[pos:]}' in '{symbol}'")
        s = tok.group(0)
        pos = tok.end()
        if s in ("(", ")", ","):
            continue
        if s in ("maj", "M", "Δ"):
            pending_maj = True
        elif s in ("min", "mi", "m", "-"):
            third = 3
        elif s in ("dim", "°", "o"):
            third, fifth = 3, 6
            if rest[pos:pos + 1] == "7":
                seventh, pos = 9, pos + 1
        elif s in ("aug", "+"):
            fifth = 8
        elif s == "ø":
            third, fifth, seventh = 3, 6, 10
        elif s in ("sus2",):
            third = 2
        elif s in ("sus4", "sus"):
            third = 5
        elif s.startswith("add"):
            n = int(s[3:])
            ext.append({2: 14, 4: 17, 6: 9, 9: 14, 11: 17, 13: 21}.get(n, -1))
        elif s[0] in "b#" and s[1:].isdigit():
            n, flat = int(s[1:]), s[0] == "b"
            if n == 5:
                fifth = 6 if flat else 8
            elif n == 9:
                ext.append(13 if flat else 15)
            elif n == 11:
                ext.append(16 if flat else 18)
            elif n == 13:
                ext.append(20 if flat else 22)
            else:
                raise ValueError(f"'{s}' isn't a chord alteration I know (b5, #5, b9, #9, #11, b13)")
        elif s.isdigit():
            n = int(s)
            if n == 5:
                third = None
            elif n == 6:
                ext.append(9)
            elif n == 69:
                ext += [9, 14]
            elif n in (7, 9, 11, 13):
                if seventh is None:
                    seventh = 11 if pending_maj else 10
                pending_maj = False
                if n >= 9:
                    ext.append(14)
                if n == 11:
                    ext.append(17)
                if n >= 13:
                    ext.append(21)
            else:
                raise ValueError(f"'{n}' isn't a chord number I know (5, 6, 7, 9, 11, 13)")
    if pending_maj:
        raise ValueError(f"'maj' needs a number after it, like maj7 or maj9 (in '{symbol}')")
    if -1 in ext:
        raise ValueError(f"can't read an 'add' in '{symbol}'")
    if third is None:                                    # a power chord: root and fifth
        intervals = [0, fifth]
    else:
        intervals = [0, third, fifth]
    if seventh is not None:
        intervals.append(seventh)
    for e in ext:
        if e not in intervals:
            intervals.append(e)
    name = text[0].upper() + text[1:]
    remark = ""
    pcs = [i % 12 for i in intervals]
    for k, i in enumerate(intervals):
        if i % 12 in pcs[:k]:
            remark = (f"{name}: the {_degree_name(i)} is the same note as the {_degree_name(intervals[pcs.index(i % 12)])}, "
                      f"so it adds nothing. For a colour on top, try {text[0].upper()}7#9 or {text[0].upper()}m(add9).")
            break
    return Chord(name, root, intervals, bass, remark)


def parse_progression(text):
    """'Dm7 G7 | Cmaj7' -> [['Dm7', 'G7'], ['Cmaj7']]. Raises ValueError for a chord that can't be read, or more than two
    chords in a bar."""
    text = text.strip()
    if not text:
        return []
    if "|" in text:
        groups = [g.split() for g in re.split(r"\|", text)]
        groups = [g for g in groups if g]
    else:
        groups = [[w] for w in text.replace(",", " ").split()]
    out, prev = [], None
    for g in groups:
        if len(g) > MAX_PER_BAR:
            raise ValueError(f"'{' '.join(g)}' has {len(g)} chords in a bar; two is the most (one per half bar)")
        bar = []
        for w in g:
            if w == "%":
                if prev is None:
                    raise ValueError("'%' repeats the chord before it, and there isn't one yet")
                bar.append(prev)
                continue
            parse(w)
            bar.append(w[0].upper() + w[1:])
            prev = bar[-1]
        out.append(bar)
    return out


def fit(chord, scale_pcs):
    """The names of the chord's notes that are not in the key's scale; empty when it sits in the key."""
    return [NOTE_NAMES[p] for p in chord.pcs if p not in scale_pcs]


def parse_modes(text, valid):
    """'major*3 lydian' -> ['major', 'major', 'major', 'lydian']: one mode per bar, `*3` repeating it. `-` or `key` is a bar in the
    piece's own mode (stored as ''). `valid` is the mode names that exist. Raises ValueError for a name that isn't one."""
    out = []
    for word in text.replace(",", " ").split():
        name, _, times = word.partition("*")
        name = name.lower()
        if name in ("-", "key", "same"):
            name = ""
        elif name not in valid:
            raise ValueError(f"'{name}' isn't a mode I know ({', '.join(valid)}); use - for the piece's own mode")
        if times and not times.isdigit():
            raise ValueError(f"'{word}': what follows * must be a number of bars")
        out += [name] * (int(times) if times else 1)
    return out
