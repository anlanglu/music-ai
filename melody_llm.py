"""Asking the language model for a melody and cleaning up what it returns."""
import json


from llmjson import extract_json
from config import COMPLEXITY, SYSTEM, TEMPERATURE, bar_beats
from keys import key_label, scale_names
from models import _generate, _get_llm


def llm_melody(prompt, bars, sig="4/4", level=3, llm="7B", before=None, phrase=(1, 1), tempo=None, key=None, chords=None,
               guidance="", ending=True, climax=True):
    model, tok = _get_llm(llm)
    bb = bar_beats(sig)
    style, density, (lo, hi) = COMPLEXITY[level]
    system = (
        SYSTEM.replace("{sig}", sig).replace("{bar_beats:g}", f"{bb:g}")
        .replace("{lo}", str(lo)).replace("{hi}", str(hi)).replace("{style}", style)
    )
    user = f"{prompt}\nTime signature: {sig}. Length: {bars} bars ({bars * bb:g} quarter-note beats)."
    if key:
        user += f"\nKey: {key_label(key)}. The notes of that scale are: {scale_names(key)}."
        if "Scale rule:" not in guidance:
            user += " Every note must belong to it."
    if chords:
        names = ", ".join(chords)
        user += f"\nThe harmony, one chord per bar: {names}. Prefer the notes of each bar's chord, especially on strong beats."
        if ending:
            user += " Start and end the phrase on a chord tone of the chord in that bar."
    if guidance:
        user += "\n" + guidance
    if tempo and not before:
        user += f"\nTempo: {tempo} bpm (use exactly this tempo in the JSON)."
    if phrase[1] > 1:
        user += f"\nThis is phrase {phrase[0]} of {phrase[1]} of a longer melody; start_beat restarts at 0 for this phrase."
    if before:
        prev = [[p, s, d] for p, s, d in before]
        user += (f"\nThe melody so far (the previous phrase, same format): {json.dumps(prev)}.\nContinue it: answer, "
                 f"develop or vary its motif (sequence, inversion, new rhythm); do not copy it.")
        user += f" Use tempo {tempo}." if tempo else ""
    if phrase[0] == phrase[1] and phrase[1] > 1:
        user += (" This is the final phrase: build to the climax if not yet reached, then resolve and end on a stable note."
                 if climax else " This is the final phrase: end on a stable note.")
    elif phrase[0] == 1 and phrase[1] > 1:
        user += " This is the opening phrase: state the main motif clearly."
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    text = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
    max_tokens = 600 + int(bars * bb * density * 18)
    raw = _generate(model, tok, text, max_tokens, TEMPERATURE[level], 0.95)
    data = _json_with_notes(raw)
    if data is None:
        raise ValueError(f"LLM returned no usable JSON:\n{raw}")
    notes = _clean_notes(data["notes"])
    if not notes:
        raise ValueError(f"LLM returned no valid notes:\n{raw}")
    try:
        tempo_out = int(float(data.get("tempo", 100)))
    except (TypeError, ValueError):
        tempo_out = 100
    return tempo_out, notes, str(data.get("plan", "")), str(data.get("key", "")), str(data.get("reading", ""))


def _json_with_notes(raw):
    """The first JSON object in the LLM's reply that has a "notes" list (see llmjson.extract_json)."""
    obj = extract_json(raw, "notes")
    return obj if obj is not None and isinstance(obj["notes"], list) else None


def _clean_notes(items):
    """[(pitch, start, dur)] from whatever the LLM put in "notes": [p, s, d] lists or {"pitch", "start", "duration"}
    objects. Entries that aren't usable are skipped rather than failing the whole melody."""
    notes = []
    for it in items:
        try:
            if isinstance(it, dict):
                def get(*ks, item=it):
                    return next(item[k] for k in ks if k in item)
                p, s, d = get("pitch", "note", "p"), get("start", "time", "onset", "s"), get("duration", "dur", "length", "d")
            else:
                p, s, d = it[0], it[1], it[2]
            p, s, d = int(p), float(s), float(d)
        except (TypeError, ValueError, KeyError, IndexError, StopIteration):
            continue
        if 0 <= p <= 127 and s >= 0 and d > 0:
            notes.append((p, s, d))
    notes.sort(key=lambda n: n[1])
    return notes


def conform_melody(notes, bars, sig):
    """Force the LLM's notes onto the bar grid: snap to 16ths, drop anything past the last bar, remove
    overlaps. Returns (notes, coverage) where coverage is the fraction of the piece the melody spans."""
    total = bars * bar_beats(sig)
    snapped = {}
    for p, s, d in notes:
        s = round(s * 4) / 4
        d = max(round(d * 4) / 4, 0.25)
        if s < total and s not in snapped:  # one note per onset: the melody is monophonic
            snapped[s] = (p, s, d)
    out = sorted(snapped.values(), key=lambda n: n[1])
    out = [(p, s, min(d, (out[i + 1][1] - s) if i + 1 < len(out) else d, total - s)) for i, (p, s, d) in enumerate(out)]
    out = [n for n in out if n[2] > 0]
    if not out:
        return [], 0.0
    coverage = (out[-1][1] + out[-1][2]) / total
    if coverage >= 0.75:  # ended a little early: let the last note ring to the final barline
        p, s, _ = out[-1]
        out[-1] = (p, s, total - s)
        coverage = 1.0
    return out, coverage
