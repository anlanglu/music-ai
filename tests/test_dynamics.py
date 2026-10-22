"""Dynamics (how hard notes are played) and the mixer (the volume of each instrument, stored in the file)."""
import mido

import dynamics
import mixer
import text2music as t

NOTES = [(60 + (i * 2) % 9, float(i) * 0.5, 0.5) for i in range(24)]


def velocities(path, channel=None):
    out = []
    for trk in mido.MidiFile(str(path)).tracks:
        out += [m.velocity for m in trk if m.type == "note_on" and m.velocity > 0 and (channel is None or m.channel == channel)]
    return out


def two_part_file(path, melody_path, tpb=480):
    """What Anticipation hands back: the melody and a bass part, every note at velocity 96."""
    src = mido.MidiFile(str(melody_path))
    mid = mido.MidiFile(type=1, ticks_per_beat=tpb)
    mid.tracks.append(t.conductor_track(100, "4/4"))
    for trk in src.tracks[1:]:
        new = mido.MidiTrack()
        for m in trk:
            new.append(m.copy(velocity=96) if m.type == "note_on" and m.velocity > 0 else m.copy())
        mid.tracks.append(new)
    bass = mido.MidiTrack()
    bass.append(mido.Message("program_change", program=33, channel=1, time=0))
    last = 0
    for beat in range(0, 12, 2):
        tick = beat * tpb
        bass.append(mido.Message("note_on", note=40, velocity=96, channel=1, time=tick - last))
        bass.append(mido.Message("note_off", note=40, velocity=0, channel=1, time=tpb * 2 - 10))
        last = tick + tpb * 2 - 10
    mid.tracks.append(bass)
    mid.save(str(path))


def test_the_melody_has_dynamics_and_depth_changes_how_much(tmp_path):
    flat = t.melody_velocities(NOTES, "4/4", 0)
    normal = t.melody_velocities(NOTES, "4/4", 1)
    more = t.melody_velocities(NOTES, "4/4", 2)
    assert len(set(flat)) == 1, "depth 0: one steady level"
    spread = lambda v: max(v) - min(v)
    assert 0 < spread(normal) < spread(more)


def test_after_the_model_runs_the_melody_gets_its_dynamics_back_and_the_part_sits_under_it(tmp_path):
    mel = tmp_path / "mel.mid"
    t.write_melody_midi(mel, 100, NOTES, 73, "4/4", 12)
    full = tmp_path / "full.mid"
    two_part_file(full, mel)
    assert set(velocities(full)) == {96}
    shaped = dynamics.finish(full, mel, "4/4", 5)
    assert shaped == 6, "only the six bass notes are new"
    assert velocities(full, 0) == velocities(mel, 0), "the melody's velocities are restored exactly"
    bass, melody = velocities(full, 1), velocities(full, 0)
    assert sum(bass) / len(bass) < sum(melody) / len(melody), "on average the accompaniment sits under the melody"


def test_the_dynamics_amount_controls_how_much_the_part_varies(tmp_path):
    mel = tmp_path / "mel.mid"
    t.write_melody_midi(mel, 100, NOTES, 73, "4/4", 12)
    spreads = {}
    for amount in (0, 10):
        full = tmp_path / f"full{amount}.mid"
        two_part_file(full, mel)
        dynamics.finish(full, mel, "4/4", amount)
        v = velocities(full, 1)
        spreads[amount] = max(v) - min(v)
    assert spreads[0] == 0 and spreads[10] > 0


def test_volume_is_set_in_the_file_without_touching_the_notes(tmp_path):
    mel = tmp_path / "mel.mid"
    t.write_melody_midi(mel, 100, NOTES, 73, "4/4", 12)
    before, length = velocities(mel), mido.MidiFile(str(mel)).length
    assert mixer.read_mix(mel)[0]["volume"] == mixer.DEFAULT_VOLUME
    mixer.set_volume(mel, 0, 40)
    mixer.set_volume(mel, 0, 55)                                   # replaces, never piles up
    mix = mixer.read_mix(mel)
    assert len(mix) == 1 and mix[0]["volume"] == 55 and mix[0]["name"] == "Flute" and mix[0]["notes"] == len(NOTES)
    assert sum(1 for trk in mido.MidiFile(str(mel)).tracks for m in trk if m.type == "control_change") == 1
    assert velocities(mel) == before and abs(mido.MidiFile(str(mel)).length - length) < 1e-6


def test_each_instrument_has_its_own_volume(tmp_path):
    mel = tmp_path / "mel.mid"
    t.write_melody_midi(mel, 100, NOTES, 73, "4/4", 12)
    full = tmp_path / "full.mid"
    two_part_file(full, mel)
    mixer.set_volume(full, 1, 30)
    vols = {c["channel"]: c["volume"] for c in mixer.read_mix(full)}
    assert vols == {0: mixer.DEFAULT_VOLUME, 1: 30}
    try:
        mixer.set_volume(full, 7, 50)
    except ValueError:
        pass
    else:
        raise AssertionError("a channel that is not in the file is an error")


def test_a_file_with_system_exclusive_messages_still_has_a_mix(tmp_path):
    mel = tmp_path / "mel.mid"
    t.write_melody_midi(mel, 100, NOTES, 73, "4/4", 12)
    mid = mido.MidiFile(str(mel))
    mid.tracks[1].insert(0, mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01], time=0))      # no channel
    mid.save(str(mel))
    assert mixer.read_mix(mel)[0]["name"] == "Flute"
    mixer.set_volume(mel, 0, 70)
    assert mixer.read_mix(mel)[0]["volume"] == 70
