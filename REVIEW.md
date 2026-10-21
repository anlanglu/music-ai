# Composer: code-review handoff

A fully local, text-prompt music composition tool for macOS (Apple silicon, 16 GB). A native SwiftUI app
(**Composer.app**) drives a persistent Python backend over JSON lines. Nothing calls a cloud service.
Install and deployment steps are in `DEPLOY.md`; exact dependency versions are in `requirements.txt`.

This document is written for a reviewer (human or LLM agent) who has not seen the project. It says what exists, how
it fits together, what was verified and how, and where I think the risk is. Section 7 lists specific questions.

- Project root: `/Users/Shared/music-ai` (the Swift app reads `MUSIC_AI_ROOT`, default this path)
- **The project root is not a git repository.** There is no diff to read. The only git repo is the vendored
  `anticipation/`, where I modified `anticipation/sample.py`; that diff is in `review/anticipation_sample.patch`.
- Python: `.venv/bin/python` 3.11 (torch 2.14 with MPS, transformers 5.20, mlx-lm 0.32, mido, music21, verovio; see `requirements.txt`).
- Swift: SwiftPM, Command Line Tools only (no Xcode), Swift 5 mode, no macros (so no `@State`; state is in
  an `ObservableObject`).
- Total size: about 2,800 lines Python, about 1,400 lines Swift. There are **no automated tests in the repo**; see
  section 5 for what the `review/` scripts do.

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
| `backend.py` | 213 | JSON-lines server. Re-points real stdout to the protocol and sends library prints to stderr. `handle(r)` dispatches `cmd`s. |
| `text2music.py` | 1174 | Core pipeline: sessions, LLM melody writing, melody engines, key logic, melody scoring, accompaniment generation, MIDI writing, CLI. Large; most of the logic. |
| `composer.py` | 447 | Rule-based "theory" melody composer (phrase plan, harmony, motif development, contour gestures, rests). |
| `texture.py` | 273 | Rule-based accompaniment patterns for any instrument (block, arpeggio, alberti, oompah, sustained, bassline, walking, pulse, double). |
| `direction.py` | 193 | Free-text direction -> controls. LLM prompt/schema + keyword fallback. |
| `playable.py` | 114 | Per-GM-program range and max-polyphony table, sampler pool builder, `fold_pitch`. |
| `notation.py` | 219 | MIDI -> MusicXML -> verovio HTML/SVG. Clef mapping, key signature, respelling. |
| `variety.py` | 50 | Pitch-movement statistics (scalar runs, leaps, repeats, turns). Used for scoring and measurement. |
| `anticipation/anticipation/sample.py` | (patched) | Vendored Stanford model code with my sampler changes (see 3.1). |
| `MusicAI/Sources/MusicAI/Backend.swift` | 177 | Process management, JSON-line framing, event decoding. |
| `MusicAI/Sources/MusicAI/ViewModel.swift` | 546 | `@MainActor ObservableObject`: all app state, request building, event handling, playback (AVMIDIPlayer + `gs_instruments.dls`). |
| `MusicAI/Sources/MusicAI/ContentView.swift` | 503 | All views (controls, option list, piano roll, sheet view, session bar). |
| `MusicAI/Sources/MusicAI/PDFExport.swift` | 72 | Paged PDF export. |
| `MusicAI/build_app.sh` | 29 | Builds `Composer.app`, ad-hoc codesigns it. Bundle id is still `local.musicai.texttomusic` on purpose (keeps saved preferences). |
| `MusicAI/Icon/make_icon.swift` | 76 | Draws the app icon. |

### Protocol (`backend.py`, header docstring)

Requests (one JSON object per line): `melodies`, `import`, `add_part`, `approve_part`, `orchestrate`, `notation`,
`session_list|new|rename|open`. There is no explicit quit; the process ends when stdin closes. Events: `ready`, `log`, `candidate`, `melodies_done`, `done`, `part_candidate`,
`parts_done`, `part_approved`, `notation`, `notation_error`, `session`, `sessions`, `error`. Notes in events are
`[channel, pitch, start_s, end_s]`. The protocol is untyped on both sides (dict access in Python, hand-decoded in Swift).

## 3. The parts worth reading closely

### 3.1 Sampler changes in `anticipation/sample.py` (`review/anticipation_sample.patch`, ~200 added lines)

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

Things to check: correctness of the KV-cache indexing (`idx`, `full.shape[1] + i - 1`); that masking fallbacks cannot
silently disable a constraint; per-token numpy work in `melodic_bias` (cost vs. the model forward pass); tokens being in
chronological order when `melodic_bias` reads the history.

### 3.2 Melody engines (`text2music.melody_candidates`)

- `hybrid` (default): the LLM (Qwen2.5 4-bit via MLX; 7B default, 14B optional, 3B for directions) writes a one-bar seed
  as JSON; the Anticipation model continues it for the full length on the grid, monophonic, in key, in the instrument's
  range. Two attempts per seed. Then `conform_melody` (snap to sixteenths, drop overlaps), `snap_to_key`,
  `apply_breaths`, `resolve_ending`.
- `llm`: the LLM writes the whole melody phrase by phrase (`compose()` -> `llm_melody()`), same post-processing.
- `theory`: `composer.compose`, no LLM and no model.

### 3.3 Theory composer (`composer.py`)

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

### 3.4 Scoring (`text2music.melody_score`)

Heuristic interestingness score (rhythm variety, entropy, interval variety, range, stepwise share, trigram repetition,
density vs. target, coverage, and a new scalar-run term via `variety.stats`). **It is only displayed; options are not
ranked or filtered by it.** (The one-shot CLI path `hybrid_melody` does pick the best by score.)

### 3.5 Accompaniment

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

## 5. Verification: what was done and what was not

Scripts in `review/` (absolute paths inside; run with `.venv/bin/python`):
- `stress_theory_composer.py`: 1,218 theory melodies across 7 time signatures, 5 levels, 6 keys and several lengths
  (set the shape in the `compose` call to test each). Checks overlap, key, range, grid, filled bars, tonic ending.
  Result at shapes 1 and 2: no problems. (An earlier texture stress test of 2,520 combinations was passed but its script
  is not included.)
- `measure_hybrid_shape.py`: 24 Hybrid melodies per shape using four fixed seed bars (the real LLM is stubbed out for a
  controlled comparison). Prints interval/run statistics.
- `drive_backend_shape.py`: drives the real `backend.py` over the protocol for both engines and prints the same statistics.
  **It creates session folders under `output/`; delete them afterwards.**

Measured effect (shape 0 -> 1 -> 2, n=24 Hybrid, fixed seeds): moves inside scalar runs 15% -> 8% -> 6%; repeated notes
11% -> 5% -> 3%; direction changes 40% -> 49% -> 55%; pitch range 17 -> 15 -> 13 semitones (Angular narrows the range).
Theory composer, level 4, 60 melodies: scalar runs 15% -> 8% -> 8%.

Checks run for the follow-up fixes: the JSON parser on eight malformed or odd replies (clean, prose + fence, trailing
braces, dict-style notes, mixed bad entries, truncated, `notes` not a list, empty); `compose()` with a stub LLM that
fails twice (recovers) and always (clean `RuntimeError`); Hybrid with an LLM that never returns usable JSON (zero
candidates, no crash); the CLI with `--melody-engine theory --shape 2 --phrasing 2`; and `drive_backend_shape.py`
through the real backend after the sampler change. These are ad hoc scripts, not a test suite.

**Not verified:**
- **Sound quality.** Nobody (including me) has listened to the new output. The metrics are proxies.
- **The GUI.** I cannot click or screenshot the app. The Swift code compiles and the backend protocol was exercised
  headlessly; the new pickers were not seen on screen.
- Sample sizes are small (24 melodies, one prompt, four stub seeds), so the weights in `SHAPE_BIAS` were tuned on thin data.
- The real-LLM end-to-end path was tested with real 7B output only in short runs; many other tests substituted stand-ins.
- No Swift unit tests, no Python unit tests, no CI.

## 6. Known issues and risks (my own list)

1. ~~LLM JSON handling is brittle~~ **Fixed** (see section 4). Remaining: a model that keeps returning bad JSON still
   ends in an error message after three tries; there is no constrained decoding or JSON-schema enforcement.
2. **Module-level mutable state** in `text2music.py`: current session, a one-entry LLM cache (`_llm`), the Anticipation
   model cache. The backend is single-threaded and handles one request at a time; there is no locking and cancellation is
   not supported.
3. **Memory pressure.** On 16 GB, 7B (about 4.5 GB) plus the Anticipation model plus other apps has produced transient
   Metal "Insufficient Memory" errors. There is a 3B fallback for directions only.
4. **`text2music.py` is a 1,174-line monolith** mixing sessions, LLM prompting, key theory, scoring, MIDI I/O, accompaniment
   and CLI. `composer.py`, `texture.py` and `text2music.py` each carry their own scale/chord/key helpers.
5. ~~Stale `direction.py` docstring~~ **Fixed.**
6. ~~Dead CLI flag~~ **Fixed.** The CLI now honours `--phrasing` and `--shape` and supports `--melody-engine theory`.
7. **`resolve_ending` and `snap_to_key`** modify notes after generation (final note -> tonic; out-of-scale notes moved to the
   nearest scale tone). Both can flatten what the sampler or composer intended, and snapping can create repeated notes.
8. **Hybrid cannot produce deliberate motifs.** Phrasing only inserts rests; shape only biases pitch choice. This is a
   limitation of the approach, not a bug.
9. **Defaults changed:** Phrasing = Balanced and Melodic shape = Balanced alter output for anyone expecting the old behaviour
   (Flowing + Stepwise reproduce it for the theory composer; Hybrid's sampler is otherwise unchanged at shape 0).
10. **Hard-coded location:** default project root is `/Users/Shared/music-ai`; output lives inside the project folder
    (`output/`), including user work and a `.tmp` scratch folder.
11. **Distribution:** ad-hoc signed, bundle id retained from the original name, no notarization, expects `.venv` next to it.
12. `melodic_bias` reads history from token order (assumed chronological). The melody instrument is now explicit
    (`instrument` in the bias dict) rather than inferred; the ordering assumption remains.
13. **Deployment is not self-contained:** `Composer.app` needs the sibling `.venv` and the Hugging Face cache; see
    `DEPLOY.md`. `requirements.txt` was generated from the working environment, and a clean-machine install following
    `DEPLOY.md` has **not** been tried end to end (only `uv pip install --dry-run` against the existing venv).

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
6. Test gaps: what minimal automated tests would catch regressions in the sampler constraints and the key/ending logic?

Specific questions:
- Is there any path where a constraint (key, range, grid, tonic ending) can be violated in the final MIDI?
- Can `melodic_bias` (or any steering) produce `-inf` everywhere or NaN, and what does `torch.multinomial` do then?
- Are there user-data-loss risks in session rename/open/new or in the `.tmp` cleanup?
- Are the Swift and Python halves of the protocol in agreement on optional fields and defaults (`phrasing`, `shape`, `key`,
  `bpm`, `resolve`, `llm_directions`)?
- Where would you split `text2music.py` first?
