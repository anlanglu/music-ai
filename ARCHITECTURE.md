# Composer: architecture

A local, text-prompt music tool for macOS (Apple silicon, 16 GB). A SwiftUI app (`Composer.app`) drives a persistent
Python backend over JSON lines. Nothing calls a cloud service, except the optional Claude models, which go through the
`claude` command-line tool on the user's own subscription. Install steps are in `DEPLOY.md`, usage in `README.md`.

## How it fits together

```
Composer.app (SwiftUI)  --JSON line-->  backend.py  handle(request)  -->  melodies / expand / edit / parts / lyrics / notation
   ViewModel.swift  <--events (stdout)--   (single process, one request at a time, models cached in memory)
```

The five steps of the app: 1 Motif (`text2music.make_melodies`), 2 Extend (`expand.py`), 3 Edit (`edit.py`),
4 Parts (`parts.py` and `texture.py` for accompaniment, `lyrics.py` for the sung line), 5 Enrich (dynamics, mixing).
Every piece is a session folder under `output/` holding MIDI files, a `session.json` with the plan, and rendered audio.

## Python modules

| File | Role |
|---|---|
| `backend.py` | the JSON-lines protocol (documented in its header), one `handle()` function, error reporting |
| `config.py` | shared settings and tables: sampling knobs, the LLM list, GM instrument names, MIDI conventions |
| `sessions.py` | session folders: create, rename, open, list, seeds |
| `models.py` | loads and caches the language model (MLX) and the Anticipatory Music Transformer |
| `melody_llm.py` | asks the language model for a melody and cleans up what it returns |
| `keys.py` | keys, modes, scales: parsing from text, estimating from notes, holding a melody to them |
| `midifiles.py` | reading and writing the MIDI files: melody, sung line, accompaniment layers, clear/retime |
| `parts.py` | accompaniment options from the sampler, texture options, free-text directions |
| `text2music.py` | the melody pipeline (compose, finish, rank candidates) and the CLI; re-exports the names above for older callers |
| `plan.py` | the musical plan: phrases, the chord and mode of every bar, cadences, the motif |
| `harmony.py` | chord names to notes (`Cm#9`, `G7#9`, `C/E`) and progression/mode text |
| `expand.py`, `edit.py` | step 2 (grow by 2-16 bars) and step 3 (change only the named bars) |
| `texture.py`, `playable.py`, `variety.py`, `dynamics.py` | rule-based accompaniment, instrument ranges, variety, loudness |
| `lyrics.py` | lyrics to a melody (one note per syllable), harmony voices, SoulX-Singer rendering |
| `notation.py`, `mixer.py` | MIDI to MusicXML to engraved sheet music; the mixer view of a file |
| `direction.py`, `llmjson.py`, `claude_llm.py` | free-text directions, tolerant LLM-JSON parsing, the Claude CLI backend |
| `anticipation/` | vendored Anticipatory Music Transformer with local sampler changes (`review/anticipation_sample.patch` is the exact diff against upstream) |
| `soulx/` | vendored SoulX-Singer (Apache 2.0): the singing voice |

## Decisions worth knowing

- **Chords are names, not degrees.** `plan["harmony"]` holds up to two chord symbols per bar; `plan["chords"]` (scale
  degrees) is only the nearest degree of each bar's first chord. `plan["modes"]` gives bars a mode of their own; the tonic
  stays the same.
- **The sampler is steered, not rewritten.** `anticipation/anticipation/sample.py` has a metrical grid, instrument steering,
  a KV cache, per-chord pitch regions (`regions`) and per-bar scale regions (`scale_regions`). Everything else is outside it.
- **Melody finishing is a pipeline of small steps** (`text2music.finish_melody`): hold to the key or each bar's scale,
  cadence and phrase endings, breaths, then one flavour note per run of bars whose mode has one.
- **Module state is small and explicit:** the current session (`sessions._session`), the model caches (`models._llm`,
  `models._ant`). The backend handles one request at a time; there is no cancellation.

## Verification

- `make test` runs the pytest suite (about 200 tests, about 30 seconds, no model downloads). `make test-full` adds a sweep
  of the melody finishing steps over about 100,000 settings. `make lint` runs ruff.
- The model-backed steps are checked by hand with the real models; the tests use fakes for the language model and sampler.
- The Swift app is built with `make app` (Command Line Tools only, no Xcode, no macros so no `@State`: state lives in the
  `ViewModel`). It has no automated UI tests.

## Known limits

- A running generation cannot be cancelled; the app only offers a restart.
- 7B plus the sampler plus other apps can exhaust 16 GB (transient Metal "Insufficient Memory" errors).
- The app expects `.venv` and the Hugging Face cache next to it; a clean-machine install has not been tried end to end.
- Output seeds are repeatable for the LLM steps; sampler runs on MPS may differ slightly between runs.
- `resolve_ending` and `snap_to_key` change notes after generation and can flatten what the model intended.
- Qwen 7B can fail the "make this bar more rhythmic" edit on a bar of long notes ("No usable edit was produced").
