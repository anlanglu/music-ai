# Composer (music-ai)

A fully local, text-prompt music composition tool for macOS. Describe a melody in words, pick one of several options,
then build it up into a piece with up to 16 instruments. A native SwiftUI app (**Composer.app**) drives a Python backend;
everything runs on your Mac, with no cloud service and no account, unless you choose the optional **Claude** entries in the
Melody LLM menu (see below).

- The melody is written by a local language model (Qwen2.5 or Mistral Small 24B via MLX). It can then be grown by that model or by the Stanford
  **Anticipatory Music Transformer**, which also writes the accompaniment.
- Output is standard MIDI (plays in Logic, GarageBand, any DAW) plus MusicXML and PDF sheet music.

## What it does

1. **Start with a motif.** A small musical idea of one to four bars: prompt plus time signature, BPM, key, length, instrument, complexity, **Phrasing** (rests between
   ideas) and a few sliders for the line's character (see below). The language model writes every bar. You get N options
   to audition (piano roll or sheet music) and can regenerate or import your own MIDI instead. The ending can be forced
   to resolve on the tonic.
2. **Extend the melody.** (Under the Expand button, **Or repeat part of the melody** copies bars — one or several times — onto the end, with their sung line, lyrics, singers and accompaniment: a verse sung again by the same duet or choir. Nothing is generated.) Pick an option and add bars (2–16) at a time, each time for an **Instrument** of your choice (the melody is passed
   on to it from there; with the Voice instrument the bars are sung to the next lyric lines, by a singer picked for that step), as new options to choose from, as often
   as you like (with Undo). Each expansion has its own settings: length, complexity, how jumpy, how strongly the
   motif comes back, dynamics and a free-text direction ("build to a climax, then calm down"). It is written by the
   Anticipation model, steered by the chords, cadences and opening idea, or by the language model for the "answer the
   motif" move. What was written is kept;
   the chords carry on, so the accompaniment follows the whole.
3. **Edit the melody** (or, once there is one, the singing). Fix what you do not like, at any point (the accompaniment already written stays as it is). By hand: the piano roll becomes a note editor (add,
   move, resize, delete), and saving redraws the sheet music. Or in words ("I don't think measure 2 leads into measure 3
   very well, so connect them better"): the LLM is given only the bars you name, its answer is spliced in by code and
   checked, so every other bar stays exactly as it was. If it thinks other bars would need to change as well, it asks
   first and you decide.
4. **Add singing and accompaniment (optional).** First the **Singing voice**: a sung line written over the melody to your lyrics (Lyrics button),
   a few bars at a time, with one singer or a duet or choir (the same notes, or **Harmonize**). It is a second line in the
   melody's own file, so the melody is not changed, and you can then change its notes in step 3 (choose *The singing*; every
   bar keeps one note per syllable) or lengthen the melody in step 2 and sing the new bars. **Hear it sung** makes the audio, with the melody
   under it. Then instruments, one at a time, written by the Anticipation model **for the bars you choose**
   (*Bars from … to …*): type a free-text direction ("answer the melody in the gaps, high and cheeky"), audition options, approve
   or undo, repeat. Parts respect each instrument's real range and polyphony, and follow the melody's own chords. Piano can get
   chords, arpeggios, Alberti bass and more. So a piece is built section by section: a full band for bars 1-4, then
   **Take the accompaniment out of these bars** to leave the singer alone in bars 5-8, then a different set of instruments for
   bars 9-12. Each instrument is listed with its bars and can be taken out. The melody and the singing stay editable (step 3) and
   extendable (step 2) throughout; new bars start without accompaniment.
5. **Enrich (optional).** The Anticipation model adds a whole ensemble (up to 16 instruments) around the piece so far,
   including the parts approved in step 4, which stay exactly as they are. It always covers the whole piece. Press again to re-roll it, or undo it.
6. **Sessions.** One folder per project under `output/`, nameable, renamable, reopenable.
7. **Lyrics (demo).** The **Lyrics** button keeps your words apart from the melody (put in as many lines as you like; it checks
   that every word can be pronounced). Choose the **Voice** instrument in the Motif step and the melody is written with
   one note per syllable to as many lines as fit the bars you asked for; each extension of a Voice melody carries on with the next
   line, and never has to use them all. The lines a melody was written to are stored beside it (`*.lyrics.json`). **Sing the chosen
   melody** has SoulX-Singer, a local singing-voice model (setup: DEPLOY.md 4b), sing those lines into a .wav, in the written key, in the singer chosen for each part: the Motif step and every Extend step have their own **Singer**, and can add more singers with them for a duet or choir (up to four voices: all on the same notes, or ticking **Harmonize** gives each extra singer a part, a third above, a third below and a fifth above, always in the key) (the two reference singers that come with SoulX, or voices you add; a voice is copied from a short recording, so more voices need more recordings). If accompaniment has been added (steps 4–5), the voice and the accompaniment are mixed into one `*.mix.wav`, rendered offline with the same Apple sound bank as playback. English and Mandarin (Chinese
   characters are one syllable and one note each); unusual English words need respelling. **Add voice…** makes a new singer
   from your own singing (recorded with the microphone, 10–30 seconds) or from a file of someone singing alone (setup: DEPLOY.md 4c; only for voices you own or have permission to copy).
8. **Sheet music.** Per-instrument clefs (grand staff for piano and harp), key signatures, PDF export (A4), MusicXML for
   MuseScore, Dorico, Sibelius and others.

## Choosing the melody LLM

The **Melody LLM** menu offers Qwen 7B and 14B and Mistral Small 24B (3-bit, about 10 GB, heavy on a 16 GB Mac: close other
apps; it is slower and tends to write fewer notes per attempt). Under it are **Claude Haiku 5.5 (cheap), Sonnet 5.5
(moderate) and Opus 5.5 (expensive)** through the `claude` command-line tool (Claude Code) on your Mac. They need no API key:
they use your Claude subscription, so they count against its usage limits instead of being billed per request (check
Anthropic's current terms for programmatic use). The prompt, the melody and your instructions leave the Mac. Claude fixes its
own sampling, so the complexity setting does not change its randomness. Fable 5.1 is not offered: Claude Code bills it as
extra usage credits. The Anticipation model (extension and accompaniment) stays local.

## The character of the melody

A melody with no shape behind it wanders. These sliders (0 to 10) in the Melody section put one there:

| Slider | What it does |
|---|---|
| **Smooth ↔ Jumpy** | how the tune moves: small steps along the scale, or bigger leaps and turns (jumpy also cuts one-way scale runs short) |
| **Steady pulse** | how strongly notes start on the beat: loose and improvised, or square and regular |
| **Dynamics** | flat to expressive: how much the loudness varies (beat accents, phrase swells, a rise to the climax, a tapering end). The accompaniment has its own Dynamics slider |
| **Modal flavor** | shown only when a mode other than major or minor is chosen: how often the note that gives the mode its sound is used |
| **Scale: Relaxed ↔ Strict** (under *Advanced*) | whether notes outside the key may stay |

In step 2 the motif has something to come back to, so **Bring the motif back** appears there: how strongly the opening
idea returns at the start of each new phrase, moved onto its chord. A motif is the theme, so step 1 has no such slider.
How much the melody fits each bar's chord and how firmly each phrase ends are fixed at sensible values
(`plan.SLIDER_DEFAULTS`). Settings most people never change (the Melody LLM, the Music model, the scale rule) are folded
under **Advanced**. The same plan is kept with the session, so the accompaniment is
harmonised with the melody instead of separately. `plan.py` holds the plan (phrases, chord of every bar, cadences, motif).
The accompaniment's sampling settings (randomness, retries, minimum density, beat-grid snapping, LLM directions) are fixed
constants in `text2music.py`, not options. The command line still has the plan sliders as flags: `--smooth-jumpy`, `--pulse`,
`--harmony`, `--endings`, `--theme` (each 0 to 10); `plan.from_sliders` is the one place that turns them into the sampler's
numbers, for the app and the command line alike.

The log under the results also says how the prompt was used: what is fixed (key, tempo, length), the model's own
one-sentence reading of the request, its plan for each phrase, the key it chose when you left it on Auto, and where its tempo
or key was overridden by yours. Lines start with ▸. The sliders reach the language model as instructions in words (for
example "never run more than three notes in one direction"); for the Anticipation model they are numbers in the sampler.

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

Try it without the app (uses the language model; it downloads on first use):

```bash
.venv/bin/python text2music.py "a bright tune" --melody-only --key "G major" --session try-it
```

## Layout

| Path | What |
|---|---|
| `MusicAI/` | the SwiftUI app (SwiftPM) and the build script |
| `backend.py` | JSON-lines server the app talks to |
| `text2music.py` | the pipeline: sessions, the LLM melody, key logic, accompaniment, CLI |
| `plan.py` | the musical plan: phrases, the chord of every bar, the cadences, the motif |
| `expand.py` | step 2: grow a melody by 2–16 bars (LLM or Anticipation) |
| `lyrics.py`, `soulx/` | lyrics to a melody (one note per syllable) and the singing voice (vendored SoulX-Singer, Apache 2.0) |
| `edit.py` | step 3: change only the named bars (LLM) or save a hand edit |
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
- The plan's harmony is diatonic and functional (a fixed set of classical progressions). It cannot express
  a modal progression, a borrowed chord or a key change.
- The app is not self-contained: it needs the project folder, its `.venv` and the Hugging Face model cache. It is
  ad-hoc signed, not notarized.
- There is no way to cancel a running generation (only to restart the backend).

## Credits

Built on the [Anticipatory Music Transformer](https://github.com/jthickstun/anticipation) (Thickstun et al., Stanford CRFM,
Apache 2.0), Qwen2.5 (Alibaba) through [MLX](https://github.com/ml-explore/mlx), music21, verovio and mido. Model weights
are downloaded from Hugging Face under their own licenses.


**Mixer.** Under the preview, each instrument of the piece has a volume slider (MIDI volume, 0–127). It is written into the MIDI file itself, so the notes are not changed and Logic or a notation app uses it too. Playback restarts from where it was when a volume changes.
