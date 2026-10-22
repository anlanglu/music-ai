"""MIDI -> engraved sheet music (self-contained HTML with inline SVG pages), fully local.

music21 turns the MIDI into notation (quantised rhythms, ties, rests, key, clefs); Verovio engraves it.
"""
import html
from pathlib import Path

import mido
import verovio
from music21 import chord, clef, converter, key, layout, meter, note, stream

import plan
from text2music import GM_NAMES, TMP_DIR, key_for_file


def _strip_drums(path, dest):
    """Copy a MIDI file without its channel-10 (drum) notes: drums have no pitches to put on a staff."""
    src = mido.MidiFile(str(path))
    out = mido.MidiFile(type=src.type, ticks_per_beat=src.ticks_per_beat)
    for trk in src.tracks:
        if any(m.type in ("note_on", "note_off") and m.channel == 9 for m in trk):
            continue
        out.tracks.append(trk)
    out.save(str(dest))


def _part_programs(path):
    """GM program of each pitched track, in file order (from the first program_change)."""
    progs = []
    for trk in mido.MidiFile(str(path)).tracks:
        notes = [m for m in trk if m.type == "note_on" and m.velocity > 0]
        if not notes or any(m.channel == 9 for m in notes):
            continue
        progs.append(next((m.program for m in trk if m.type == "program_change"), 0))
    return progs


# Each instrument's usual notation (concert pitch). Anything not listed is a single treble staff.
GRAND_STAFF = set(range(0, 9)) | set(range(16, 24)) | {12, 46} | set(range(48, 55)) | set(range(88, 96))
BASS_CLEF = set(range(32, 40)) | {42, 43, 47, 57, 58, 70}   # basses, cello, contrabass, timpani, trombone, tuba, bassoon
ALTO_CLEF = {41}                                           # viola
TENOR_TOO = {42, 57, 70}                                   # cello, trombone, bassoon use tenor clef for their upper range
TREBLE_8VB = set(range(24, 32))                            # guitars sound an octave below written
SPLIT = 60                                                 # grand staff: middle C and up -> treble staff


def _clef_for(program, mean_pitch):
    """Usual clef for the instrument, but never one that leaves the part on a pile of ledger lines: a bass-clef
    instrument that is playing high (e.g. a cello carrying the melody) moves to tenor / treble clef; a horn that is
    playing low moves to bass clef; the piccolo and glockenspiel, which sound 1 and 2 octaves above written, get the
    clefs that say so."""
    if program in BASS_CLEF:
        if mean_pitch >= 60:
            return clef.TrebleClef()
        if program in TENOR_TOO and mean_pitch >= 50:
            return clef.TenorClef()
        return clef.BassClef()
    if program == 60:                                       # French horn: treble, bass for its low register
        return clef.BassClef() if mean_pitch < 50 else clef.TrebleClef()
    if program == 72:                                       # piccolo
        return clef.Treble8vaClef()
    if program == 9:                                        # glockenspiel
        two = clef.TrebleClef()
        two.octaveChange = 2
        return two
    if program in ALTO_CLEF:
        return clef.AltoClef()
    if program in TREBLE_8VB:
        return clef.Treble8vbClef()
    return clef.TrebleClef()


_MAJOR_SHARPS = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: -5, 8: -4, 3: -3, 10: -2, 5: -1}


def _sharps(key):
    """Key signature (+ sharps / - flats) of a (tonic pitch class, mode) key."""
    tonic, mode = key
    if mode == "minor" and tonic == 3:
        return -6                                                  # E-flat minor, not D-sharp minor
    return _MAJOR_SHARPS[(tonic - plan.PARENT_MAJOR_BELOW.get(mode, 0)) % 12]    # the major key with the same notes


def _respell(part, sharps):
    """Spell notes the way the key signature does (B-flat in F major, not A-sharp), leaving genuine accidentals
    such as the raised 7th of a minor key alone."""
    if sharps == 0:
        return
    altered = {p.name for p in key.KeySignature(sharps).alteredPitches}
    for el in part.recurse().notes:
        for p in el.pitches:
            if p.accidental is not None and p.accidental.alter in (1, -1):
                alt = p.getEnharmonic()
                if alt.name in altered:
                    p.name, p.octave = alt.name, alt.octave


def _finish(part, sharps, sig):
    """Add key / time signature, then bar the part up and fill the gaps with rests."""
    _respell(part, sharps)
    part.insert(0, key.KeySignature(sharps))
    part.insert(0, meter.TimeSignature(sig.ratioString))
    part.makeMeasures(inPlace=True)
    part.makeRests(fillGaps=True, inPlace=True)
    part.makeTies(inPlace=True)
    for m in part.getElementsByClass(stream.Measure):
        m.splitAtDurations(recurse=False)  # a gap-filling rest like 2.5 quarters must become 2 + 0.5
    return part


def _split_hands(elements):
    """Split notes/chords between the treble (>= SPLIT) and bass (< SPLIT) staves of a grand staff."""
    upper, lower = [], []
    for el in elements:
        if el.quarterLength <= 0:  # zero-length leftovers from quantising can't be notated
            continue
        pitches = list(el.pitches)
        hi = [p for p in pitches if p.midi >= SPLIT]
        lo = [p for p in pitches if p.midi < SPLIT]
        for target, group in ((upper, hi), (lower, lo)):
            if not group:
                continue
            new = note.Note(group[0]) if len(group) == 1 else chord.Chord(group)
            new.quarterLength = el.quarterLength
            target.append((el.offset, new))
    return upper, lower


def render(midi_path, title=None, scale=45, paged=False):
    """Write <midi>.html next to the MIDI file and return its path.

    paged=True lays the score out on fixed A4 pages (<midi>-print.html) for PDF export instead of one
    continuous, screen-friendly column."""
    midi_path = Path(midi_path)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TMP_DIR / "_notation_tmp.mid"
    _strip_drums(midi_path, tmp)
    programs = _part_programs(tmp)

    score = converter.parse(str(tmp), quantizePost=True)
    parts = [p for p in score.parts if p.recurse().notes]
    if not parts:
        raise RuntimeError("No pitched notes to notate")
    sig = next(iter(score.recurse().getElementsByClass(meter.TimeSignature)), meter.TimeSignature("4/4"))
    chosen = key_for_file(midi_path)   # the key picked for the session, so the signature matches what was asked for
    if chosen:
        sharps = _sharps(chosen)
    else:
        try:
            sharps = score.analyze("key").sharps
        except Exception:
            sharps = 0

    out = stream.Score()
    groups = []  # one entry per instrument: [part] or [upper, lower] for a grand staff
    for i, p in enumerate(parts):
        program = programs[i] if i < len(programs) else 0
        label = GM_NAMES[program]
        elements = [el for el in p.flatten().notes if el.quarterLength > 0]
        if program in GRAND_STAFF:
            staves = []
            for placed, which in zip(_split_hands(elements), (clef.TrebleClef(), clef.BassClef())):
                st = stream.PartStaff()
                for off, el in placed:
                    st.insert(off, el)
                st.insert(0, which)
                staves.append(_finish(st, sharps, sig))
            staves[0].partName = label
            staves[0].partAbbreviation = label[:4] + "."
            groups.append(staves)
        else:
            part = stream.Part()
            part.partName = label
            part.partAbbreviation = label[:4] + "."
            for el in elements:
                part.insert(el.offset, el)
            mean = sum(p.midi for el in elements for p in el.pitches) / max(sum(len(el.pitches) for el in elements), 1)
            part.insert(0, _clef_for(program, mean))
            groups.append([_finish(part, sharps, sig)])
    # Staves that go quiet early would otherwise just stop; pad every one to the full length with bar rests.
    everything = [st for g in groups for st in g]
    n_bars = max(len(st.getElementsByClass(stream.Measure)) for st in everything)
    bar_ql = sig.barDuration.quarterLength
    for st in everything:
        for k in range(len(st.getElementsByClass(stream.Measure)), n_bars):
            m = stream.Measure(number=k + 1)
            r = note.Rest(quarterLength=bar_ql)
            r.fullMeasure = "always"
            m.append(r)
            st.append(m)
    for g in groups:
        for st in g:
            out.insert(0, st)
        if len(g) == 2:
            out.insert(0, layout.StaffGroup(g, name=g[0].partName, abbreviation=g[0].partAbbreviation,
                                            symbol="brace", barTogether=True))
    if title:
        from music21 import metadata

        out.insert(0, metadata.Metadata(title=title))

    xml_path = midi_path.with_suffix(".musicxml")  # kept: this is what notation apps (MuseScore, Dorico...) open
    out.write("musicxml", fp=str(xml_path))

    tk = verovio.toolkit()
    tk.setOptions({
        "scale": scale, "pageWidth": 2100, "pageHeight": 2970, "adjustPageHeight": not paged,
        "pageMarginLeft": 40, "pageMarginRight": 40, "pageMarginTop": 40, "pageMarginBottom": 40,
        "breaks": "auto", "svgViewBox": True, "footer": "none",
    })
    if not tk.loadFile(str(xml_path)):
        raise RuntimeError("Verovio could not read the score")
    pages = "\n".join(f'<div class="page">{tk.renderToSVG(n)}</div>' for n in range(1, tk.getPageCount() + 1))
    if paged:  # exactly one A4 page (794 x 1123 CSS px) per sheet, no margins, white
        css = ("body{margin:0;background:#fff}.page{width:794px;height:1123px;overflow:hidden;background:#fff;"
               "break-after:page}.page svg{width:100%;height:100%;display:block}")
    else:
        css = ("body{margin:0;background:#e9e9ec;font-family:-apple-system,sans-serif}"
               ".page{background:#fff;margin:14px auto;max-width:1000px;box-shadow:0 1px 6px rgba(0,0,0,.25);padding:8px}"
               ".page svg{width:100%;height:auto;display:block}")
    page = (f'<!doctype html><meta charset="utf-8"><title>{html.escape(title or midi_path.name)}</title>'
            f"<style>{css}</style>{pages}")
    dest = midi_path.with_name(midi_path.stem + ("-print.html" if paged else ".html"))
    dest.write_text(page, encoding="utf-8")
    return dest


if __name__ == "__main__":
    import sys

    print(render(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
