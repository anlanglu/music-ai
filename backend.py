"""JSON-lines backend for the Swift app. Reads one JSON request per stdin line, writes events to stdout.

Requests (one JSON object per line, "cmd" selects the stage):
  {"cmd":"melodies", prompt, sliders {shape,pulse,harmony,endings,theme,flavor,strict,dynamics: each 0..10; missing ones take plan.SLIDER_DEFAULTS}, bars, program, sig, bpm, resolve, key:[tonic, mode|"auto"], mode:"auto"|"major"|"minor"|"dorian"|"phrygian"|"lydian"|"mixolydian"|"locrian", complexity, llm, count}
  {"cmd":"expand", "source_midi":..., bars (2..16), engine ("hybrid"|"llm"), count, size, llm, complexity, sliders, direction, resolve, phrasing}
  {"cmd":"expand", ..., "chords": true, "program"}   (the new bars are chords for that instrument, in the plan's harmony, one pattern per option; the melody line is silent in them, ready to be sung to)
  {"cmd":"melodies"|"expand", ..., "progression": "Dm7 G7 | Cmaj7 | Am7 D7"}   (the chords the new bars follow: bars split by |, up to two chords in a bar, see harmony.py)
  {"cmd":"chords", "source_midi"}  ->  chords{path,key,written,bars:[[{symbol,notes,outside,remark}]]}   (the chords of each bar; "outside" = notes the mode lacks)
  {"cmd":"set_chords", "source_midi", "progression" | "bars_chords": [[symbol,...] per bar]}  ->  chords{...}   (written into the session's plan; later steps follow them)
  {"cmd":"melodies"|"expand", ..., "modes": "major*3 lydian"}   (a mode of its own for some bars: names from plan.MODES, *N repeats, - is the piece's mode; bars after the list keep the piece's)
  {"cmd":"set_modes", "source_midi", "modes" | "bar_modes": ["", "lydian", ...]}  ->  chords{...}   (the chords event also carries modes, labels and mode_names per bar)
  {"cmd":"repeat", "source_midi", "from_bar", "to_bar", "times"?: 1..4}  (bars copied onto the end: melody, instruments, sung line and its lyrics, accompaniment)
  {"cmd":"sing_part", "source_midi", "lyrics", "line_bars"?: 0 auto | 1..4 (bars each lyric line takes; "[2]" after a line overrides, a blank line ends a verse), "start_bar"?, "bars", voice, voices, harmony, llm, complexity, direction, count}  (a sung line over the melody: the next lyric lines in those bars; melody and earlier singing kept)
  {"cmd":"edit", "source_midi":..., "instruction":..., "bars":[...], "part"?: "melody"|"vocal", count, llm, complexity}   (only the bars named in the instruction, else "bars", change)
  {"cmd":"save_edit", "source_midi":..., "notes":[[pitch, start_beat, duration_beats],...]}   (the melody as edited by hand)
  {"cmd":"import", "path":...}
  {"cmd":"add_part", "source_midi":..., "program", "from_bar"?, "to_bar"?, "count", "texture", "style", "direction", ...} | {"cmd":"approve_part", "path", "label"}
  {"cmd":"clear_bars", "source_midi", "from_bar"?, "to_bar"?, "programs"?: [..]}  ->  part_approved{path,notes,programs,layers}  (the accompaniment taken out of those bars)
  A part is an accompaniment layer kept in the melody's own file, for the bars asked for (default: all); candidate, part_candidate, part_approved and done events carry layers:[{program,channel,from_bar,to_bar,notes}]
  {"cmd":"notation", "path":..., "title":..., "paged":false}
  {"cmd":"session_list"} | {"cmd":"session_new"} | {"cmd":"session_rename", "name":...} | {"cmd":"session_open", "path":...}
  {"cmd":"orchestrate", "source_midi":..., ensemble, playable, size, dynamics 0..10}
  {"cmd":"mix_info", "path":...} | {"cmd":"set_volume", "path":..., "channel":..., "volume": 0..127}  ->  mix{path, channels:[{channel, program, name, volume, notes}]}
  {"cmd":"voice_add", "path":"a recording of a voice singing alone", "name", "language":"English"|"Mandarin"}  ->  voices{items:[{key,name}]}, melodies_done
  {"cmd":"lyrics_check", "lyrics", "line_bars"?: 0 auto | 1..4}  ->  lyrics_info{text}
  {"cmd":"lyrics_sing", "source_midi", "backing_midi"?, "voice"?: "en"|"zh"|"u-<name>"}  ->  vocal{path,backing} (the lyric lines sung, each in the singer stored with it, a .wav next to the melody; "voice" is only for lines from before singers were stored; backing = the piece without the stretches played by the Voice instrument)
  "melodies" with the Voice instrument (program 53), and "expand" with "program": 53, take "lyrics" (the whole text) and "voice"
  (the singer of those bars), "voices" (further singers with them: a duet or choir of up to 4) and "harmony" (true: each further singer a harmony part, false: the same notes) and write the melody to as many lines as fit; expand carries on with the next line. "expand" with
  any other "program" passes the melody to that instrument for the new bars; without "program" the melody's last instrument goes on
Any request may also carry "seed": <int> to make that run repeatable.
Events:   ready{sliders} | voices{items:[{key,name}]} | lyrics_info{text,ok} | log{text} | candidate{index,path,tempo,sig,score,notes} | melodies_done |
          done{melody,full,notes} | notation{midi,html,paged} | notation_error{midi,text} |
          session{name,path,old_path} | sessions{items:[{name,path,created,files}]} | part_candidate{index,path,program,label,channel,added,programs,notes} | parts_done | part_approved{path,notes,programs} | edit_suggestion{bars,reason,targets} | vocal{path,backing} | error{text}      (notes = [[channel,pitch,start,end],...])
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

from config import bar_beats  # noqa: E402
from midifiles import clear_layers_file, layers_of, midi_info, piece_bars  # noqa: E402
from parts import add_part_options, approve_part  # noqa: E402
from sessions import current_session, ensure_session, list_sessions, new_session, open_session, rename_session, reset_session, seed_everything, session_info  # noqa: E402
from text2music import make_melodies, make_music  # noqa: E402
import harmony  # noqa: E402
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
         channel=progs.get(info["program"], -1), added=info["added"], programs=list(progs), notes=notes_of(info["path"]),
         layers=layers_of(info["path"]))


def candidate_event(info):
    send(type="candidate", index=info["index"], path=str(info["path"]), tempo=info["tempo"], sig=info["sig"],
         score=info.get("score"), notes=notes_of(info["path"]), programs=list(programs_of(info["path"])),
         layers=layers_of(info["path"]))


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


def progression_of(r):
    """The chords the request asks for, as [[chord name, ...] per bar], or None. A ValueError names what can't be read."""
    if r.get("bars_chords"):
        return [[str(c) for c in bar] for bar in r["bars_chords"]]
    text = (r.get("progression") or "").strip()
    return harmony.parse_progression(text) if text else None


def modes_of(r):
    """The mode of each bar the request asks for, as ['major', 'major', '', 'lydian'] ('' = the piece's own), or None."""
    if r.get("bar_modes") is not None:
        return [str(m) for m in r["bar_modes"]]
    text = (r.get("modes") or "").strip()
    return harmony.parse_modes(text, plan.MODE_ORDER) if text else None


def chords_event(path):
    """The chords of a melody's bars for the app's chord strip: each chord's notes, and the notes that the key's mode doesn't
    have (the strip shows those bars in another colour). The plan's chords, else the ones the melody's notes suggest."""
    import math
    import text2music as t
    from expand import read_melody
    src = Path(path)
    notes, _tempo, sig, _first, total = read_melody(src)
    key = t.key_for_file(src) or t.estimate_key(notes)
    bars = max(1, math.ceil(total / t.bar_beats(sig) - 0.01))
    base = t.plan_for_file(src) or {}
    chords = list(base.get("chords", []))[:bars]
    if len(chords) < bars:
        chords += plan.infer_chords(notes, key, sig, bars)[len(chords):]
    view = {"key": key, "chords": chords, "harmony": list(base.get("harmony") or [])[:bars] or None,
            "modes": (list(base.get("modes") or []) + [""] * bars)[:bars]}
    out = []
    for bar, miss in zip(plan.bar_chords(view), plan.chord_fit(view)):
        out.append([{"symbol": c.symbol, "notes": c.notes(), "outside": m, "remark": c.remark} for c, m in zip(bar, miss)])
    send(type="chords", path=str(src), key=t.key_label(key), written=bool(base.get("harmony")), bars=out,
         modes=view["modes"], labels=[t.key_label((key[0], m)) for m in plan.bar_modes(view)],
         mode_names=plan.MODE_ORDER)


def write_chords(path, progression=None, modes=None):
    """Keep chords (and a mode for some bars) the user wrote in the session's plan: they are what later steps (Extend,
    accompaniment, singing) follow. Whichever is None is left as it was."""
    import math
    import text2music as t
    from expand import read_melody
    src = Path(path)
    notes, _tempo, sig, _first, total = read_melody(src)
    key = t.key_for_file(src) or t.estimate_key(notes)
    bars = max(1, math.ceil(total / t.bar_beats(sig) - 0.01))
    folder = t.ensure_session()
    base = t.plan_for_file(src)
    meta = json.loads((folder / "session.json").read_text()) if (folder / "session.json").exists() else {}
    form = int((meta.get("plan") or {}).get("form", t.DEFAULT_FORM) or t.DEFAULT_FORM)
    theplan = base if base and len(base["chords"]) >= bars else plan.build_plan(bars, sig, key)
    theplan["key"] = tuple(key)
    if progression:
        plan.set_harmony(theplan, progression)
    if modes is not None:
        plan.set_modes(theplan, modes)
    t._write_meta(folder, key=list(key), plan=t.plan_json(theplan, form), form=form)


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
        import lyrics as ly
        if int(r.get("program", 0)) == ly.VOICE_PROGRAM:
            if not r.get("lyrics", "").strip():
                raise RuntimeError("The Voice instrument sings your lyrics: add them with the Lyrics button first.")
            ly.lyric_motifs(r["lyrics"], int(r.get("bars", 4)), ly.VOICE_PROGRAM, r.get("sig", "4/4"),
                            max(40, min(int(r.get("bpm", 80)), 220)), chosen_key(r) or (0, "major"), r.get("llm", "7B"),
                            int(r.get("complexity", 3)), r["prompt"], int(r.get("count", 4)), log=log, on_candidate=candidate_event,
                            voice=r.get("voice", "en"), voices=r.get("voices"), harmony=bool(r.get("harmony")),
                            line_bars=int(r.get("line_bars") or 0))
            send(type="melodies_done")
            return
        make_melodies(
            r["prompt"], int(r.get("bars", 8)), int(r.get("program", 0)),
            r.get("sig", "4/4"), int(r.get("complexity", 3)), r.get("llm", "7B"), int(r.get("count", 4)),
            log=log, on_candidate=candidate_event,
            key=chosen_key(r), mode=chosen_mode(r),
            bpm=max(40, min(int(r.get("bpm", 80)), 220)), resolve=bool(r.get("resolve", True)), phrasing=int(r.get("phrasing", 1)),
            sliders=r.get("sliders"), progression=progression_of(r), modes=modes_of(r),
        )
        send(type="melodies_done")
    elif cmd == "chords":  # the chords of the bars of a melody, for the chord strip
        chords_event(r["source_midi"])
    elif cmd == "set_chords":  # write chords into the session's plan ("progression" text or "bars_chords"), then show them
        prog = progression_of(r)
        if not prog:
            raise RuntimeError("There are no chords to write.")
        write_chords(r["source_midi"], prog)
        chords_event(r["source_midi"])
    elif cmd == "set_modes":  # give some bars a mode of their own ("modes" text like "major*3 lydian", or "bar_modes" list)
        modes = modes_of(r)
        if modes is None:
            modes = []
        write_chords(r["source_midi"], None, modes)
        chords_event(r["source_midi"])
    elif cmd == "expand":  # step 2: add bars to the chosen melody, as several options
        begin()
        import lyrics as ly
        from expand import read_programs
        program = r.get("program")                  # the instrument for the new bars (default: the one the melody ends on)
        if program is None:
            program = read_programs(r["source_midi"])[-1][1]
        program = int(program)
        if program == ly.VOICE_PROGRAM:
            if not r.get("lyrics", "").strip():
                raise RuntimeError("The Voice instrument sings your lyrics: add them with the Lyrics button first.")
            made = ly.lyric_extend(r["source_midi"], r["lyrics"], max(2, min(16, int(r.get("bars", 4)))), r.get("llm", "7B"),
                                   int(r.get("complexity", 3)), r.get("direction", ""), int(r.get("count", 3)), log=log,
                                   on_candidate=candidate_event, voice=r.get("voice", "en"), voices=r.get("voices"),
                                   harmony=bool(r.get("harmony")), line_bars=int(r.get("line_bars") or 0))
            if made is None:
                raise RuntimeError("This melody has already used all the lyrics. Add more lines in the Lyrics box, "
                                   "or write the new bars for another instrument.")
            send(type="melodies_done")
            return
        if r.get("chords"):             # chords for the new bars, to be sung to
            from expand import expand_chords
            expand_chords(r["source_midi"], max(2, min(16, int(r.get("bars", 4)))), program, int(r.get("count", 3)),
                          int(r.get("complexity", 3)), log=log, on_candidate=candidate_event, progression=progression_of(r),
                          modes=modes_of(r))
            send(type="melodies_done")
            return
        expand_melody(
            r["source_midi"], max(2, min(16, int(r.get("bars", 4)))), r.get("engine", "hybrid"), int(r.get("count", 3)),
            r.get("size", "small"), r.get("llm", "7B"), int(r.get("complexity", 3)), r.get("sliders"),
            r.get("direction", ""), bool(r.get("resolve", True)), int(r.get("phrasing", 1)), log=log,
            on_candidate=candidate_event, program=program, progression=progression_of(r), modes=modes_of(r),
        )
        send(type="melodies_done")
    elif cmd == "repeat":  # step 2: copy bars of the melody (with its singing, lyrics and accompaniment) onto the end
        begin()
        from expand import repeat_bars
        repeat_bars(r["source_midi"], r.get("from_bar"), r.get("to_bar"), int(r.get("times", 1)), log=log,
                    on_candidate=candidate_event)
        send(type="melodies_done")
    elif cmd == "sing_part":  # step 4: a sung line over the chosen melody, the next lyric lines in the bars asked for
        begin()
        import lyrics as ly
        if not r.get("lyrics", "").strip():
            raise RuntimeError("Add your lyrics with the Lyrics button first.")
        ly.sing_part(r["source_midi"], r["lyrics"], r.get("start_bar") or None, max(1, min(16, int(r.get("bars", 4)))),
                     r.get("llm", "7B"), int(r.get("complexity", 3)), r.get("direction", ""), int(r.get("count", 3)), log=log,
                     on_candidate=candidate_event, voice=r.get("voice", "en"), voices=r.get("voices"),
                     harmony=bool(r.get("harmony")), line_bars=int(r.get("line_bars") or 0))
        send(type="melodies_done")
    elif cmd == "voice_add":  # a new singer from a recording of a voice singing alone
        import lyrics as ly
        ly.add_voice(r["path"], r["name"], r.get("language", "English"), log=log)
        send(type="voices", items=[{"key": k, "name": n} for k, n in ly.voice_list()])
        send(type="melodies_done")
    elif cmd == "lyrics_check":  # the lyrics box: how many lines and syllables, and any word it cannot pronounce
        import lyrics as ly
        rows = ly.describe(r.get("lyrics", ""), int(r.get("line_bars") or 0))
        lines = [x for x in rows if x["text"]]
        lines_text = []
        for i, x in enumerate(lines, 1):
            where = "?" if x["syllables"] is None else f"{x['syllables']} syl \u2192 {x['bars']} bar(s)" if x["bars"] else f"{x['syllables']} syl"
            short = x["text"] if len(x["text"]) <= 34 else x["text"][:33] + "\u2026"
            lines_text.append(f"{i}. {short}  ({where})" + (f"  \u26a0 {x['note']}" if x["note"] else ""))
        text = f"{len(lines)} line(s), {sum(x['syllables'] or 0 for x in lines)} syllables.\n" + "\n".join(lines_text)
        warned = any(x["note"] for x in lines)
        send(type="lyrics_info", text=text, ok=not warned and bool(lines))
    elif cmd == "lyrics_sing":  # sing the lyric lines the chosen melody was written to
        import lyrics as ly
        ok, why = ly.ready()
        if not ok:
            raise RuntimeError(why)
        wav = ly.sing_midi(r["source_midi"], log=log, voice=r.get("voice", "en"))
        back = ly.backing(r["backing_midi"]) if r.get("backing_midi") else None   # the accompaniment, to play with the voice
        send(type="vocal", path=str(wav), backing=str(back) if back else "")
        send(type="melodies_done")
    elif cmd == "edit":  # step 3: change only the bars the user named, as several options
        begin()
        edit_melody(
            r["source_midi"], r.get("instruction", ""), r.get("bars"), int(r.get("count", 3)), r.get("llm", "7B"),
            int(r.get("complexity", 3)), log=log, on_candidate=candidate_event,
            on_suggestion=lambda sug, targets: send(type="edit_suggestion", bars=sug["bars"], reason=sug["reason"], targets=targets),
            part=r.get("part", "melody"),
        )
        send(type="melodies_done")
    elif cmd == "save_edit":  # step 3, by hand: write the notes as the user left them
        begin()
        save_edit(r["source_midi"], r["notes"], log=log, on_candidate=candidate_event, part=r.get("part", "melody"))
        send(type="melodies_done")
    elif cmd == "add_part":  # stage 2, one instrument at a time: several options for the chosen instrument
        begin()
        add_part_options(
            r["source_midi"], int(r["program"]), int(r.get("count", 3)), r.get("size", "small"),
            bool(r.get("playable", True)), log=log, on_candidate=part_event,
            in_key=bool(r.get("in_key", True)), direction=r.get("direction", ""),
            dynamics=float(r.get("dynamics", 5)),
            texture_style=(r.get("style") or "mix") if r.get("texture") else None,
            from_bar=r.get("from_bar"), to_bar=r.get("to_bar"),
        )
        send(type="parts_done")
    elif cmd == "clear_bars":  # take the accompaniment out of some bars (all instruments, or the programs listed)
        begin()
        src = Path(r["source_midi"])
        tempo, sig, bars = piece_bars(src)
        lo, hi = max(1, int(r.get("from_bar") or 1)), min(bars, int(r.get("to_bar") or bars))
        if lo > hi:
            raise RuntimeError(f"The piece has {bars} bars.")
        bb = bar_beats(sig)
        dest = ensure_session() / "parts" / f"{time.strftime('%H%M%S')}-clear-b{lo}-{hi}.mid"
        dest.parent.mkdir(exist_ok=True)
        n = clear_layers_file(src, dest, (lo - 1) * bb, hi * bb, [int(p) for p in r.get("programs") or []])
        if not n:
            raise RuntimeError(f"There is no accompaniment in bars {lo}-{hi}.")
        log(f"  ▸ Took the accompaniment out of bars {lo}-{hi} ({n} notes).")
        send(type="part_approved", path=str(dest), notes=notes_of(dest), programs=list(programs_of(dest)), layers=layers_of(dest))
    elif cmd == "approve_part":
        dest = approve_part(r["path"], r.get("label", "part"))
        send(type="part_approved", path=str(dest), notes=notes_of(dest), programs=list(programs_of(dest)), layers=layers_of(dest))
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
            dynamics=float(r.get("dynamics", 5)), layered=True,
        )
        send(type="done", melody=str(melody), full=str(full), notes=notes_of(full), programs=list(programs_of(full)), layers=layers_of(full))
        send(type="sessions", items=list_sessions())


def main():
    # where the app's sliders start, so the defaults live in one place (plan.py)
    send(type="ready", sliders=plan.SLIDER_DEFAULTS)
    import lyrics as ly
    send(type="voices", items=[{"key": k, "name": n} for k, n in ly.voice_list()])
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            handle(json.loads(line))
        except Exception as e:
            send(type="error", text=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
