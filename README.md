# Composer (music-ai)

A fully local, text-prompt music composition tool for macOS. Describe a melody in words, pick one of several options,
then build it up into a piece with up to 16 instruments. A native SwiftUI app (**Composer.app**) drives a Python backend;
everything runs on your Mac, with no cloud service and no account.

- Melodies come from a local language model (Qwen2.5 via MLX), the Stanford **Anticipatory Music Transformer**, or a
  rule-based theory composer you can pick from a dropdown.
- Output is standard MIDI (plays in Logic, GarageBand, any DAW) plus MusicXML and PDF sheet music.

## What it does

1. **Melody first.** Prompt plus time signature, BPM, key, length, instrument, complexity, **Phrasing** (rests between
   ideas) and five sliders for the line's character (see below).
   You get N options to audition (piano roll or sheet music) and can regenerate or import your own MIDI instead.
   The ending can be forced to resolve on the tonic.
2. **Then accompaniment**, either all at once (up to 16 instruments) or one instrument at a time: type a free-text
   direction ("answer the melody in the gaps, high and cheeky"), audition options, approve or undo, repeat. Parts respect
   each instrument's real range and polyphony, and follow the melody's own chords. Piano can get chords, arpeggios,
   Alberti bass and more under an unchanged melody.
3. **Sessions.** One folder per project under `output/`, nameable, renamable, reopenable.
4. **Sheet music.** Per-instrument clefs (grand staff for piano and harp), key signatures, PDF export (A4), MusicXML for
   MuseScore, Dorico, Sibelius and others.

## The character of the melody

A melody with no shape behind it wanders. Five sliders (0 to 10) in the Melody section put one there:

| Slider | What it does |
|---|---|
| **Smooth ↔ Jumpy** | how the tune moves: small steps along the scale, or bigger leaps and turns |
| **Steady pulse** | how strongly notes start on the beat: loose and improvised, or square and regular |
| **Fit the harmony** | every bar has a chord underneath, and the melody prefers its notes |
| **Phrase endings** | each phrase finishes on a note that sounds like a stopping place, like a full stop |
| **Recurring theme** | the opening idea comes back at the start of each phrase, moved onto its chord (off by default) |

The three plan sliders at zero give a free melody. The same plan is kept with the session, so the accompaniment is
harmonised with the melody instead of separately. `plan.py` holds the plan (phrases, chord of every bar, cadences, motif).
The accompaniment generator's own settings (sampling randomness, retries, minimum density, beat-grid snapping, LLM
directions) are no longer in the app: they have fixed, sensible values, and the backend and CLI still accept them. The command line keeps the older `--form 0..3` and `--shape 0..2`
presets, and the backend still accepts `form`, `shape` and individual `knobs`; the app sends `shape_amount` and four strengths.

## Requirements and install

Apple-silicon Mac, macOS 14+, 16 GB RAM, about 15 GB of disk (models download once from Hugging Face), Python 3.11 and the
Swift Command Line Tools. Full, step-by-step instructions are in **[DEPLOY.md](DEPLOY.md)**. In short:

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python --no-deps -e ./anticipation
zsh MusicAI/build_app.sh          # builds Composer.app next to the project folder
open Composer.app
```

Try it without the app (no model download needed, uses the theory composer):

```bash
.venv/bin/python text2music.py "a bright tune" --melody-engine theory --melody-only --key "G major" --session try-it
```

## Layout

| Path | What |
|---|---|
| `MusicAI/` | the SwiftUI app (SwiftPM) and the build script |
| `backend.py` | JSON-lines server the app talks to |
| `text2music.py` | the pipeline: sessions, melody engines, key logic, accompaniment, CLI |
| `plan.py` | the musical plan: phrases, the chord of every bar, the cadences, the motif |
| `composer.py` | rule-based melody composer |
| `texture.py` | rule-based accompaniment patterns |
| `direction.py`, `llmjson.py` | free-text directions and tolerant LLM-JSON parsing |
| `playable.py` | instrument ranges and polyphony |
| `notation.py` | MIDI to MusicXML to engraved sheet music |
| `anticipation/` | vendored Anticipatory Music Transformer (Apache 2.0) with local sampler changes |
| `tests/` | pytest suite |
| `review/` | the sampler patch and measurement scripts |

## Development

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
make test          # about 3 seconds, no model downloads
make test-full     # adds a sweep of about 140,000 composer settings
make app           # rebuild Composer.app after Swift changes
```

Python changes take effect when the app is restarted; Swift changes need `make app`. Add `--seed N` to the CLI (or a
`seed` field to a backend request) for repeatable output.

If the app's Python process crashes, the app shows the last lines of its error output and offers **Restart backend**;
the full log is at `~/Library/Logs/Composer/backend.log`.

## Documentation

- [DEPLOY.md](DEPLOY.md): requirements, models and sizes, install, build, run, troubleshooting.
- [REVIEW.md](REVIEW.md): architecture, what was verified and how, known issues, and a guide for code review.

## Status and limitations

- Nobody has formally evaluated how the output sounds; quality measures in the repo are statistical proxies.
- The plan sliders are new: the plan the melody follows was measured with
  `review/bench_plan.py` (harmonic fit, cadences and motif recurrence over 24 melodies), not by ear. The motif comes back
  as a *paraphrase* — the shape of the opening idea with the model's own rhythm — not as a literal repeat.
- The plan's harmony is diatonic and functional (the same progressions the theory composer uses). It cannot express
  a modal progression, a borrowed chord or a key change.
- The app is not self-contained: it needs the project folder, its `.venv` and the Hugging Face model cache. It is
  ad-hoc signed, not notarized.
- There is no way to cancel a running generation (only to restart the backend).

## Credits

Built on the [Anticipatory Music Transformer](https://github.com/jthickstun/anticipation) (Thickstun et al., Stanford CRFM,
Apache 2.0), Qwen2.5 (Alibaba) through [MLX](https://github.com/ml-explore/mlx), music21, verovio and mido. Model weights
are downloaded from Hugging Face under their own licenses.
