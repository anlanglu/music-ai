"""Sings a job file with SoulX-Singer (zero-shot singing voice synthesis). Runs in its own environment (.venv-sing):
    .venv-sing/bin/python soulx/sing.py job.json
job.json: {"segments": [{time:[ms,ms], duration, phoneme, text, note_pitch, note_type}, ...], "out": "x.wav",
           "prompt_wav": ..., "prompt_meta": ..., "model": ..., "n_steps": 32, "cfg": 3}
Each segment is one lyric line in the metadata format of the SoulX-Singer examples (score control). The voice is cloned from
the prompt recording. Prints "progress i/n" lines and "done <path>"."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402

from cli.inference import build_model  # noqa: E402
from soulxsinger.utils.data_processor import DataProcessor  # noqa: E402
from soulxsinger.utils.file_utils import load_config  # noqa: E402


def main(job_path):
    job = json.load(open(job_path))
    config = load_config(str(HERE / "soulxsinger/config/soulxsinger.yaml"))
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = build_model(job["model"], config, device)
    dp = DataProcessor(hop_size=config.audio.hop_size, sample_rate=config.audio.sample_rate,
                       phoneset_path=str(HERE / "soulxsinger/utils/phoneme/phone_set.json"), device=device)
    prompt_meta = json.load(open(job["prompt_meta"]))[0]
    prompt_median = float(np.median([x for x in (int(v) for v in prompt_meta["note_pitch"].split()) if x >= 1]))
    prompt = dp.process(prompt_meta, job["prompt_wav"])
    sr = config.audio.sample_rate
    # one shift for the whole song (the model's own auto-shift works per line, which moves the key between lines)
    notes = [x for x in (int(v) for sg in job["segments"] for v in sg["note_pitch"].split()) if x >= 1]
    shift = round(prompt_median - float(np.median(notes)))
    if job.get("keep_key", True):    # only whole octaves, so the voice stays in the written key and fits an accompaniment
        shift = 12 * round(shift / 12)
    print("pitch shift", shift, flush=True)
    segs = job["segments"]
    merged = np.zeros(int(max(s["time"][1] for s in segs) / 1000 * sr), dtype=np.float32)
    for i, seg in enumerate(segs):
        target = dp.process(seg, None)
        with torch.no_grad():
            audio = model.infer({"prompt": prompt, "target": target}, auto_shift=False, pitch_shift=shift,
                                n_steps=job.get("n_steps", config.infer.n_steps), cfg=job.get("cfg", config.infer.cfg),
                                control="score", use_fp16=False)
        audio = audio.squeeze().cpu().numpy()
        start = int(seg["time"][0] / 1000 * sr)
        n = min(len(audio), len(merged) - start)
        merged[start:start + n] = audio[:n]
        print(f"progress {i + 1}/{len(segs)}", flush=True)
    sf.write(job["out"], merged, sr)
    print("done", job["out"], flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
