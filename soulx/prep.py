"""Turns a recording of someone singing into a SoulX-Singer voice prompt (the words, notes and timing of the recording are
worked out by the preprocess pipeline). Runs in its own environment (.venv-prep):
    .venv-prep/bin/python -I soulx/prep.py job.json
job.json: {"audio": "source.wav", "save_dir": "work", "language": "English" | "Mandarin", "models": "models/soulx-preprocess"}
Writes save_dir/metadata.json and the matching save_dir/long_cut_wavs/<index>.wav. Prints "done"."""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


def main(job_path):
    job = json.load(open(job_path))
    os.environ["SOULX_PREP_MODELS"] = job["models"]
    from preprocess.pipeline import PreprocessPipeline

    # no vocal separation: the recording must be a voice on its own (a cappella); one prompt of at most 30 s
    PreprocessPipeline(device="cpu", language=job["language"], save_dir=job["save_dir"], vocal_sep=False,
                       max_merge_duration=30000, midi_transcribe=True).run(audio_path=job["audio"], language=job["language"])
    print("done", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
