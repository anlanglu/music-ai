# soulx/

Vendored [SoulX-Singer](https://github.com/Soul-AILab/SoulX-Singer) (Apache 2.0, see `LICENSE`), the zero-shot singing voice
model. Treat `soulxsinger/`, `preprocess/`, `cli/` and `prompt/` as upstream code: they are excluded from the project's lint
and tests, and are changed only to make them run here.

Our own files:

- `sing.py`: sings a job file; runs in its own environment (`.venv-sing`), called by `lyrics.py`.
- `prep.py`: turns a recording of someone singing into a voice prompt; runs in `.venv-prep`.
- `prompt/`: the two built-in reference voices (English and Mandarin), as `.wav`/`.mp3` plus metadata. These audio files are
  committed on purpose; the rest of the repo ignores audio (see `.gitignore`).

The model weights are not in the repository; they download to `models/` on first use.
