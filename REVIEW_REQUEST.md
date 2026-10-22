# Code review request: Composer, melody redesign and everything after it

**For the reviewing agent.** This document says what changed, why, where the risk is, and what was and was not verified.
Please review the work critically. Do not assume anything here is correct because it says so: the "Claims to verify"
section lists what to check against the code.

- **Repo:** `/Users/Shared/music-ai` (remote `git@github.com:anlanglu/music-ai.git`, branch `main`)
- **Range to review:** `git diff 9f2b258..HEAD` (about 28 files, +2300 / −1400). Work up to `9fa3971` was already pushed; the
  commit that adds this document also contains a small `mixer.py` fix and its test (section 7).
- **Ground rules:** read-only review. Do not push, do not force-push, do not touch anything under `output/` (it holds the
  user's own sessions), do not delete files. If you want to run a real-model check, make a throwaway session folder
  and delete it afterwards. Report findings; do not edit code unless asked.
- **Pronouns:** the user's pronouns are unknown. Use "they/them" if you must refer to them.

## 1. What the project is

**Composer** is a fully local (no cloud) text-prompt music tool for macOS. A native SwiftUI app (`Composer.app`, built with
`make app`) drives a persistent Python backend (`backend.py`) over JSON lines on stdin/stdout. Python 3.11 venv in `.venv`.

| Piece | Role |
|---|---|
| Qwen2.5 7B / 14B / 3B (MLX) | the language model that writes melodies and picks keys |
| Anticipation music-small / medium / large | the music model that writes accompaniment (and the Hybrid melody continuation) |
| music21 + Verovio | MIDI → MusicXML → engraved sheet music (shown in a WKWebView) |
| mido | all MIDI reading and writing |

Run: `make test` (fast, uses a stub LLM; one test uses the real Anticipation model if cached), `make test-full`, `make app`.
**Current result: 115 tests pass.** The app was rebuilt from the same tree.

Layout (Python): `text2music.py` (pipeline, LLM prompts, key logic, melody finishing, accompaniment, CLI), `plan.py` (phrases,
chords, modes, slider scaling), `expand.py` (step 2), `dynamics.py` and `mixer.py` (new), `notation.py`, `texture.py`
(rule-based piano parts), `direction.py` (free-text direction → controls, built for accompaniment), `backend.py` (protocol).
Swift: `MusicAI/Sources/MusicAI/{Backend,ViewModel,ContentView}.swift`.

## 2. The workflow the app now has

1. **Melody.** Written entirely by the LLM, a phrase (2 or 4 bars) at a time. No Anticipation, no rule-based composer.
2. **Expand melody (optional, repeatable).** Adds 2–16 bars to the chosen melody as several options. Engine: *Hybrid*
   (Anticipation continues the last ≤ 8 bars, steered by the plan) or *LLM only*. Each expansion has its own settings and a
   free-text direction. Only offered before any accompaniment exists. Undo steps back.
3. **Accompaniment.** Anticipation only: automatic, or one instrument at a time.

## 3. Change summary (chronological, with commits)

### 3.1 Menu clean-up and plain-language sliders (`cdb8f05` … `434bf0d`)
- The Form and Shape presets were merged into sliders, all 0–10 and renamed: Smooth ↔ Jumpy, Steady pulse, Fit the harmony,
  Phrase endings, Recurring theme. `plan.from_sliders` / `plan.clean_sliders` are the single place that scales them.
- The "Generation settings" group (top-p, retries, min notes/s, enforce meter, LLM directions) became constants in
  `text2music.py` (`TOP_P`, `ATTEMPTS`, `MIN_DENSITY`, `BEAT_BOOST`); matching backend fields and CLI flags were removed.
- Logging: lines starting `▸` say how the prompt was used; the LLM returns a `reading` field; `_short()` limit raised 260 → 900.

### 3.2 Melody redesign and Expand step (`5f3d743`, merged in `46bef72`)
- `composer.py` (theory composer) and three `review/` scripts were deleted. `breathe` moved to `text2music.apply_breaths`.
- `llm_melody` now takes `guidance` (the sliders said in words) and returns `(tempo, notes, plan, key, reading)`.
  New: `pick_key` (a short LLM call for the key), `explain_prompt`, `finish_melody`, `best_melody`, `guidance_text`.
- `expand.py` (new): `read_melody`, `expand_melody`, `_hybrid_section`, `_direction_for_sampler`. Old notes are preserved except
  the last may be shortened. `plan.extend` grows the plan and the whole plan is written to `session.json` on the first option.
- Backend: new `expand` command reusing the `candidate` event; `melodies` takes `sliders`.
- Swift: Melody section lost its engine picker; new `expandControls`; `expansionHistory`/`undoExpansion`.
- Tests: `tests/stub_backend.py` (the real backend with a deterministic stand-in LLM) and `tests/test_expand.py`.

### 3.3 Notation clefs (`18ec445`)
Horn: treble, bass clef when the mean sounding pitch < 50. Trombone/bassoon/cello: tenor clef for the upper range. Piccolo:
8va treble; glockenspiel: treble with `octaveChange = 2`. Accordion moved to the grand staff.

### 3.4 Sessions (`676a21f`)
Default session name is `Session YYYY-MM-DD HH:mm`. The folder is now just `slugify(name)` (no date prefix), `-2`, `-3` on
collision, and **renaming the session renames the folder** (`rename_session`). Session list sorts by `created`, not by folder
name. Legacy dated folders are untouched until renamed.

### 3.5 Sliders (several commits)
- **Avoid scale runs** returned as its own slider (`run` knob = `runs * 0.3`; LLM caps runs at 3/4/5 notes).
- **Auto** on every character slider: the value is sent as the string `"auto"`; `clean_sliders` keeps the default *number*
  for the sampler and lists the name in `s["auto"]`, so `guidance_text` gives the LLM no sentence for it.
- **All 0–10 sliders are whole numbers** (`round()` in `clean_sliders`, `step: 1` in Swift). Old defaults 1.67/2.75/3.33 became
  2/3/3, and the mappings were rescaled so the *default* knobs are unchanged (chord 2.2, cadence 5.0, beat 0.5). The top of
  each range now reaches slightly different strengths.
- Tooltips: help is attached to the label row and the slider, with a `contentShape` so the empty gap is hoverable.

### 3.6 Melody and Expand share one layout (`94f7c9a`)
`melodyControls` and `expandControls` use the same helpers (`describeBox`, `barsRow`, `complexityRow`, `optionsRow`,
`characterControls`, `llmPicker`). Expand's bars is a 2–16 slider (was a 4/8 picker); the backend clamps to 2–16. Expand's
settings are initialised from the melody's by `copySettingsToExpansion()` each time melodies finish generating.
`_hybrid_section` now shrinks the context window so context + added bars stays under the Anticipation clock (≈100 s).

### 3.7 Accompaniment preview bug (`e885e63`)
After one-at-a-time mode, an Automatic accompaniment left `partOptions` populated and the main view kept showing them.
`addAccompaniment` now calls `resetParts()`; switching to Automatic drops un-approved options.

### 3.8 History pane (`48da85d`)
`HistoryEntry` holds a `Snapshot` of everything the results area shows (candidates, selection, orchestration, part options,
approved parts, undo stacks). Entries are recorded on: melodies done, expansion done, accompaniment done, part options done,
part approved, undo. Clicking restores a snapshot and **appends nothing**; later actions append after the existing entries
(nothing is discarded). Selection changes refresh the *current* entry's snapshot. History is **in memory only**.

### 3.9 Directions that were being ignored (`e7eff4c`)
Diagnosis (from code reading, not measured): the direction was one line near the end of a long prompt, repeated identically for
every chunk, competing with the original prompt, the slider sentences and a hard-coded "build to the climax" line; the
retry loop also demanded ≥ 70 % of the complexity-implied note count. Hybrid only mapped density / register / rhythm /
adventurousness, and logged "Understood: …intensity and volume" for things it never applied.
Fix: `compose()` puts an `INSTRUCTION FROM THE USER …` block first, tells each chunk "part i of n", passes `climax=False` to
`llm_melody` when a direction exists, halves the note target when `direction.parse` reads a sparse direction. `expand.py`
logs what Hybrid will and cannot do (`_hybrid_reading`).

### 3.10 Modes (`6c920c3`, `238afc3`, `0ae9b6e`)
- `plan.MODES` holds seven scales (major, minor, dorian, phrygian, lydian, mixolydian, locrian). `deg_scale`, `key_scale`,
  `mode_family`, `FLAVOR`, `MODAL_PROGRESSIONS`, `PARENT_MAJOR_BELOW`, `is_modal` are new. Allowed notes are derived from this
  table, not from the LLM; `text2music.scale_names(key)` lists them and is shown to the LLM and in the log.
- **Key** (home note only) and **Mode** are now two pickers; `backend.chosen_key` / `chosen_mode` resolve "auto".
- `parse_key` accepts mode names and prefers a modal match over major/minor; `parse_mode` finds a mode named without a note
  ("in lydian mode"). In `melody_candidates` the order is: picker mode → mode named in the prompt → the key's own mode.
- **Modal flavor** slider: LLM sentence (> 2.5, stronger > 7.5) plus `plan.flavor_notes`, which moves neighbouring notes onto the
  characteristic note (up to ≈ 1 note in 6) — never the first or last note, never two adjacent.
- Chords: `chord_progression(role, length, rng, mode)` uses `MODAL_PROGRESSIONS`; `chord_name` reports "diminished".
- `texture.py` uses `deg_scale` / `key_pcs` / `mode_family`.

### 3.11 Scale strictness (`ed7ded6`)
`limit_outside(notes, key, strict, span)` replaces the unconditional `snap_to_key` in `finish_melody`: strict 10 = old behaviour;
lower values keep up to 25 % of notes outside the scale (at 0), choosing short, off-beat, non-first/last notes. Hybrid only
restricts the sampler's pitch classes when strict ≥ 6. `outside_share` reports the actual share in the log.

### 3.12 Key signature for modes (`c1233bb`)
`notation._sharps`: a mode uses the signature of its tonic's major or minor key (by `mode_family`), and the notes that make it a
mode are accidentals (D Lydian = D major + G♯). Previously it used the relative major (D Lydian showed A major).
Pitch-class 1 uses D♭ major, pitch-class 8 minor uses G♯ minor, as plain major/minor already did.

### 3.13 Dynamics and mixer (`72e8d78`, `9fa3971`)
- **Root cause found:** `anticipation.convert.events_to_midi` writes every note at velocity 96 and the model discards input
  velocities, so adding an accompaniment flattened the melody's dynamics.
- `dynamics.finish(path, source_path, sig, amount)`: notes already in the source (matched by program, pitch, onset ±2 ticks)
  get their old velocity back; new notes get `melody_mean + role_offset(program)` shaped by the melody's local loudness (±3 beats),
  beat/bar accents (not for sustained sounds) and a deterministic wobble; `amount` 0–10 scales the shaping. It is called after
  `retime` in `make_music`, `add_part_options` and `texture_options`.
- `melody_velocities(notes, sig, depth)` gained `depth` and a per-phrase swell; `write_melody_midi(..., depth=)`.
- `apply_velocity` (the direction's soft/loud) now shifts the mean instead of flattening.
- `mixer.py`: `read_mix` / `set_volume` write MIDI volume (CC 7) at the start of a channel's track, replacing any existing one and
  carrying delta times so no other event moves. Backend commands `mix_info` and `set_volume` reply with a `mix` event.
- Swift: `MixChannel`, `.mix` event, `loadMix` / `setVolume` (0.25 s debounce) / `gotMix`, `play(_, from:)` so playback resumes
  at the same position, a Mixer disclosure under the transport, a Dynamics slider (Melody, Expand, Accompaniment).
- Notation: `_mark_dynamics` prints a marking where the loudness settles at a new level (≥ 2 bars apart, one bar of look-ahead) and a
  hairpin between markings; upper staff only on a grand staff; `_split_hands` now copies `volume.velocity`.

## 4. Design decisions the reviewer may want to challenge

1. **Auto = "don't instruct the LLM; the engine uses its usual number."** It does not mean "infer from the prompt".
2. **Strict ↔ Relaxed default is 10** (the old behaviour) so nothing changed by default.
3. **Locrian was added late** after the user asked why there were six. Its tonic chord is diminished; the plan avoids it.
4. **Mode signatures use parallel major/minor + accidentals** (user's explicit request), not the relative major.
5. **Volume is MIDI CC 7 stored in the file, edited in place.** Alternatives were a sidecar file or scaling velocities.
   In-place edits also change what an old History snapshot sounds like (same file).
6. **History is in memory.** Persisting it with the session was offered and not done.
7. **Dynamics for new accompaniment notes sit below the melody on average, not strictly below every melody note.**

## 5. Where I would look hardest (ranked)

1. **`plan.clean_sliders` now returns an extra `"auto"` key** (a list). Anything that iterates the returned dict, JSON-dumps it,
   or does arithmetic over all values would break. Check every consumer: `from_sliders`, `finish_melody`, `compose`,
   `guidance_text`, `make_melodies` meta writing, `expand.py`, tests.
2. **`dynamics.finish` note matching.** Key is `(program, pitch, tick ± 2)`. Check: duplicated notes (a piano melody with a piano
   part on the same pitch and tick inherits the melody velocity); drums (`channel == 9` → program 128); `ticks_per_beat`
   scaling; `bisect` on `(tick - 2, 0)` tuples; a source with no notes (returns 0); very long files (the per-note `local()` scan).
3. **`mixer.set_volume` track rewrite.** Deleting an existing CC 7 moves its delta time onto the next message; inserting the new
   one after the leading meta events. Check multi-track files that share a channel, a track whose first event is not meta,
   and `end_of_track` handling. (A sysex crash was found and fixed during this write-up, see section 7.)
4. **Swift state in `ViewModel.swift`**, which has no unit tests: `Snapshot` / `goBack` / `refreshCurrentEntry`,
   `expandJustRan` handling on error, `copySettingsToExpansion` being called on every melody completion (does it clobber
   settings the user changed for step 2 after Regenerate?), `gotMix` / `resumeAt` races when volume changes while playback
   stops, `sliderValues` typed `[String: Any]` being serialised.
5. **`text2music.compose` direction handling.** The prompt string is rebuilt each attempt inside the retry loop; `sparse` uses
   `direction.parse` (a keyword vocabulary built for accompaniment); `climax=not extra` must leave Melody step 1 (which passes no
   `extra`) exactly as before, so check that step 1's prompt is unchanged.
6. **`plan.flavor_notes`** neighbour test (`between` list over `range(min+1, max)` using the mode's pitch classes) and the
   `rank` key. Order in `finish_melody`: limit_outside → flavor_notes → apply_breaths → resolve_phrase_endings → resolve_ending.
   Confirm flavor cannot undo a cadence or the final tonic.
7. **`parse_key` / `parse_mode` regexes** use `re.I`. A lowercase letter followed by a mode word can false-positive
   ("e dorian"); "a" is excluded only when it has no accidental. Negations ("not lydian") are read as the mode.
8. **`limit_outside`** keep-set is chosen over *indices*, so it assumes `snap_to_key` returns a same-length list (true today).
9. **`_hybrid_section` context sizing**: `room = int(95 * tempo / 60 / bb) - add_bars` can go ≤ 0 for a long addition at a slow
   tempo; `max(1, …)` then makes the sequence longer than the model's clock. Not run with the real model at 16 bars.
10. **`notation._sharps` / `_respell`** for modes in extreme keys; double sharps/flats are not produced (the plain equivalent note
    is written), and the two pitch classes with enharmonic choices (C♯/D♭, A♭/G♯).
11. **Session rename edge cases**: `rename_session`'s "already called that" regex (`slug-\d+`), two sessions with one name,
    renaming to a name whose slug equals another existing folder.

## 6. Claims to verify (each should be checkable from the code or by running it)

- [ ] `make test` passes (115) and no test needs network or a model download except the optional Anticipation one.
- [ ] The Melody step never calls Anticipation (`grep -n "melody-engine\|Anticipation" text2music.py` around `melody_candidates`).
- [ ] After adding an accompaniment, the melody's note velocities in the `-full.mid` equal those in the source (`test_dynamics`).
- [ ] `set_volume` changes no note, velocity or event time (`test_dynamics::test_volume_is_set_in_the_file_without_touching_the_notes`).
- [ ] An automatic slider produces no sentence in `guidance_text` and still yields a number in `clean_sliders`.
- [ ] `clean_sliders({...})` output fed back into `clean_sliders` keeps the `auto` list.
- [ ] Default knobs are numerically unchanged by the integer-slider rescale (`test_plan` checks chord 2.2, cadence 5.0, beat 0.5).
- [ ] All 84 key × mode signatures follow the rule in 3.12 (see the snippet in section 8).
- [ ] No code path writes into `output/` folders other than the current session's.
- [ ] Nothing in the Swift app sends a slider as a non-number except the string `"auto"`.

## 7. Verified, unverified and known limits

**Verified (by running):**
- 115 Python tests. A real 7B + Anticipation-small run (4-bar 3/4 melody → 9 instruments): melody velocities restored, accompaniment
  means 72–83 against a melody mean of 84, spread 65–88.
- Real 7B runs: shape slider effect measured (leaps 8 % / 20 % / 24 %, direction turns 28 % / 31 % / 47 % at Smooth / Balanced /
  Jumpy, 5 melodies each); "G lydian" melody used exactly G A B C♯ D E F♯ with C♯ in 3 of 31 notes; D Dorian at strict 2 gave
  19 % outside notes against an announced allowance of 20 %.
- Rendered MusicXML and Verovio output for D Lydian / Dorian / Phrygian, B♭ Dorian, F♯ Phrygian, E♭ Lydian, A Mixolydian,
  B Locrian, C♯ Lydian (signature, spelled notes, printed accidentals), and dynamics markings/hairpins on flute and piano scores.
- Fix made while writing this document: `mixer.read_mix` / `set_volume` raised `AttributeError` on a file containing a system-exclusive
  message (no `.channel`). Fixed with `hasattr(m, "channel")` and a regression test.

**Not verified:**
- **Nothing in the Swift UI has been clicked through** by me: layout of the shared Melody/Expand sections, the History pane, Auto
  checkboxes, the Mixer, tooltips (macOS only shows them after a pause and when the window is active), the Mode picker.
- How anything *sounds*. No listening test of expansions, modal melodies, relaxed scales, dynamics or the mixer.
- Whether the new direction prompt makes the 7B follow instructions better. The diagnosis was from reading code; no before/after
  measurement. Hybrid still cannot follow most expressive directions.
- A 16-bar Hybrid expansion at a slow tempo with the real model; expansion after many rounds (drift from the opening).
- Whether the History pane stays coherent after a backend restart or a session switch mid-generation.
- The accuracy of the dynamics marking thresholds on imported MIDI with other velocity ranges.

**Known limits (by design or left open):**
- Volume edits are lost when a new accompaniment file is written (it starts at the default level).
- Going back to a pre-expansion melody in History leaves the session's plan longer than the melody (expected to be harmless;
  `plan.extend` cuts it back, accompaniment effect unchecked).
- `round()` in `clean_sliders` is banker's rounding on `.5` (only reachable from the CLI with floats).
- Hybrid's direction vocabulary is only density / register / beat emphasis / adventurousness.
- The flute sits near its top register (7 of 9 notes above A5 in an early sample); a narrower range was offered and not done.

## 8. How to check things quickly

```bash
cd /Users/Shared/music-ai
make test                                   # 115 tests, ~15 s
git log --oneline 9f2b258..HEAD             # the commits in scope
git diff 9f2b258..HEAD --stat

# key signature rule, all 84 combinations (differences only at the C#/Db and Ab/G# enharmonic choices)
.venv/bin/python - <<'PY'
import notation, plan
from music21 import key as k21
names = ["C","C#","D","E-","E","F","F#","G","A-","A","B-","B"]
for tonic in range(12):
    for mode in plan.MODE_ORDER:
        fam = plan.mode_family(mode)
        exp = k21.Key(names[tonic], fam).sharps
        got = notation._sharps((tonic, mode))
        if got != exp: print(names[tonic], mode, got, exp)
PY

# real-model smoke test (writes to output/; use a throwaway session name and delete it afterwards)
.venv/bin/python text2music.py "a dreamy tune in G lydian" --bars 4 --melody-only --candidates 1 --session zz-review
rm -rf output/zz-review
```

## 9. What I would like back

A list of findings, most severe first. For each: **file:line**, what is wrong, a concrete input or state that triggers it, and a
suggested fix. Please separate (a) bugs, (b) behaviour that contradicts section 3 or 6, (c) missing tests, (d) risky design
choices from section 4, and (e) style/simplification notes. Say explicitly which findings you reproduced by running code and
which come from reading only.
