# Composer: code-review handoff

A fully local, text-prompt music composition tool for macOS (Apple silicon, 16 GB). A native SwiftUI app
(**Composer.app**) drives a persistent Python backend over JSON lines. Nothing calls a cloud service.
Install and deployment steps are in `DEPLOY.md`; exact dependency versions are in `requirements.txt`.

This document is written for a reviewer (human or LLM agent) who has not seen the project. It says what exists, how
it fits together, what was verified and how, and where I think the risk is. Section 7 lists specific questions.

- Project root: `/Users/Shared/music-ai` (the Swift app reads `MUSIC_AI_ROOT`, default this path)
- The project root is a git repository (`github.com/anlanglu/music-ai`, branch `main`). `anticipation/` is **vendored**
  (copied in, no nested repo) from upstream commit `af37397`; `review/anticipation_sample.patch` is the exact diff against
  that commit (it applies cleanly and reproduces the vendored folder byte for byte).
- Python: `.venv/bin/python` 3.11 (torch 2.14 with MPS, transformers 5.20, mlx-lm 0.32, mido, music21, verovio; see `requirements.txt`).
- Swift: SwiftPM, Command Line Tools only (no Xcode), Swift 5 mode, no macros (so no `@State`; state is in
  an `ObservableObject`).
- Total size: about 2,800 lines Python (`text2music.py` is 1,237 lines), about 1,450 lines Swift, and a 53-test pytest
  suite in `tests/` (`make test`, about 3 seconds, no model downloads). Section 5 says what it does and does not cover.

## 1. What it does (user-visible workflow)

1. **Stage 1, melody.** The user types a prompt and picks time signature, BPM (default 80), key, bars, melody
   instrument, complexity (1-5), engine, number of options, Phrasing and Melodic shape. The app writes N melody
   options as MIDI files; the user previews them (piano roll or sheet music), regenerates, or picks one.
   An existing MIDI file can be imported instead as the single option.
2. **Stage 2, accompaniment** on the chosen melody, either:
   - *Automatic:* up to 16 instruments at once (Anticipatory Music Transformer, melody as a fixed "control"), or
   - *One at a time:* pick an instrument, optionally type a free-text direction ("answer the melody in the gaps,
     high and cheeky"), get several options, approve one (it is merged into a new `pieceNN-*.mid`) or undo, repeat.
     Piano gets rule-based chord/arpeggio textures that leave the melody untouched.
3. **Sessions.** Everything for one project lives in `output/<date>_<time>_<slug>/` (with `session.json` holding name,
   created, key). Sessions can be named, renamed and reopened.
4. **Notation.** MIDI -> MusicXML (music21) -> verovio SVG shown in a WKWebView, with per-instrument clefs (grand staff for
   piano/harp), key signature from the session key, PDF export (A4 pages via `WKWebView.createPDF` + PDFKit), and a
   `.musicxml` file next to the MIDI for notation apps.

## 2. Architecture

```
Composer.app (SwiftUI)                      .venv/bin/python -u backend.py   (one long-lived process)
  ContentView / ViewModel  --JSON line-->     handle(request) -> text2music.* / notation.render
  Backend.swift (Process)  <--JSON lines--    events: log, candidate, melodies_done, parts_done, ...
```

### File map

| File | Lines | Role |
|---|---|---|
| `backend.py` | 219 | JSON-lines server. Re-points real stdout to the protocol and sends library prints to stderr. `handle(r)` dispatches `cmd`s. |
| `text2music.py` | 1362 | Core pipeline: sessions, LLM melody writing, melody engines, key logic, melody scoring, accompaniment generation, MIDI writing, CLI. Large; most of the logic. |
| `composer.py` | 444 | Rule-based "theory" melody composer (phrase plan, harmony, motif development, contour gestures, rests). |
| `plan.py` | 312 | The musical plan every engine shares: the phrases, the chord of every bar, where each phrase cadences, the motif, and how much of it a melody follows (`form_weights`). |
| `texture.py` | 283 | Rule-based accompaniment patterns for any instrument (block, arpeggio, alberti, oompah, sustained, bassline, walking, pulse, double). |
| `direction.py` | 193 | Free-text direction -> controls. LLM prompt/schema + keyword fallback. |
| `playable.py` | 114 | Per-GM-program range and max-polyphony table, sampler pool builder, `fold_pitch`. |
| `notation.py` | 219 | MIDI -> MusicXML -> verovio HTML/SVG. Clef mapping, key signature, respelling. |
| `variety.py` | 50 | Pitch-movement statistics (scalar runs, leaps, repeats, turns). Used for scoring and measurement. |
| `llmjson.py` | 17 | Tolerant JSON-object extraction from LLM replies, shared by melody and direction parsing. |
| `tests/` | about 830 | pytest suite: composer invariants, key/ending logic, LLM-JSON handling, sampler constraints, the plan and the Form slider, sessions, real-backend protocol round trip, notation, Swift/Python contract. |
| `anticipation/anticipation/sample.py` | (patched) | Vendored Stanford model code with my sampler changes (see 3.1). |
| `MusicAI/Sources/MusicAI/Backend.swift` | 233 | Process management, JSON-line framing, event decoding. |
| `MusicAI/Sources/MusicAI/ViewModel.swift` | 595 | `@MainActor ObservableObject`: all app state, request building, event handling, playback (AVMIDIPlayer + `gs_instruments.dls`). |
| `MusicAI/Sources/MusicAI/ContentView.swift` | 509 | All views (controls, option list, piano roll, sheet view, session bar). |
| `MusicAI/Sources/MusicAI/PDFExport.swift` | 92 | Paged PDF export. |
| `MusicAI/build_app.sh` | 29 | Builds `Composer.app`, ad-hoc codesigns it. Bundle id is still `local.musicai.texttomusic` on purpose (keeps saved preferences). |
| `MusicAI/Icon/make_icon.swift` | 76 | Draws the app icon. |

### Protocol (`backend.py`, header docstring)

Requests (one JSON object per line): `melodies`, `import`, `add_part`, `approve_part`, `orchestrate`, `notation`,
`session_list|new|rename|open`. There is no explicit quit; the process ends when stdin closes. Events: `ready`, `log`, `candidate`, `melodies_done`, `done`, `part_candidate`,
`parts_done`, `part_approved`, `notation`, `notation_error`, `session`, `sessions`, `error`. Notes in events are
`[channel, pitch, start_s, end_s]`. The protocol is untyped on both sides (dict access in Python, hand-decoded in Swift).

Two optional fields ride along on any request: `seed` (an integer, to make the run repeatable) and `knobs` (the
individual musical settings, see 3.3a). The `ready` event carries `presets`, the tables the app's sliders start from.

## 3. The parts worth reading closely

### 3.1 Sampler changes in `anticipation/sample.py` (`review/anticipation_sample.patch`, about 260 added lines)

The stock `generate()` has no notion of meter, instrument range, polyphony or key. I added, all optional and off unless
a `grid` / `instruments` dict is passed:
- **KV cache** across the three tokens of one event (the Markov-window re-relativization means the cache can only be
  reused within an event). Prefill once, feed tokens 2 and 3 incrementally. MPS backend.
- **Metrical grid** (`grid_time_logits`, `grid_dur_logits`): onsets and ends restricted to a sixteenth grid, boosts on
  beats/bars, `max_gap`, `min_gap`, `max_chord`, `min_dur`/`max_dur`, `avoid` (ticks a part must not attack on).
- **Instrument pool steering** (`steer_instruments`, `_pool_mask`): only allowed instruments, per-instrument pitch
  range, `max_poly`, a hard "starved instrument must play next" rule plus a soft balance bonus, scale `pitch_classes`.
  Each constraint falls back to the unconstrained logits if it would mask everything (`isfinite().any()` guard).
- **`melodic_bias`** (new this session): for the single-line melody it adds logit bias against a third same-direction
  step, repeated notes, and shuffling among <=3 pitches; for thirds-to-fifths leaps, and a step back after a leap;
  against jumps over a sixth; and for escaping a narrow recent range. It assumes `pool['allowed'][0]` is the melody
  instrument. Weights are `SHAPE_BIAS` in `text2music.py`.

Constraint fallbacks are tiered, not all-or-nothing: if the model gives no probability to anything the full rules allow,
the least important rule is dropped first (time: `max_gap`, `min_gap`, `avoid`, stacking; duration: max length, then
minimum length, the grid last; notes: polyphony and fair share, then the scale, never the range or the instrument list).
`count_simultaneous` counts the notes already at the current instant while skipping anticipated controls (see 4b).
`_pool_mask` is cached per (instruments, ranges, scale) and rebuilt when they change.

An independent review (second round, below) validated the KV-cache path against the real `music-small-800k` model:
per-token logits match a full forward pass to about 2e-4 with identical argmax, and `generate()` with `grid=None,
instruments=None` returns byte-identical output to upstream under the same seed. I did not repeat that comparison.

Things still worth checking: correctness of the KV-cache indexing (`idx`, `full.shape[1] + i - 1`); that masking fallbacks cannot
silently disable a constraint; per-token numpy work in `melodic_bias` (cost vs. the model forward pass); tokens being in
chronological order when `melodic_bias` reads the history.

### 3.2 Melody engines (`text2music.melody_candidates`)

- `hybrid` (default): the LLM (Qwen2.5 4-bit via MLX; 7B default, 14B optional, 3B for directions) writes a one-bar seed
  as JSON; the Anticipation model continues it for the full length on the grid, monophonic, in key, in the instrument's
  range. Two attempts per seed. Then `conform_melody` (snap to sixteenths, drop overlaps), `snap_to_key`,
  `apply_breaths`, `resolve_ending`.
- `llm`: the LLM writes the whole melody phrase by phrase (`compose()` -> `llm_melody()`), same post-processing.
- `theory`: `composer.compose`, no LLM and no model.

### 3.3 The plan (`plan.py`) and the Form slider

A melody with no shape behind it wanders. `plan.py` puts one there, and the **Form** slider says how much of it the
melody follows. The plan is the same for every engine:

```
{"key": (7, "major"), "bars": 8, "sig": "4/4", "bb": 4.0,
 "chords": [0, 0, 3, 4, 0, 2, 3, 0],                 # the chord of each bar, as a scale degree
 "phrases": [{"index": 0, "start_bar": 0, "bars": 4, "role": "ante",
              "start_beat": 0.0, "end_beat": 16.0, "chords": [0, 0, 3, 4],
              "cadence": [4, 1]}, ...],
 "cadences": [{"beat": 16.0, "role": "ante", "targets": [4, 1]}, ...],
 "boundaries": [(8.0, "idea"), (16.0, "phrase"), ...]}
```

The phrases and the chord templates come from the theory composer (`plan.phrase_plan`, `plan.PROGRESSIONS`), so the
Hybrid engine and the theory composer now articulate the *same* kind of form; `composer.compose` takes the plan's
chords so the two cannot drift apart. The chord tables and the two scales live in `plan.py` now — `composer.py`,
`texture.py` and `text2music.py` all read them from there.

| Form | What it does | How |
|---|---|---|
| 0 Free | nothing: the melody wanders | - |
| 1 Harmony | every bar has a chord, and the melody prefers its notes | `pool['regions']` (a pitch-class region per bar) gives the chord's notes a logit bonus (`_chord_bonus`) |
| 2 Cadences | and every phrase ends on its cadence degree | `plan.resolve_phrase_endings` moves the last note before each phrase's end; the sampler is also given the cadence pitch as a target |
| 3 Full | and the opening idea comes back at the start of every phrase | the motif (the LLM's one-bar seed) is transposed onto each phrase's first chord (`fit_motif_to_chord`) and given to the sampler as targets |

Measured over 24 melodies (`review/bench_plan.py`), Free -> Full: the notes that belong to the chord of their bar
46% -> 80%, phrases that end on their cadence degree 16/24 -> 24/24, notes of the opening idea that come back at the
start of a later phrase 29% -> 67%, and the phrases differ from each other more (1.2 -> 2.3 distinct interval kinds
between the most and least varied phrase). The table is in section 5.

Two design decisions worth checking:

- **The chord is a preference, not a cage.** An earlier version masked the pitch classes outright, which left the
  melody only three pitches to choose from per bar: it stalled and repeated (measured: 3 distinct interval kinds per
  melody). A logit bonus for the chord's notes instead of a hard mask lets passing notes through, and the harmonic fit
  went up rather than down.
- **The motif comes back as a paraphrase.** The sampler is pulled towards the motif's *shape* (a note landing between two
  anchors is pulled towards the line between them), not forced onto its exact notes, so the idea returns with the
  model's own rhythm. Measured with `review/bench_plan.py`; see section 5.

### 3.3a The knobs, and the sliders

Every number behind the two presets is a knob the app can turn (`plan.DEFAULT_KNOBS`):

| Knob | Default | What it does |
|---|---|---|
| `chord` | 2.2 | how strongly the melody prefers the notes of the bar's chord |
| `cadence` | 5.0 | how strongly a phrase is pulled towards the note it ends on |
| `motif` | 8.0 | how strongly the opening idea is pulled back at each phrase's start |
| `sigma` | 1.0 | how wide that pull is, in semitones |
| `tol` | 1.0 | how near a note has to land to be pulled, in beats |
| `run`, `leap`, `repeat`, `variety` | from the shape preset | the melodic bias: scale runs, leaps, repeated notes, shuffling among a few pitches |
| `beat` | 0.5 | how strongly the melody's onsets prefer the beat |

They travel in one request field, `"knobs": {...}`, and only the ones that are set are sent. **Form** and **Melodic
shape** are presets: picking one *sets* the sliders, and moving a slider afterwards keeps that one value when the next
preset is chosen. The tables stay in `plan.py`; the backend sends them to the app in the `ready` event
(`plan.presets()`), so the app does not keep a second copy of them.

Two rules keep this honest:
- **The Form setting decides *whether* a part of the plan is used; the knobs decide *how strongly*.** A knob cannot
  switch on something the setting does not use, so "Free" really is free even though the app always sends every knob.
- **The defaults reproduce the old behaviour exactly** (`tests/test_plan.py` checks this), so nothing changes for
  anyone who does not touch the sliders.

The plan is kept in `session.json` (`plan_json`), so the accompaniment is harmonised *with* the melody instead of
separately: the model-generated parts get the same chord regions, and the rule-based `texture` patterns take the
plan's chords instead of choosing their own (`texture.choose_chords(..., plan_chords=...)`).

### 3.4 Theory composer (`composer.py`)
Phrase plan by length (`_phrase_plan`: ante/cons/cont/final roles) -> chord per bar (templates per role, tonic at end)
-> a motif (rhythm cell + contour) -> per-bar operations (`motif`, `new_idea`, `restate`, `restate_seq`, `seq_up/down`,
`invert`, `vary`, `new`, `cadence`) -> register recentering -> climax -> ornaments on non-"plain" bars -> pitches ->
`breathe` (rests at idea/phrase boundaries, idempotent). Changes made in the last three tasks:
1. **Phrasing** (0 flowing, 1 balanced, 2 spacious): idea-structured plan, rest cells, `breathe`. Applied to all engines
   through `text2music.apply_breaths`.
2. **Melodic shape** (0 stepwise, 1 balanced, 2 angular): `_contour(..., shape)` builds motifs from `_GESTURES`
   (arpeggio, leap-and-step-back, zigzag, ...) with a guard against three same-direction single steps; `_ARCS` gives each
   four-bar phrase a rise/fall/arch/dip by shifting bar start degrees. Shape 0 reproduces the previous contour code.
3. The default for both settings is 1, which changes default output for existing users.

### 3.5 Scoring (`text2music.melody_score`)

Heuristic interestingness score (rhythm variety, entropy, interval variety, range, stepwise share, trigram repetition,
density vs. target, coverage, and a new scalar-run term via `variety.stats`). **It is only displayed; options are not
ranked or filtered by it.** (The one-shot CLI path `hybrid_melody` does pick the best by score.)

### 3.6 Accompaniment

`add_part_options` (one instrument): `direction.parse`/LLM interpretation -> controls (style, density, register,
note length, rhythm feel, dynamics, complementary, adventurous) -> either a rule-based `texture` pattern or an
Anticipation generation with the melody as control and instrument-specific pool/grid -> `make_playable` cleanup (range
folding, legato trimming for monophonic instruments, polyphony cap). `make_music` is the automatic ensemble path.
`interpret_direction` prefers an already-loaded LLM, then 7B, then 3B, then keywords.

## 4. What changed most recently (this and the previous session segment)

- Melody **Phrasing** control and the rest/idea-structure work (composer.py, `apply_breaths`, backend, Swift picker).
- Melody **Melodic shape** control: `melodic_bias` in the sampler, gesture contours and phrase arcs in the composer,
  scalar-run scoring, `variety.py`, backend field `shape`, Swift picker above Phrasing.
- Bug fix: in `text2music.compose`, the enforced `key` was overwritten by the key string the LLM returned, so phrase 2
  crashed with `TypeError: list indices must be integers or slices, not str` (LLM-only engine, key set, more than one
  phrase). Variable renamed to `said_key`.
- Robustness: the hybrid seed step now retries when the LLM returns unusable JSON instead of aborting the batch.
- **Review follow-up fixes (after the first version of this document):**
  - LLM JSON parsing is now tolerant (`_json_with_notes`, `_clean_notes` in `text2music.py`): it tries a parse at every
    `{` (prose, code fences, trailing junk, truncated replies), accepts `[p, s, d]` lists or `{pitch, start, duration}`
    objects, skips bad entries instead of failing, and tolerates a non-numeric tempo.
  - `compose()` (LLM-only engine) retries a phrase up to three times when the reply is unusable, then raises a clear
    `RuntimeError`. The Hybrid seed step catches that too and yields fewer options instead of aborting the batch.
  - The one-shot CLI now routes every engine through `melody_candidates` (before, `--melody-engine theory` was accepted
    but silently ran the LLM path), and `--phrasing` / new `--shape` are honoured.
  - `melodic_bias` takes the melody instrument explicitly (`pool['melodic']['instrument']`) instead of assuming
    `allowed[0]`.
  - The stale `direction.py` docstring now describes the LLM-first design with keyword fallback.
  - `review/drive_backend_shape.py` deletes the session folders it creates.
  - Added `requirements.txt` and `DEPLOY.md`.
- An experiment that was **removed**: a phrase-level register "arc" pull for Hybrid. A weak pull did nothing measurable;
  a strong one cut leaps from 25% to 11% and narrowed the range.

### 4b. Second review round (an independent agent reviewed the code; I checked each claim before acting)

Every item the reviewer put in "ship first" was reproduced or confirmed by reading the code, and fixed:
- **Lost work on reopen.** `backend.restore` only re-emitted `*-opt*.mid` and `*-full.mid`, so approved `pieceNN-*.mid`
  versions were not restored when a session was reopened. It now re-emits the newest of both kinds. (Known limit: if a
  part was approved and then undone in the app, reopening restores the newest file on disk, i.e. the undone version.)
- **Unplayable ending / out-of-range notes.** `snap_to_key` and `resolve_ending` ignored the instrument's range and
  MIDI's 0..127. They now take a `span` and pick the nearest tonic / scale tone the instrument can play. My own sweep:
  15,133 of 138,600 settings combinations (10.9%) broke an invariant before the fix, none after.
- **PDF page size.** WebKit's per-page PDFs were 794 x 1123 pt (about 28 x 40 cm), not A4. `PDFExporter.writeA4` redraws
  each page, still as vectors, onto 595.28 x 841.89 pt. Verified by exporting a real score headlessly and reading the
  MediaBox.
- **Direction note never showed.** The app matched log text from before the LLM direction work ("Following your
  direction"); `direction.describe` now says "Understood: ...", "Matched keywords: ..." or "Couldn't turn ...". The Swift
  list `directionPrefixes` is now checked against `direction.describe` by `tests/test_app_contract.py`.
- **Backend crashes were silent.** stderr went to `/dev/null`, there was no restart and no watchdog. Now stderr is kept in
  `~/Library/Logs/Composer/backend.log`, the exit message includes the last lines of it, there is a "Restart backend"
  button (shown after a crash or after 4 minutes without any message while busy), and old-process output is ignored via a
  generation counter. Checked headlessly with a fake backend that crashes once, then works.
- **`n_same` bug in the sampler** (found by the reviewer, confirmed by measurement): anticipated controls are appended to
  the tail of the token list, and the loop that counted simultaneous notes stopped at the first one, so the stacking cap
  and `min_gap` were silently off. On a real accompaniment run the old count was wrong in 35% of steps. Fixed by
  `count_simultaneous`.
- **Mask fallback granularity**, `max_gap` without an `.any()` guard, `grid['beat']`/`['bar']` required while the boosts used
  `.get`, `_pool_mask` never invalidated, no guard for an empty pool: all fixed as described in 3.1.
- **Swift state bugs:** `sheetLoading`, `pdfJob` and `pdfBusy` were not cleared when an error or crash ended a request
  (buttons stayed locked); `renameSession` lacked the `!busy` guard the other session actions have; `remap` used a raw
  `hasPrefix`, so renaming `x` also moved a sibling folder `x-2`. Fixed.
- **`open_session` containment:** now refuses folders outside `output/`.
- **Direction fallback chain:** an unusable reply from the first model now moves on to the next (7B, then 3B) instead of
  dropping straight to keywords; direction replies use the same tolerant JSON parser as melodies (`llmjson.py`).
- **Reproducibility:** a `seed` field on any backend request and `--seed` on the CLI seed Python, NumPy, PyTorch and MLX.
  The measurement scripts use it. (Repeatability is tested for the theory engine over the protocol; Hybrid with a real LLM
  is not guaranteed bit-identical across runs on MPS.)
- **A bug in my own code found by the new tests:** `melodic_bias` computed the recent moves as
  `zip(hist[-4:], hist[-3:])`, which gives all-zero moves when the history holds exactly two or three notes.
- **A bug in my own review script:** `review/drive_backend_shape.py` sent `engine` instead of `melody_engine`, so its "theory" and "hybrid" rows both ran Hybrid; it now sends the right field and asserts that the engine that ran is the one requested.
- `MUSIC_AI_OUT` overrides the output folder (used by the tests so they never touch real sessions).

Not done from the reviewer's list: a ruff/pyright configuration, and splitting `text2music.py` (a refactor, listed in
section 6). The first part of the review text I was given was cut off, so findings in that part were not seen.

### 4c. Third round: a plan behind the melody (the Form slider)

The complaint that started this: the melodies are complex enough, but the *phrasing* is not clear and the ideas are not
interesting. Both come from the same cause — the Hybrid engine had no shape behind it at all: the LLM wrote one bar and
the model continued it linearly, with rests inserted every two bars and no harmony anywhere. Phrases in music are
delimited by harmonic motion, not by gaps, so a melody with no chords has no cadences and no phrases to speak of.

What was added:
- **`plan.py`** (new): the plan — the phrases, the chord of every bar, where each phrase cadences, the motif. The phrase
  roles and the chord progressions come from the theory composer, so the two engines now articulate the same kind of
  form. The scales and chord tables moved here too, so `composer.py`, `texture.py` and `text2music.py` no longer each
  carry their own copy.
- **The sampler can follow a plan.** Two new controls in `anticipation/sample.py`:
  - `pool['regions']` — a pitch-class region per bar, applied as a *logit bonus* for the chord's notes
    (`_chord_bonus`), not as a mask. A mask was tried first and was much worse: with three pitches available per bar the
    melody stalled (3 distinct interval kinds per melody instead of 7).
  - `pool['melodic']['targets']` — (tick, pitch) anchors the melody is pulled towards (`_target_bias`): the motif at the
    start of each phrase, the cadence at its end. A note landing between two anchors is pulled towards the line between
    them, so the melody follows the idea's *shape* rather than only its exact notes.
- **The Form slider** (0 Free, 1 Harmony, 2 Cadences, 3 Full) in the app, `--form` on the CLI, `form` in the protocol.
  The weights live in `plan.FORM_WEIGHTS`.
- **The accompaniment follows the melody's chords**: the plan is kept in `session.json`, and the model-generated parts
  get the same regions while the rule-based `texture` patterns take the plan's chords instead of choosing their own.
- **Tests** (`tests/test_plan.py`, 22 of them) for the plan, the slider, the motif transposition, the cadence
  resolution and the two new sampler controls.

A bug found while building it: the target pull's strength fell to zero exactly *between* two anchors (it was scaled by
the distance to the nearest anchor), which made the "line between two anchors" interpolation pointless. The strength now
depends on the distance from the anchor *span* instead.

Another: the motif was first snapped to the chord of the first bar, which removed the passing notes that make an idea
worth coming back to (the motif `(G, A, B, C)` became `(G, G, B, B)`). The motif is now kept as the language model wrote
it, in key; the chord preference is a bonus, so it does not have to be one of the chord's notes.

And a bug in the measurement script itself: `bench_plan.py` built a plan to measure against and the melody built
another one, so the chords it scored the melody on were not the chords the melody had followed (the two disagreed
because the plan was built from an unseeded `random.Random()`). Both now use the seeded global random module, the
script passes its plan to the melody, and the numbers are reproducible: running it twice gives the same table.

## 5. Verification: what was done and what was not

**Automated (`make test`; `make test-full` adds the exhaustive sweep).** 84 tests, about 3 seconds, no model downloads:
- `test_invariants.py`: theory-composer melodies, run through the same fold / snap / resolve steps as the app, with the
  instrument's real range. Asserts: no overlaps, MIDI range, instrument range, in key, on the sixteenth grid, fills
  the bars, ends on the tonic. Default 600 random settings; `--full` runs about 138,600 (7 time signatures x 5 levels x
  8 keys x 5 lengths x 11 instruments x 3 phrasings x 3 shapes).
- `test_key_endings.py`: `snap_to_key` / `resolve_ending` at 0 and 127, in narrow ranges, with no tonic available.
- `test_llm_json.py`: messy replies, bounded retries, and a regression test for the enforced-key crash.
- `test_sampler.py`: synthetic-logit tests of the grid, tiered fallbacks, steering, mask invalidation, `count_simultaneous`
  and `melodic_bias`. No model is loaded, so this checks the rules, not how they interact with the real model.
- `test_plan.py`: the plan (the phrases tile the piece, every chord is inside the key, the minor dominant is a major
  triad), the Form slider's weights, the knobs (the defaults, an override, an unknown or unset one, and that the
  defaults reproduce the old behaviour), transposing a motif, resolving a phrase's cadence, and the sampler's two new
  controls: the chord bonus (`_chord_bonus`) and the target pull (`_target_bias`).
- `test_protocol.py` also checks that the `ready` event carries the presets and that a request carrying knobs (or a
  nonsense one) is accepted.
- `test_sessions.py`: containment, rename, duplicate names, approved pieces numbered and restored.
- `test_protocol.py`: the real `backend.py` process over JSON lines (theory engine): melodies, repeatability under a seed,
  a bad path is an error not a crash, create -> approve -> rename -> new -> reopen round trip.
- `test_notation.py`: MusicXML and HTML/SVG, grand staff for piano, paged layout.
- `test_app_contract.py`: Swift `directionPrefixes` vs `direction.describe`; every event `Backend.swift` decodes is listed
  in `backend.py`'s protocol header.
I confirmed that the restore and range tests fail against the old code (and do not hang).

**Measurement scripts in `review/`** (absolute paths inside; they print numbers, they do not assert):
- `measure_hybrid_shape.py`: 24 Hybrid melodies per shape with four fixed seed bars (LLM stubbed for a controlled
  comparison), seeded.
- `drive_backend_shape.py`: drives the real `backend.py` for both engines and prints the same statistics; it asserts the
  requested engine ran and removes the session folders it creates.
- `bench_plan.py`: 24 Hybrid melodies per setting of the Form slider (LLM stubbed, so the only difference between the
  runs is the plan), measuring the harmonic fit, the cadences and whether the opening idea comes back.
Measured effect of the Melodic shape setting (n=24 Hybrid, fixed seeds, shape 0 -> 1 -> 2): moves inside scalar runs
15% -> 8% -> 6%; repeated notes 11% -> 5% -> 3%; direction changes 40% -> 49% -> 55%; range 17 -> 15 -> 13 semitones.
Theory composer, level 4, 60 melodies: scalar runs 15% -> 8% -> 8%. Through the backend (4 melodies each): theory 13% ->
2%, Hybrid 19% -> 5%.
Measured effect of the Form setting (n=24 Hybrid, six seeds, fixed seed bars; Free -> Harmony -> Cadences -> Full):
notes that belong to the chord of their bar 46% -> 84% -> 72% -> 80%; phrases that end on their cadence degree
16/24 -> 20/24 -> 24/24 -> 24/24; notes of the opening idea that come back at the start of a later phrase
14/48 -> 19/60 -> 21/60 -> 32/48; how much the phrases differ from each other (the spread of their interval
vocabularies) 1.2 -> 1.0 -> 1.5 -> 2.3; the heuristic score 9.2 -> 9.7 -> 9.9 -> 10.9.
(The numbers are stable: running the script twice gives the same table.)

The motif comes back as a **paraphrase**, not a literal repeat: the same opening gesture (in the example, G-A-B) with
the model's own rhythm, because the sampler is pulled towards the idea's shape rather than forced onto its notes. The
pull's width matters a lot: measured over the same 24 melodies, a wide pull (1.8 semitones) brought 23 of 36 motif
notes back and a narrow one (1.0 semitone) brought 31 — the model's own preference wins against a diffuse pull.
`plan.FORM_WEIGHTS` and `text2music.PLAN_TARGET_SIGMA` are where those numbers live.

**Checked by hand (not automated):** the PDF page size (a headless export of a real score, MediaBox read back), the Swift
backend crash/restart behaviour (a fake backend, headless), the app builds.

**Not verified:**
- **Sound quality.** Nobody (including me) has listened to the new output. The metrics are proxies.
- **The GUI.** I cannot click or screenshot the app. The new Restart button, the direction note and the pickers have not
  been seen on screen; there are no Swift unit tests, and no CI. The **Form** slider in particular has not been heard.
- **The Form slider's weights were tuned on statistical proxies, not by ear.** The numbers in section 5 are the best
  evidence available here; whether a narrow pull on the motif sounds like a returning idea or a mechanical copy is a
  listening question.
- Hybrid with the real LLM and real accompaniment generation are covered only by manual runs, not by the suite.
- Sample sizes in the measurements are small, so the weights in `SHAPE_BIAS` and `plan.FORM_WEIGHTS` were tuned on thin data.

## 6. Known issues and risks (my own list)

1. ~~LLM JSON handling is brittle~~ **Fixed** (see section 4). Remaining: a model that keeps returning bad JSON still
   ends in an error message after three tries; there is no constrained decoding or JSON-schema enforcement.
2. **Module-level mutable state** in `text2music.py`: current session, a one-entry LLM cache (`_llm`), the Anticipation
   model cache. The backend is single-threaded and handles one request at a time; there is no locking and cancellation is
   not supported.
3. **Memory pressure.** On 16 GB, 7B (about 4.5 GB) plus the Anticipation model plus other apps has produced transient
   Metal "Insufficient Memory" errors. There is a 3B fallback for directions only.
4. **`text2music.py` is a 1,237-line monolith** mixing sessions, LLM prompting, key theory, scoring, MIDI I/O, accompaniment
   and CLI. `composer.py`, `texture.py` and `text2music.py` each carry their own scale/chord/key helpers.
5. ~~Stale `direction.py` docstring~~ **Fixed.**
6. ~~Dead CLI flag~~ **Fixed.** The CLI now honours `--phrasing` and `--shape` and supports `--melody-engine theory`.
7. **`resolve_ending` and `snap_to_key`** modify notes after generation (final note -> tonic; out-of-scale notes moved to the
   nearest scale tone). Both can flatten what the sampler or composer intended, and snapping can create repeated notes.
   They are now range-aware (see 4b); when an instrument's range holds no tonic, the last note is left as it is.
8. **Hybrid cannot produce deliberate motifs.** Phrasing only inserts rests; shape only biases pitch choice. This is a
   limitation of the approach, not a bug.
9. **Defaults changed:** Phrasing = Balanced and Melodic shape = Balanced alter output for anyone expecting the old behaviour
   (Flowing + Stepwise reproduce it for the theory composer; Hybrid's sampler is otherwise unchanged at shape 0).
10. **Hard-coded location:** default project root is `/Users/Shared/music-ai`; output lives inside the project folder
    (`output/`), including user work and a `.tmp` scratch folder.
11. **Distribution:** ad-hoc signed, bundle id retained from the original name, no notarization, expects `.venv` next to it.
12. `melodic_bias` reads history from token order (assumed chronological; the reviewer's check agrees). The melody
    instrument is explicit (`instrument` in the bias dict).
13. **Deployment is not self-contained:** `Composer.app` needs the sibling `.venv` and the Hugging Face cache; see
    `DEPLOY.md`. `requirements.txt` was generated from the working environment, and a clean-machine install following
    `DEPLOY.md` has **not** been tried end to end (only `uv pip install --dry-run` against the existing venv).

14. **Undo then reopen** restores the newest piece file on disk, not the version the user had undone back to.
15. **No cancellation.** A running generation cannot be stopped from the app; the new watchdog only offers a restart.
16. **Seeds** make the theory engine repeatable; Hybrid and accompaniment runs on MPS are seeded but may still differ
    slightly between runs.
17. Style tooling is not configured (no ruff / pyright / swift-format).

## 7. Suggested review focus

Highest value first:
1. `anticipation/sample.py` diff: logic errors in the KV cache path, masking fallbacks, `melodic_bias` indexing and
   dtype/device handling (MPS), and any change in behaviour when `grid`/`instruments` are `None` (it should match upstream).
2. `text2music.py`: `melody_candidates` (all three engines), `compose`/`llm_melody` error paths, `add_part_options`,
   `make_playable`, and session functions (path handling, renames, `drop_if_empty`, anything that could delete user files).
3. `text2music._json_with_notes` / `_clean_notes` / `compose` retry path (new), then `backend.py`: stdout redirection, error reporting (the app shows only the message text, with no traceback), request
   validation (`int(r.get(...))` on untrusted-ish fields), behaviour on a bad request mid-stream.
4. `composer.py`: edge cases (very short pieces, 1-2 bars, compound meters, 5/4 and 7/8), `breathe` invariants,
   integer vs. float scale-degree arithmetic, `_contour` termination and run-breaking logic.
5. Swift: `Backend.swift` framing (partial lines, process death, stderr handling), `ViewModel` request building and event
   handling on the main actor, retain cycles, `@Published` update storms from `candidate` events, PDF export paging.
6. `tests/`: are the invariants the right ones, and what would you add (Swift tests for `ViewModel` and `Backend`, a
   model-backed sampler test marked slow)?

Specific questions:
- Is there any path where a constraint (key, range, grid, tonic ending) can be violated in the final MIDI?
- Can `melodic_bias` (or any steering) produce `-inf` everywhere or NaN, and what does `torch.multinomial` do then?
- Are there user-data-loss risks in session rename/open/new or in the `.tmp` cleanup?
- Are the Swift and Python halves of the protocol in agreement on optional fields and defaults (`phrasing`, `shape`, `key`,
  `bpm`, `resolve`, `llm_directions`)?
- Where would you split `text2music.py` first?

### 4d. Menu clean-up (after the third round)

The app's Form slider and Melodic shape picker were presets over the same numbers the Advanced sliders set, so the same
idea was controlled twice and a "which slider did the user move" memory reconciled them. They are gone from the app:
the Melody section has one **Melodic shape** slider (the stepwise/balanced/angular presets blended, `plan.shape_amount_knobs`),
**Sit on the beat**, **Follow the chords**, **Land on the cadence**, **Bring the idea back**, **Phrasing**, the Anticipation model
and the melody LLM. The accompaniment settings moved to **Generation settings** in the Accompaniment section. The backend
derives how much of the plan is in use from the three strengths (`plan.form_from_knobs`); `form`, `shape` and `knobs` still work
for the CLI and old clients. `sigma` and `tol` are no longer reachable from the app (defaults). References to "the Form slider"
above describe the state before this change.
