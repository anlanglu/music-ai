"""The volume of each instrument in a MIDI file, as MIDI volume (controller 7) at the start of the instrument's channel.

Volume is stored in the file itself, so it plays the same in the app, in Logic and in any notation program, and the notes
(and their velocities) are never touched: setting the volume back to where it was restores the file.
"""
import mido

DEFAULT_VOLUME = 100        # what General MIDI plays at when a file sets no volume


def read_mix(path):
    """[{channel, program, name, volume, notes}] for each channel that plays notes, in channel order."""
    from text2music import GM_NAMES

    mid = mido.MidiFile(str(path))
    chans = {}
    for trk in mid.tracks:
        for m in trk:
            if m.is_meta or not hasattr(m, "channel"):           # meta and system-exclusive messages belong to no channel
                continue
            c = chans.setdefault(m.channel, {"channel": m.channel, "program": None, "volume": DEFAULT_VOLUME, "notes": 0})
            if m.type == "program_change" and c["program"] is None:
                c["program"] = m.program
            elif m.type == "control_change" and m.control == 7:
                c["volume"] = m.value
            elif m.type == "note_on" and m.velocity > 0:
                c["notes"] += 1
    out = []
    for ch in sorted(chans):
        c = chans[ch]
        if not c["notes"]:
            continue
        c["name"] = "Drums" if ch == 9 else GM_NAMES[c["program"] or 0]
        out.append(c)
    return out


def set_volume(path, channel, volume):
    """Set one channel's volume (0..127) in the file, replacing any volume it had."""
    volume = max(0, min(127, int(volume)))
    mid = mido.MidiFile(str(path))
    done = False
    for trk in mid.tracks:
        if not any(hasattr(m, "channel") and m.channel == channel for m in trk):
            continue
        # deleting a message must not shift the ones after it: its delta time moves onto the next message
        rebuilt, carry = mido.MidiTrack(), 0
        for m in trk:
            if m.type == "control_change" and m.channel == channel and m.control == 7:
                carry += m.time
                continue
            rebuilt.append(m.copy(time=m.time + carry))
            carry = 0
        at = 0
        while at < len(rebuilt) and rebuilt[at].is_meta and rebuilt[at].type != "end_of_track":
            at += 1
        rebuilt.insert(at, mido.Message("control_change", channel=channel, control=7, value=volume, time=0))
        trk[:] = rebuilt
        done = True
    if not done:
        raise ValueError(f"No channel {channel} in that file")
    mid.save(str(path))
