"""Dynamics: how hard each note is played (MIDI velocity), and how loud each instrument is (MIDI volume, CC 7).

The melody gets its own shape when it is written (`text2music.melody_velocities`). The Anticipation model, however, writes
every note at one fixed velocity and keeps none of the velocities it is given, so after it has run, `finish` puts the
dynamics back:

  * every note that was already in the source piece (the melody, or the parts approved so far) gets its old velocity back,
  * every new note (an accompaniment part) is played at a level that suits its instrument, a little under the melody,
    swelling and fading with the melody's own loudness, with the beat and the bar a little stronger.

`amount` (0..10, 5 = normal) is how much of that shaping is used: at 0 each instrument plays at one steady level.
"""
import bisect

import mido

DRUMS = 128


def role_offset(program):
    """How far below the melody's average loudness an instrument sits: pads and strings sit back, bass and keys carry."""
    if program == DRUMS:
        return -6
    if program in (32, 33, 34, 35, 36, 37, 38, 39, 43, 57, 58, 70):
        return -8                                  # basses, tuba, trombone, bassoon
    if program <= 23 or program in (46, 47):
        return -10                                 # keyboards, organs, harp, timpani
    if 48 <= program <= 55 or 88 <= program <= 95:
        return -16                                 # string sections, choirs, pads
    if 40 <= program <= 45:
        return -12                                 # solo strings
    return -12


def _notes(mid):
    """[(track, message, abs_tick, program)] for every note-on of a file, with the program its track plays."""
    out = []
    for trk in mid.tracks:
        prog = next((m.program for m in trk if m.type == "program_change"), 0)
        tick = 0
        for m in trk:
            tick += m.time
            if m.type == "note_on" and m.velocity > 0:
                out.append((trk, m, tick, DRUMS if m.channel == 9 else prog))
    return out


def finish(path, source_path, sig="4/4", amount=5.0):
    """Restore the source's velocities on the notes it already had and shape the new ones. Returns the number of notes shaped."""
    mid = mido.MidiFile(str(path))
    src = mido.MidiFile(str(source_path))
    scale = mid.ticks_per_beat / src.ticks_per_beat
    old = {}                                                  # (program, pitch) -> sorted [(tick, velocity)]
    env = []                                                  # (tick, velocity) of every source note
    for _, m, tick, prog in _notes(src):
        t = round(tick * scale)
        old.setdefault((prog, m.note), []).append((t, m.velocity))
        env.append((t, m.velocity))
    for v in old.values():
        v.sort()
    if not env:
        return 0
    env.sort()
    mean = sum(v for _, v in env) / len(env)
    std = max((sum((v - mean) ** 2 for _, v in env) / len(env)) ** 0.5, 6.0)
    starts = [t for t, _ in env]
    tpb = mid.ticks_per_beat
    num, den = (int(x) for x in sig.split("/"))
    bar = tpb * num * 4 / den
    depth = max(0.0, min(10.0, float(amount))) / 5.0
    shaped = 0

    def local(tick):
        lo = bisect.bisect_left(starts, tick - 3 * tpb)
        hi = bisect.bisect_right(starts, tick + 3 * tpb)
        near = env[lo:hi] or [min(env, key=lambda e: abs(e[0] - tick))]
        return sum(v for _, v in near) / len(near)

    for trk, m, tick, prog in _notes(mid):
        hits = old.get((prog, m.note), [])
        i = bisect.bisect_left(hits, (tick - 2, 0))
        if i < len(hits) and abs(hits[i][0] - tick) <= 2:
            m.velocity = hits[i][1]                           # a note that was already there keeps its dynamics
            continue
        norm = max(-1.5, min(1.5, (local(tick) - mean) / std))
        pos = tick % bar
        accent = 6 if pos < 2 else 3 if pos % tpb < 2 else 0
        if prog in range(48, 56) or prog in range(88, 96):
            accent = 0                                        # sustained sounds are not accented
        base = mean + role_offset(prog)
        v = base + depth * (9 * norm + accent) + depth * (((m.note * 7 + tick // 5) % 5) - 2)   # a touch of unevenness
        m.velocity = int(max(25, min(120, round(v))))
        shaped += 1
    mid.save(str(path))
    return shaped
