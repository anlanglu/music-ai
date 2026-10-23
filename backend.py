"""JSON-lines backend for the Swift app. Reads one JSON request per stdin line, writes events to stdout.

Requests (one JSON object per line, "cmd" selects the stage):
  {"cmd":"melodies", prompt, sliders {shape,pulse,harmony,endings,theme,runs: each 0..10}, bars, program, sig, bpm, resolve, key:[tonic, mode|"auto"], mode:"auto"|"major"|"minor"|"dorian"|"phrygian"|"lydian"|"mixolydian"|"locrian", complexity, llm, count}
  {"cmd":"expand", "source_midi":..., bars (2..16), engine ("hybrid"|"llm"), count, size, llm, complexity, sliders, direction, resolve, phrasing}
  {"cmd":"edit", "source_midi":..., "instruction":..., "bars":[...], count, llm, complexity}   (only the bars named in the instruction, else "bars", change)
  {"cmd":"save_edit", "source_midi":..., "notes":[[pitch, start_beat, duration_beats],...]}   (the melody as edited by hand)
  {"cmd":"import", "path":...}
  {"cmd":"add_part", "source_midi":..., "program", "count", "texture", "style", "direction", ...} | {"cmd":"approve_part", "path", "label"}
  {"cmd":"notation", "path":..., "title":..., "paged":false}
  {"cmd":"session_list"} | {"cmd":"session_new"} | {"cmd":"session_rename", "name":...} | {"cmd":"session_open", "path":...}
  {"cmd":"orchestrate", "source_midi":..., ensemble, playable, size, dynamics 0..10}
  {"cmd":"mix_info", "path":...} | {"cmd":"set_volume", "path":..., "channel":..., "volume": 0..127}  ->  mix{path, channels:[{channel, program, name, volume, notes}]}
Any request may also carry "seed": <int> to make that run repeatable.
Events:   ready{sliders} | log{text} | candidate{index,path,tempo,sig,score,notes} | melodies_done |
          done{melody,full,notes} | notation{midi,html,paged} | notation_error{midi,text} |
          session{name,path,old_path} | sessions{items:[{name,path,created,files}]} | part_candidate{index,path,program,label,channel,added,programs,notes} | parts_done | part_approved{path,notes,programs} | edit_suggestion{bars,reason,targets} | error{text}      (notes = [[channel,pitch,start,end],...])
"""
import json
import os
import sys

# Libraries print to stdout; keep the real stdout for the protocol only.
_proto = os.fdopen(os.dup(1), "w", buffering=1)
sys.stdout = sys.stderr

import mido  # noqa: E402

import re
import time
from pathlib import Path

from text2music import (  # noqa: E402
    add_part_options, approve_part, current_session, ensure_session, instrument_name, list_sessions,
    make_melodies, make_music, midi_info, new_session, open_session, rename_session, reset_session, seed_everything,
    session_info,
)
import mixer  # noqa: E402
import plan  # noqa: E402
from expand import expand_melody  # noqa: E402
from edit import edit_melody, save_edit  # noqa: E402


def send(**ev):
    _proto.write(json.dumps(ev) + "\n")
    _proto.flush()


def notes_of(path):
    t, on, notes = 0.0, {}, []
    for msg in mido.MidiFile(str(path)):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            on[(msg.channel, msg.note)] = t
        elif msg.type in ("note_off", "note_on") and (msg.channel, msg.note) in on:
            notes.append([msg.channel, msg.note, on.pop((msg.channel, msg.note)), t])
    return notes


def programs_of(path):
    """GM program of every pitched track in a MIDI file (128 = drums), and the channel each one plays on."""
    progs = {}
    for trk in mido.MidiFile(str(path)).tracks:
        notes = [m for m in trk if m.type == "note_on" and m.velocity > 0]
        if not notes:
            continue
        chan = notes[0].channel
        prog = 128 if chan == 9 else next((m.program for m in trk if m.type == "program_change"), 0)
        progs[prog] = chan
    return progs


def part_event(info):
    progs = programs_of(info["path"])
    send(type="part_candidate", index=info["index"], path=str(info["path"]), program=info["program"],
         label=info.get("label", ""),
         channel=progs.get(info["program"], -1), added=info["added"], programs=list(progs), notes=notes_of(info["path"]))


def candidate_event(info):
    send(type="candidate", index=info["index"], path=str(info["path"]), tempo=info["tempo"], sig=info["sig"],
         score=info.get("score"), notes=notes_of(info["path"]), programs=list(programs_of(info["path"])))


def announce(old=None):
    """Tell the app which session folder is current (and, after a rename, where it used to be)."""
    path = current_session()
    if path is None:
        send(type="session", name="", path="", old_path="")
        return
    info = session_info(path)
    send(type="session", name=info["name"], path=info["path"], old_path=str(old) if old else "")
    send(type="sessions", items=list_sessions())


def begin(prompt=""):
    """Make sure a session exists before any file is written; announce it if it was just created."""
    had = current_session()
    ensure_session(prompt)
    if had is None:
        announce()


def restore(path):
    """Re-emit what a saved session contains: its latest batch of melody options and its latest full piece."""
    folder = Path(path)
    batches = {}
    for f in sorted(folder.glob("*-opt*.mid")):
        m = re.match(r"(.+)-opt(\d+)\.mid$", f.name)
        if m:
            batches.setdefault(m.group(1), []).append((int(m.group(2)), f))
    if batches:
        latest = sorted(batches)[-1]
        for k, f in sorted(batches[latest]):
            tempo, sig, _ = midi_info(f)
            candidate_event({"index": k, "path": f, "tempo": tempo, "sig": sig, "score": None})
    else:
        imports = sorted(folder.glob("*-import.mid"))
        if imports:
            tempo, sig, _ = midi_info(imports[-1])
            candidate_event({"index": 1, "path": imports[-1], "tempo": tempo, "sig": sig, "score": None})
    send(type="melodies_done")
    # the latest finished piece: an automatic orchestration (*-full.mid) or the newest approved version (pieceNN-*.mid)
    finals = list(folder.glob("*-full.mid")) + list(folder.glob("piece*.mid"))
    if finals:
        latest = max(finals, key=lambda f: (f.stat().st_mtime, f.name))
        send(type="done", melody=str(latest), full=str(latest), notes=notes_of(latest), programs=list(programs_of(latest)))


def chosen_mode(r):
    """The mode asked for ("auto" or none: leave it to the prompt)."""
    m = str(r.get("mode") or "auto").lower()
    return m if m in plan.MODES else None


def chosen_key(r):
    """The key asked for, as (tonic, mode): a tonic from the app, with the mode chosen or, on Auto, the one the prompt's
    mood suggests (minor for sad words, major otherwise)."""
    k = r.get("key")
    if not k:
        return None
    mode = chosen_mode({"mode": k[1] if len(k) > 1 else None}) or chosen_mode(r) or plan.mode_for(r.get("prompt", ""))
    return int(k[0]), mode


def handle(r):
    cmd = r.get("cmd", "orchestrate")
    if r.get("seed") is not None:
        seed_everything(int(r["seed"]))
    knobs = r.get("knobs") or None
    log = lambda s: send(type="log", text=s)
    if cmd == "session_list":
        send(type="sessions", items=list_sessions())
    elif cmd == "session_new":  # lazily: the folder is only created once something is written
        reset_session()
        announce()
    elif cmd == "session_rename":
        if current_session() is None:  # naming before the first generation: create it now with that name
            new_session(r["name"])
            announce()
        else:
            old, _ = rename_session(r["name"])
            announce(old)
    elif cmd == "session_open":
        open_session(r["path"])
        announce()
        restore(r["path"])
    elif cmd == "melodies":
        if current_session() is None and r.get("session_name"):
            new_session(r["session_name"])
            announce()
        begin(r["prompt"])
        make_melodies(
            r["prompt"], int(r.get("bars", 8)), int(r.get("program", 0)),
            r.get("sig", "4/4"), int(r.get("complexity", 3)), r.get("llm", "7B"), int(r.get("count", 4)),
            log=log, on_candidate=candidate_event,
            key=chosen_key(r), mode=chosen_mode(r),
            bpm=max(40, min(int(r.get("bpm", 80)), 220)), resolve=bool(r.get("resolve", True)), phrasing=int(r.get("phrasing", 1)),
            sliders=r.get("sliders"),
        )
        send(type="melodies_done")
    elif cmd == "expand":  # step 2: add bars to the chosen melody, as several options
        begin()
        expand_melody(
            r["source_midi"], max(2, min(16, int(r.get("bars", 4)))), r.get("engine", "hybrid"), int(r.get("count", 3)),
            r.get("size", "small"), r.get("llm", "7B"), int(r.get("complexity", 3)), r.get("sliders"),
            r.get("direction", ""), bool(r.get("resolve", True)), int(r.get("phrasing", 1)), log=log,
            on_candidate=candidate_event,
        )
        send(type="melodies_done")
    elif cmd == "edit":  # step 3: change only the bars the user named, as several options
        begin()
        edit_melody(
            r["source_midi"], r.get("instruction", ""), r.get("bars"), int(r.get("count", 3)), r.get("llm", "7B"),
            int(r.get("complexity", 3)), log=log, on_candidate=candidate_event,
            on_suggestion=lambda sug, targets: send(type="edit_suggestion", bars=sug["bars"], reason=sug["reason"], targets=targets),
        )
        send(type="melodies_done")
    elif cmd == "save_edit":  # step 3, by hand: write the notes as the user left them
        begin()
        save_edit(r["source_midi"], r["notes"], log=log, on_candidate=candidate_event)
        send(type="melodies_done")
    elif cmd == "add_part":  # stage 2, one instrument at a time: several options for the chosen instrument
        begin()
        add_part_options(
            r["source_midi"], int(r["program"]), int(r.get("count", 3)), r.get("size", "small"),
            bool(r.get("playable", True)), log=log, on_candidate=part_event,
            in_key=bool(r.get("in_key", True)), direction=r.get("direction", ""),
            dynamics=float(r.get("dynamics", 5)),
            texture_style=(r.get("style") or "mix") if r.get("texture") else None,
        )
        send(type="parts_done")
    elif cmd == "approve_part":
        dest = approve_part(r["path"], r.get("label", "part"))
        send(type="part_approved", path=str(dest), notes=notes_of(dest), programs=list(programs_of(dest)))
        send(type="sessions", items=list_sessions())
    elif cmd == "mix_info":  # the volume of each instrument in a file
        send(type="mix", path=r["path"], channels=mixer.read_mix(r["path"]))
    elif cmd == "set_volume":  # change one instrument's volume in the file itself, then report the new mix
        mixer.set_volume(r["path"], int(r["channel"]), int(r["volume"]))
        send(type="mix", path=r["path"], channels=mixer.read_mix(r["path"]))
    elif cmd == "notation":  # engrave a MIDI file as sheet music (HTML with inline SVG)
        try:
            from notation import render

            paged = bool(r.get("paged", False))
            html_path = render(r["path"], r.get("title"), paged=paged)
            send(type="notation", midi=r["path"], html=str(html_path), paged=paged)
        except Exception as e:
            send(type="notation_error", midi=r["path"], text=f"{type(e).__name__}: {e}")
    elif cmd == "import":  # treat an imported file as the one and only melody option
        import shutil

        src = Path(r["path"])
        if current_session() is None and r.get("session_name"):
            new_session(r["session_name"])
            announce()
        begin(src.stem)
        dest = current_session() / f"{time.strftime('%H%M%S')}-import.mid"
        shutil.copyfile(src, dest)
        tempo, sig, _ = midi_info(dest)
        candidate_event({"index": 1, "path": dest, "tempo": tempo, "sig": sig, "score": None})
        send(type="melodies_done")
    else:  # orchestrate: keep the chosen melody exactly, add the accompaniment
        begin()
        melody, full = make_music(
            "", size=r.get("size", "small"), log=log,
            ensemble=int(r.get("ensemble", 0)), source_midi=r["source_midi"],
            playable=bool(r.get("playable", True)), in_key=bool(r.get("in_key", True)),
            dynamics=float(r.get("dynamics", 5)),
        )
        send(type="done", melody=str(melody), full=str(full), notes=notes_of(full), programs=list(programs_of(full)))
        send(type="sessions", items=list_sessions())


def main():
    # where the app's sliders start, so the defaults live in one place (plan.py)
    send(type="ready", sliders=plan.SLIDER_DEFAULTS)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            handle(json.loads(line))
        except Exception as e:
            send(type="error", text=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
