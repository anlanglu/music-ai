# Composer: install and deploy guide

Composer is a native macOS app (SwiftUI) that starts a local Python process to write music. Everything runs on the
machine: there is no server, account, or cloud call. The only network use is the one-time download of model weights
from Hugging Face.

The app is **not a self-contained bundle**. `Composer.app` is a thin launcher (1.7 MB) that runs
`<project root>/.venv/bin/python backend.py`. So "deploying" means setting up the project folder, its Python
environment and the model cache on the target Mac, then building the app next to them.

## 1. What you need

| Requirement | Detail |
|---|---|
| Mac | Apple silicon (M-series). The models use Metal (MLX for the language model, PyTorch MPS for the music model). Intel Macs are not supported. |
| macOS | 14 or newer (`LSMinimumSystemVersion` is 14.0). Developed on macOS 27. |
| RAM | 16 GB works; the 7B language model plus the music model together use roughly 6-8 GB. On 16 GB, close memory-heavy apps first or you may see transient Metal "Insufficient Memory" errors. 8 GB is not recommended. |
| Disk | About 15 GB free for the default setup (see model table). Up to about 20 GB with every optional model. The Python environment is about 1.3 GB. |
| Swift toolchain | Apple Command Line Tools are enough (`xcode-select --install`); full Xcode is not needed. Swift 5.9+ (built and tested with Swift 6.4 in Swift 5 mode). |
| Python | 3.11 (the tested version). |
| `uv` | Recommended for creating the environment (`brew install uv`); plain `python3.11 -m venv` + `pip` also works. |
| Network | Needed once, to download the model weights (and the Python packages). Afterwards the app is fully offline. |
| Sound bank | `/System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls`, built into macOS, used for playback. Nothing to install. |

### Models (downloaded automatically from Hugging Face on first use)

| Model | Used for | Size |
|---|---|---|
| `stanford-crfm/music-small-800k` | Anticipatory Music Transformer, default | 0.5 GB |
| `stanford-crfm/music-medium-800k` | optional, "Anticipation model" in the Melody section | 1.3 GB |
| `stanford-crfm/music-large-800k` | optional | 2.9 GB |
| `mlx-community/Qwen2.5-7B-Instruct-4bit` | language model for melodies and directions, default | 4.0 GB |
| `mlx-community/Qwen2.5-3B-Instruct-4bit` | lightweight fallback for reading directions | 1.6 GB |
| `mlx-community/Qwen2.5-14B-Instruct-4bit` | optional, richer melodies, slower, memory-hungry | 7.7 GB |

They are stored in the standard Hugging Face cache (`~/.cache/huggingface/hub`). The first generation that needs a
model downloads it; the app shows a "download" note beside a model that is not cached yet. To avoid a surprise
download later, pre-fetch them (step 4).

The Python code in this folder pulls in only these runtime libraries: torch, transformers, MLX and mlx-lm, mido,
music21, verovio, numpy, tqdm, huggingface-hub (see `requirements.txt` for the exact tested versions).

## 2. Get the project folder in place

Copy or clone the project to the target Mac. The default location is `/Users/Shared/music-ai`; any other location works
if you set `MUSIC_AI_ROOT` (see 6.3). The folder must contain: `backend.py`, `text2music.py`, `composer.py`,
`texture.py`, `direction.py`, `playable.py`, `notation.py`, `variety.py`, `requirements.txt`, `MusicAI/` and
`anticipation/`.

**The vendored `anticipation/` directory contains local modifications** (metrical grid, instrument steering, KV cache,
melodic-shape bias) that the app depends on. If you start from a fresh upstream clone instead of copying this folder,
apply the patch:

```bash
git clone https://github.com/jthickstun/anticipation.git
cd anticipation
git checkout af37397922665a0fb8d474d7988b0f3755a38d45   # the commit the patch was made against
git apply ../review/anticipation_sample.patch
```

Do not use upstream's `requirements.txt`: it pins `transformers==4.29.2`, which is incompatible with this project.

## 3. Create the Python environment

```bash
cd /Users/Shared/music-ai
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python --no-deps -e ./anticipation
```

(Without uv: `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install --no-deps -e ./anticipation`.)

Check it:

```bash
.venv/bin/python -c "import torch, mlx_lm, music21, verovio, mido, anticipation.sample; print('torch', torch.__version__, 'mps', torch.backends.mps.is_available())"
```

It should print `mps True`. If it prints `mps False`, generation falls back to the CPU and will be many times slower.

### Run the tests (optional, no models needed)

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
make test            # about 3 seconds; `make test-full` also sweeps about 140,000 composer settings
```

## 4. Download the models once (optional but recommended)

```bash
.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
for repo in ["stanford-crfm/music-small-800k", "mlx-community/Qwen2.5-7B-Instruct-4bit", "mlx-community/Qwen2.5-3B-Instruct-4bit"]:
    print(repo, snapshot_download(repo))
PY
```

Add `stanford-crfm/music-medium-800k`, `stanford-crfm/music-large-800k` or `mlx-community/Qwen2.5-14B-Instruct-4bit` to the
list if you want those options. Hugging Face may warn about unauthenticated requests; that is harmless for public models
(set `HF_TOKEN` to speed up downloads).

To move an already-downloaded cache to another Mac, copy `~/.cache/huggingface/hub` over (or point `HF_HOME` at a shared location).

## 5. Build the app

```bash
cd /Users/Shared/music-ai
zsh MusicAI/build_app.sh
```

This runs `swift build -c release`, assembles `Composer.app` next to the project folder (executable, `Info.plist`, icon),
and ad-hoc code-signs it (`codesign --sign -`). It needs only the Command Line Tools. The result is
`/Users/Shared/music-ai/Composer.app`.

Re-run the script after any change to the Swift sources. Changes to the Python files need no rebuild, just quit and
reopen the app (the backend process loads the code at launch).

## 6. Run it

1. Open `Composer.app` (double-click, or `open /Users/Shared/music-ai/Composer.app`).
2. First launch of an ad-hoc-signed app: macOS Gatekeeper may block it if the app was copied from another machine or
   downloaded. Right-click the app, choose **Open**, then **Open** again; or run
   `xattr -dr com.apple.quarantine /Users/Shared/music-ai/Composer.app`.
3. If the project is somewhere other than `/Users/Shared/music-ai`, launch with the root set:
   `MUSIC_AI_ROOT=/path/to/music-ai /path/to/music-ai/Composer.app/Contents/MacOS/Composer` (an app started from
   Finder does not see shell variables, so for a different location either use that command, or change the default in
   `MusicAI/Sources/MusicAI/Backend.swift`, `projectRoot`, and rebuild).
4. Type a prompt, pick key/BPM/etc., and press **Generate melodies** (Cmd-Return). The first run is slower: models load (and download, if you
   skipped step 4).

Output goes to `<project root>/output/<date>_<time>_<name>/` (one folder per session: MIDI files, `session.json`,
sheet-music HTML and MusicXML). Preferences are stored under the bundle id `local.musicai.texttomusic`.

### Quick check without the app

```bash
cd /Users/Shared/music-ai
.venv/bin/python text2music.py "a hopeful tune in G major" --melody-engine theory --melody-only --key "G major" --session smoke-test
```

This uses the theory composer, which needs no model download, and writes a MIDI file into `output/`. Delete the
session folder afterwards. The JSON-lines backend can be tried directly with `.venv/bin/python -u backend.py`
(it prints `{"type": "ready"}`).

### The command-line options that matter

| Option | What it does |
|---|---|
| `--melody-engine hybrid\|llm\|theory` | which composer writes the melody |
| `--form 0..3` | how much of a written plan the melody follows: 0 free, 1 harmony, 2 cadences, 3 the whole plan (the app has sliders instead: Follow the chords, Land on the cadence, Bring the idea back) |
| `--shape 0..2` | melodic shape: stepwise, balanced, angular |
| `--phrasing 0..2` | rests between ideas and phrases: flowing, balanced, spacious |
| `--key "D minor"` | force a key instead of guessing one from the prompt |
| `--seed N` | make the run repeatable (the same seed gives the same music) |
| `--melody-only` | stop after the melody, before the accompaniment |

## 7. Updating, moving, and distributing

- **Update Python code:** replace the files, restart the app.
- **Update Swift code:** replace the sources, run `zsh MusicAI/build_app.sh`.
- **Move to another Mac (same architecture):** copy the whole project folder including `.venv` only if the destination
  has the same macOS major version and Python location; otherwise recreate `.venv` (step 3), which is safer because virtual
  environments contain absolute paths.
- **Sharing with others:** the app is ad-hoc signed and not notarized, and it depends on the sibling `.venv` and the model
  cache, so it cannot simply be dragged to `/Applications`. Making a standalone, notarized installer would need a bundled
  Python runtime, an Apple Developer ID, and a first-launch model downloader; none of that exists yet.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| App opens but "Generate melodies" does nothing / "backend failed" | `.venv` missing or broken at `<root>/.venv/bin/python`. Redo step 3 and run the check command. |
| "Insufficient Memory" (Metal) | Close other apps, use the 7B model instead of 14B, or retry; pressure is often transient on 16 GB. |
| Very slow generation | `torch.backends.mps.is_available()` is false, so it is running on the CPU. Check macOS version and the torch install. |
| A model "downloads" every time | `HF_HOME`/cache path differs between the shell where you pre-fetched and the app's environment. Use the default cache location. |
| "App is damaged" / cannot be opened | Quarantine flag after copying: `xattr -dr com.apple.quarantine Composer.app`. |
| "Python backend exited" in the app | The message includes the last lines of the process's error output; the full log is `~/Library/Logs/Composer/backend.log`. Press **Restart backend** (loaded models are lost, files already written are safe). The same button appears if a request shows no sign of life for 4 minutes. |
| No sound in the app | Playback uses macOS's built-in `gs_instruments.dls`; check output device and system volume. MIDI files themselves open in Logic or any DAW. |
| Sheet music fails | `music21` or `verovio` missing from the environment; reinstall step 3. The log shows the error. |
