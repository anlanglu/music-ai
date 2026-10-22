"""The real backend, with the language model replaced by a deterministic stand-in, so the tests need no model download.
Run as `python tests/stub_backend.py`; the tests drive it over the JSON-lines protocol like the app does."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import text2music as t  # noqa: E402


def fake_notes(bars, sig, key, rng, lo=55, hi=84):
    """A plausible melody: a random walk in the key that fills the bars exactly."""
    total = bars * t.bar_beats(sig)
    pcs = sorted(t.scale_pcs(key or (0, "major")))
    pitches = [p for p in range(lo, hi + 1) if p % 12 in pcs]
    i, pos, notes = len(pitches) // 2, 0.0, []
    while pos < total - 1e-9:
        d = min(rng.choice([0.5, 0.5, 1.0, 1.0, 2.0]), total - pos)
        notes.append((pitches[i], pos, d))
        pos += d
        i = max(0, min(len(pitches) - 1, i + rng.choice([-2, -1, -1, 1, 1, 2, 3])))
    return notes


def fake_llm_melody(prompt, bars, sig="4/4", level=3, llm="7B", before=None, phrase=(1, 1), tempo=None, key=None,
                    chords=None, guidance="", ending=True, climax=True):
    rng = random.Random(hash((prompt, bars, phrase, len(before or []))) & 0xFFFF)
    return (tempo or 100), fake_notes(bars, sig, key, rng), "a rising idea that peaks mid-way", "C major", "a bright, simple tune"


t.llm_melody = fake_llm_melody
t.pick_key = lambda prompt, llm, log: (t.parse_key(prompt) or (7, "major"))

import backend  # noqa: E402

if __name__ == "__main__":
    backend.main()
