"""Reading and writing the MIDI files the app plays: melody, sung line and accompaniment layers."""
import math

import mido

from config import ACC_PREFIX, DRUM_PROGRAM, GM_NAMES, VOCAL_CHANNEL, VOCAL_PROGRAM, bar_beats, instrument_name


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


def melody_velocities(notes, sig="4/4", depth=1.0):
    """A velocity for each note [(pitch, start_beat, dur_beats)], so the phrasing can be heard and not only seen:
    downbeats and beats are a little stronger, the note that ends a phrase (a rest follows it) is accented, the line
    swells towards its highest note, each phrase rises and falls a little, and the last notes taper. `depth` scales how
    much of that is applied (0 = every note at one steady level, 1 = as described, 2 = twice as much). Returns a list
    parallel to `notes`, in 30..120."""
    if not notes:
        return []
    bb = bar_beats(sig)
    top = max(range(len(notes)), key=lambda i: (notes[i][0], -notes[i][1]))     # the climax: first of the highest notes
    span = max(notes[-1][1] - notes[0][1], 1e-9)
    climax = (notes[top][1] - notes[0][1]) / span
    phrase_of, first = [], 0
    for i, (_p, s, d) in enumerate(notes):                  # a phrase ends where a rest follows
        gap = (notes[i + 1][1] - (s + d)) if i + 1 < len(notes) else 1.0
        phrase_of.append((first, i))
        if gap >= 0.45:
            first = i + 1
    out = []
    for i, (_p, s, d) in enumerate(notes):
        v = 76.0
        pos = s % bb
        v += 8 if pos < 1e-6 else 4 if abs(pos % 1.0) < 1e-6 else 0
        end = s + d
        gap = (notes[i + 1][1] - end) if i + 1 < len(notes) else 1.0
        if gap >= 0.45:
            v += 7                                  # a phrase-final note is leant on
        x = (s - notes[0][1]) / span
        arch = 1 - abs(x - climax) / max(climax, 1 - climax, 1e-9)    # 0 at the ends, 1 at the climax
        v += 10 * arch
        if i == top:
            v += 4
        if i >= len(notes) - 2:
            v -= 10                                 # the closing notes taper
        a, b = phrase_of[i]
        ph_end = i
        while ph_end + 1 < len(notes) and phrase_of[ph_end + 1][0] == a:
            ph_end += 1
        if ph_end > a:
            v += 5 * math.sin(math.pi * (i - a) / (ph_end - a))        # each phrase swells and fades
        out.append(int(max(30, min(120, round(76 + depth * (v - 76))))))
    return out


def vocal_channel(mid):
    """VOCAL_CHANNEL if this MIDI file has a sung line on it (the Voice program there), else None: an imported file may
    use that channel for anything."""
    for trk in mid.tracks:
        for m in trk:
            if m.type == "program_change" and m.channel == VOCAL_CHANNEL and m.program == VOCAL_PROGRAM:
                return VOCAL_CHANNEL
    return None


def is_acc_track(trk):
    return any(m.type == "track_name" and m.name.startswith(ACC_PREFIX) for m in trk)


def read_acc(path):
    """The accompaniment tracks of a melody file (copies), to be written back unchanged when the file is rewritten."""
    return [trk.copy() for trk in mido.MidiFile(str(path)).tracks if is_acc_track(trk)]


def _acc_program(trk):
    name = next(m.name for m in trk if m.type == "track_name")
    return int(name[len(ACC_PREFIX):])


def _track_notes(trk):
    """[[start_tick, dur_tick, pitch, velocity, channel]] of a track's notes."""
    tick, on, out = 0, {}, []
    for m in trk:
        tick += m.time
        if m.type == "note_on" and m.velocity > 0:
            on[(m.channel, m.note)] = (tick, m.velocity)
        elif m.type in ("note_off", "note_on") and (m.channel, m.note) in on:
            start, vel = on.pop((m.channel, m.note))
            out.append([start, max(tick - start, 1), m.note, vel, m.channel])
    return sorted(out)


def _rebuild_track(trk, notes):
    """The track with its other messages (name, program, volume) and these notes."""
    header = [m.copy(time=0) for m in trk if m.type in ("track_name", "program_change", "control_change")]
    events = []
    for start, dur, pitch, vel, ch in notes:
        events.append((start, 1, pitch, vel, ch))
        events.append((start + dur, 0, pitch, 0, ch))
    events.sort()
    new, last = mido.MidiTrack(header), 0
    for tick, on, pitch, vel, ch in events:
        new.append(mido.Message("note_on" if on else "note_off", note=pitch, velocity=vel, channel=ch, time=tick - last))
        last = tick
    return new


def layers_of(path):
    """[{program, channel, from_bar, to_bar, notes}] of the accompaniment layers of a melody file (one per instrument)."""
    mid = mido.MidiFile(str(path))
    _tempo, sig, _len = midi_info(path)
    bb = bar_beats(sig)
    out = []
    for trk in mid.tracks:
        if not is_acc_track(trk):
            continue
        notes = _track_notes(trk)
        if notes:
            out.append({"program": _acc_program(trk), "channel": notes[0][4], "notes": len(notes),
                        "from_bar": int(notes[0][0] / mid.ticks_per_beat // bb) + 1,
                        "to_bar": int((max(n[0] + n[1] for n in notes) - 1) / mid.ticks_per_beat // bb) + 1})
    return out


def _free_channel(mid, program):
    used = {m.channel for trk in mid.tracks for m in trk if m.type == "note_on"}
    if program == DRUM_PROGRAM:
        return 9
    for c in range(2, 16):
        if c != 9 and c not in used:
            return c
    raise RuntimeError("The piece already has as many instruments as MIDI allows.")


def add_layers_file(src, dest, layers, lo_beat=0.0, hi_beat=None):
    """Copy a melody file and add accompaniment: `layers` is {program: [(pitch, start_beat, duration_beats)]} (velocity is
    shaped afterwards by dynamics.finish). Notes are cut to [lo_beat, hi_beat). A layer of an instrument that is already
    there joins that layer. The melody, the sung line and every other layer are copied exactly."""
    mid = mido.MidiFile(str(src))
    tpb = mid.ticks_per_beat
    for program, notes in layers.items():
        fresh = []
        for p, s, d in notes:
            if s < lo_beat - 1e-6 or (hi_beat is not None and s >= hi_beat - 1e-6):
                continue
            end = min(s + d, hi_beat) if hi_beat is not None else s + d
            fresh.append([round(s * tpb), max(round((end - s) * tpb), 1), p, 80, None])
        if not fresh:
            continue
        mine = next((i for i, trk in enumerate(mid.tracks) if is_acc_track(trk) and _acc_program(trk) == program), None)
        if mine is None:
            ch = _free_channel(mid, program)
            head = [mido.MetaMessage("track_name", name=f"{ACC_PREFIX}{program}", time=0)]
            if program != DRUM_PROGRAM:
                head.append(mido.Message("program_change", program=program, channel=ch, time=0))
            trk = mido.MidiTrack(head)
            mid.tracks.append(trk)
            mine = len(mid.tracks) - 1
        else:
            ch = next(n[4] for n in _track_notes(mid.tracks[mine])) if _track_notes(mid.tracks[mine]) else \
                next((m.channel for m in mid.tracks[mine] if m.type == "program_change"), 9 if program == DRUM_PROGRAM else 2)
        have = _track_notes(mid.tracks[mine])
        mid.tracks[mine] = _rebuild_track(mid.tracks[mine], have + [[a, b, c, d, ch] for a, b, c, d, _ in fresh])
    mid.save(str(dest))
    return dest


def clear_layers_file(src, dest, lo_beat, hi_beat, programs=None):
    """Copy a melody file with the accompaniment taken out of [lo_beat, hi_beat): of every instrument, or of the
    `programs` listed. A note that starts before the range and runs into it is cut at its start. Returns how many
    notes were removed or cut."""
    mid = mido.MidiFile(str(src))
    lo, hi = round(lo_beat * mid.ticks_per_beat), round(hi_beat * mid.ticks_per_beat)
    changed = 0
    for i, trk in enumerate(mid.tracks):
        if not is_acc_track(trk) or (programs and _acc_program(trk) not in programs):
            continue
        kept = []
        for start, dur, pitch, vel, ch in _track_notes(trk):
            if start >= hi or start + dur <= lo:
                kept.append([start, dur, pitch, vel, ch])
            elif start < lo:
                kept.append([start, lo - start, pitch, vel, ch])
                changed += 1
            else:
                changed += 1
        mid.tracks[i] = _rebuild_track(trk, kept)
    mid.tracks = [trk for trk in mid.tracks if not is_acc_track(trk) or _track_notes(trk)]
    mid.save(str(dest))
    return changed


def _line_track(mid, notes, channel, changes, sig, total_beats, dynamics, depth):
    """Add one monophonic line to a MIDI file as its own track. `changes` is [(beat, program)]."""
    tpb = mid.ticks_per_beat
    track = mido.MidiTrack()
    mid.tracks.append(track)
    events = [(round(b * tpb), 0.5, prog, 0) for b, prog in changes]   # a change comes after note-offs, before note-ons
    vels = melody_velocities(notes, sig, depth) if dynamics else [90] * len(notes)
    for (p, s, d), v in zip(notes, vels):
        events.append((round(s * tpb), 1, p, v))
        events.append((round((s + d) * tpb), 0, p, 0))  # note-off sorts before note-on at same tick
    events.sort()
    last = 0
    for tick, kind, p, v in events:
        if kind == 0.5:
            track.append(mido.Message("program_change", program=p, channel=channel, time=tick - last))
        else:
            track.append(mido.Message("note_on" if kind else "note_off", note=p, velocity=v if kind else 0, channel=channel,
                                      time=tick - last))
        last = tick
    if total_beats is not None:  # pad the file out to the final barline so its length is exactly N bars
        track.append(mido.MetaMessage("end_of_track", time=max(round(total_beats * tpb) - last, 0)))


def write_melody_midi(path, tempo, notes, program, sig="4/4", total_beats=None, dynamics=True, depth=1.0, vocal=None, acc=None):
    """`program` is a General MIDI program, or [(beat, program), ...] when the instrument changes along the melody (the
    melody stays on one channel; the changes are program_change events). `vocal` is the optional sung line over it, as
    [(pitch, start_beat, duration_beats)], written as a second track on VOCAL_CHANNEL. `acc` is the accompaniment of the
    file this one replaces (read_acc), written back unchanged."""
    mid = mido.MidiFile(type=1, ticks_per_beat=480)
    mid.tracks.append(conductor_track(tempo, sig))
    changes = [(0.0, program)] if isinstance(program, int) else sorted(program)
    _line_track(mid, notes, 0, changes, sig, total_beats, dynamics, depth)
    if vocal:
        _line_track(mid, vocal, VOCAL_CHANNEL, [(0.0, VOCAL_PROGRAM)], sig, total_beats, dynamics, depth)
    mid.tracks.extend(acc or [])
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


def _melody_program_of(path):
    """The program the melody of a melody file plays (its first instrument)."""
    import expand
    return expand.read_programs(path)[0][1]


def piece_bars(path):
    """(tempo, sig, bars) of a melody file."""
    tempo, sig, length = midi_info(path)
    return tempo, sig, max(1, math.ceil(length * tempo / 60 / bar_beats(sig) - 0.01))


def part_range(path, program, from_bar, to_bar):
    """(lo_beat, hi_beat, from_bar, to_bar) of the bars a new part is for (default: all), after checking that the
    instrument is not already playing there."""
    tempo, sig, bars = piece_bars(path)
    bb = bar_beats(sig)
    lo, hi = max(1, int(from_bar or 1)), min(bars, int(to_bar or bars))
    if lo > hi:
        raise RuntimeError(f"The piece has {bars} bars, so there are no bars {from_bar}-{to_bar} to play in.")
    for layer in layers_of(path):
        if layer["program"] == program and layer["from_bar"] <= hi and layer["to_bar"] >= lo:
            mid = mido.MidiFile(str(path))
            for trk in mid.tracks:
                if is_acc_track(trk) and _acc_program(trk) == program and any(
                        n[0] < hi * bb * mid.ticks_per_beat and n[0] + n[1] > (lo - 1) * bb * mid.ticks_per_beat
                        for n in _track_notes(trk)):
                    raise RuntimeError(f"{instrument_name(program)} already plays in bars {lo}-{hi}. Take it out of those bars "
                                       "first, or choose other bars.")
    return (lo - 1) * bb, hi * bb, lo, hi
