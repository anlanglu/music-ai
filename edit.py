"""Step 3: edit the melody, by hand or by asking the language model.

The rule that makes editing workable: bars the user did not name are never touched. The language model is only ever
asked for the named bars; its answer is spliced into the melody by code, and the result is checked bar by bar against
the original before it is written. If the model thinks other bars would have to change too, it says so, and that comes
back as a suggestion for the user to approve (the same edit is then run again with those bars added).
"""
import json
import re
import time
from pathlib import Path

import plan
import text2music as t
from expand import read_melody
from llmjson import extract_json

GRID = 0.25     # edited notes are put on a sixteenth-note grid


# ---------------------------------------------------------------------------------------------------------------- bars

def bar_count(total_beats, bb):
    return max(1, int(total_beats / bb + 0.99))


def parse_bars(text, n_bars):
    """The bar numbers (1-based) a sentence names: 'measure 2', 'bars 3-4', 'measures 2, 3 and 5', 'bar 2 into bar 3'.
    Numbers outside the piece are ignored."""
    found = set()
    num = r"\d{1,3}(?:\s*(?:-|–|to|through|thru)\s*\d{1,3})?"
    for m in re.finditer(rf"\b(?:measures?|bars?|mm?\.)\s*({num}(?:\s*(?:,|and|&|or)\s*{num})*)", text or "", re.I):
        for part in re.split(r"\s*(?:,|and|&|or)\s*", m.group(1)):
            span = [int(x) for x in re.findall(r"\d+", part)]
            if len(span) == 2:
                found.update(range(min(span), max(span) + 1))
            elif span:
                found.add(span[0])
    return sorted(b for b in found if 1 <= b <= n_bars)


def bar_of(start, bb):
    return int(start // bb + 1e-9) + 1


def _runs(bars):
    """[(first, last)] of consecutive bar numbers."""
    out = []
    for b in sorted(bars):
        if out and b == out[-1][1] + 1:
            out[-1][1] = b
        else:
            out.append([b, b])
    return [tuple(r) for r in out]


def untouched_changes(old, new, targets, bb):
    """Bars outside `targets` whose notes differ between two melodies (empty when the edit kept its promise)."""
    def by_bar(notes):
        d = {}
        for n in notes:
            d.setdefault(bar_of(n[1], bb), []).append(n)
        return d
    a, b = by_bar(old), by_bar(new)
    return sorted(k for k in set(a) | set(b) if k not in targets and a.get(k) != b.get(k))


def splice(old, replacements, targets, bb, total_beats):
    """The melody with the notes of the bars in `replacements` ({bar: [(pitch, beat_in_bar, dur)]}) replaced. A target bar
    missing from it keeps its notes. Every note that starts outside the replaced bars is kept exactly as it was, and the
    result is checked bar by bar before it is returned."""
    keep = [n for n in old if bar_of(n[1], bb) not in replacements]
    kept_starts = sorted(n[1] for n in keep)
    fresh = []
    for bar, items in replacements.items():
        for p, s, d in items:
            s = round(s / GRID) * GRID
            d = max(round(d / GRID) * GRID, GRID)
            if 0 <= s < bb:
                fresh.append((p, (bar - 1) * bb + s, d))
    fresh.sort(key=lambda n: n[1])
    out = []
    for p, s, d in fresh:
        if any(ks < s - 1e-9 and ks + kd > s + 1e-9 for _kp, ks, kd in keep):   # an earlier kept note is still ringing
            continue
        end = min(s + d, bar_of(s, bb) * bb)
        nxt = next((k for k in kept_starts if s + 1e-9 < k < end), None)
        if nxt is not None:
            end = nxt
        if out and abs(out[-1][1] - s) < 1e-9:
            continue
        if end - s >= GRID - 1e-9:
            out.append((p, s, end - s))
    merged = sorted(keep + out, key=lambda n: n[1])
    fixed = []
    for i, (p, s, d) in enumerate(merged):
        if i + 1 < len(merged) and s + d > merged[i + 1][1] + 1e-9 and (p, s, d) not in keep:
            d = merged[i + 1][1] - s
        fixed.append((p, s, d))
    bad = untouched_changes(old, fixed, set(targets), bb)
    if bad:
        raise RuntimeError(f"The edit would have changed bars {bad}, which were not named; nothing was changed")
    return fixed


# ----------------------------------------------------------------------------------------------------- the LLM's part

SYSTEM = """You are a careful music editor. You are given a melody written bar by bar and an instruction from the composer.
Rewrite ONLY the bars you are told to rewrite, so they do what the instruction says. Do not repeat or change any other bar.
Notes are [midi_pitch, start_beat, duration_beats] with start_beat counted from the start of that bar (0 to {bar_beats:g}).
Rules: one note at a time (no overlaps); keep notes inside the bar; use the key's scale; keep the rhythm and style of the
melody unless told otherwise; make the bars connect smoothly to the bars on either side.
If the instruction cannot work without changing a bar you were not given, still rewrite only the given bars as well as you can,
and name the other bars in "suggest_also" with the reason.
Answer with one JSON object and nothing else:
{"bars": {"<bar number>": [[pitch, start_beat, duration_beats], ...]}, "summary": "what you changed, in one sentence", "suggest_also": {"bars": [numbers], "reason": "why"}}
Leave "suggest_also" out when no other bar needs to change."""


def _name(p):
    return f"{'C C# D D# E F F# G G# A A# B'.split()[p % 12]}{p // 12 - 1}"


def _listing(notes, bb, n_bars):
    lines = []
    for b in range(1, n_bars + 1):
        items = [[p, round(s - (b - 1) * bb, 2), round(d, 2)] for p, s, d in notes if bar_of(s, bb) == b]
        names = " ".join(_name(p) for p, _s, _d in notes if bar_of(_s, bb) == b)
        lines.append(f"bar {b}: {json.dumps(items)}" + (f"   ({names})" if names else "   (rest)"))
    return "\n".join(lines)


def llm_rewrite(notes, targets, instruction, sig, key, tempo, llm, level, temp_boost=0.0):
    """Ask the model for new versions of the `targets` bars. Returns ({bar: [(p, beat, dur)]}, summary, suggestion)."""
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler

    model, tok = t._get_llm(llm)
    bb = t.bar_beats(sig)
    n_bars = bar_count(max((s + d for _p, s, d in notes), default=bb), bb)
    runs = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in _runs(targets))
    user = (f"Time signature {sig}, tempo {tempo} bpm."
            + (f" Key: {t.key_label(key)}. The notes of that scale are: {t.scale_names(key)}." if key else "")
            + f"\n\nThe melody:\n{_listing(notes, bb, n_bars)}"
            + f"\n\nInstruction from the composer: {instruction.strip() or '(none: improve these bars)'}"
            + f"\n\nRewrite ONLY bar(s) {runs}. Put exactly those bar numbers in \"bars\".")
    msgs = [{"role": "system", "content": SYSTEM.replace("{bar_beats:g}", f"{bb:g}")},
            {"role": "user", "content": user}]
    text = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
    raw = generate(model, tok, prompt=text, max_tokens=500 + 120 * len(targets),
                   sampler=make_sampler(temp=0.7 + temp_boost, top_p=0.95))
    obj = extract_json(raw, "bars")
    if obj is None or not isinstance(obj["bars"], dict):
        raise ValueError(f"LLM returned no usable JSON:\n{raw[:300]}")
    out = {}
    for k, items in obj["bars"].items():
        try:
            bar = int(re.sub(r"\D", "", str(k)))
        except ValueError:
            continue
        if bar in targets and isinstance(items, list):
            out[bar] = t._clean_notes(items)
    sug = obj.get("suggest_also")
    suggestion = None
    if isinstance(sug, dict):
        extra = sorted({int(b) for b in (sug.get("bars") or []) if str(b).lstrip("-").isdigit()
                        and 1 <= int(b) <= n_bars and int(b) not in targets})
        if extra:
            suggestion = {"bars": extra, "reason": str(sug.get("reason", ""))[:300]}
    return out, str(obj.get("summary", ""))[:300], suggestion


# ------------------------------------------------------------------------------------------------------------- commands

def _write(folder, name, k, notes, tempo, program, sig, total_beats):
    path = folder / f"{name}-opt{k}.mid"
    t.write_melody_midi(path, tempo, notes, program, sig, total_beats)
    return path


def edit_melody(source_midi, instruction, bars=None, count=3, llm="7B", complexity=3, log=print, on_candidate=None,
                on_suggestion=None):
    """Write up to `count` versions of the melody in which only the named bars differ. The bars are the ones the
    instruction names ('measure 2 and 3') together with `bars` (the ones picked in the app, or agreed to after a question). Returns the list of option infos."""
    src = Path(source_midi)
    notes, tempo, sig, program, total = read_melody(src)
    if not notes:
        raise RuntimeError("There is no melody in that file to edit")
    bb = t.bar_beats(sig)
    n_bars = bar_count(total, bb)
    named = parse_bars(instruction, n_bars)
    targets = sorted(set(named) | {int(b) for b in (bars or []) if 1 <= int(b) <= n_bars})
    if not targets:
        raise RuntimeError(f"Say which bars to change, for example \"measure 2\" (the melody has {n_bars} bars)")
    shown = ", ".join(f"{a}" if a == b else f"{a}–{b}" for a, b in _runs(targets))
    log(f"  ▸ Changing bar{'s' if len(targets) > 1 else ''} {shown} only; every other bar is kept exactly as it is.")
    key = t.key_for_file(src) or t.estimate_key(notes)
    span = t.limits(program)[:2]
    folder = t.ensure_session()
    name = time.strftime("%H%M%S") + "-edit"
    total_beats = n_bars * bb
    out, tries, suggestion, summary = [], 0, None, ""
    fails, made = 0, 0
    while len(out) < count and tries < count * 3:
        fails = 0 if len(out) > made else fails + (1 if tries else 0)     # attempts in a row that gave nothing
        made = len(out)
        if fails >= t.MAX_FAILED_ATTEMPTS:
            log(f"      (giving up: {fails} attempts in a row gave nothing usable)")
            break
        tries += 1
        try:
            repl, summary_, sug = llm_rewrite(notes, set(targets), instruction, sig, key, tempo, llm, complexity,
                                              temp_boost=0.1 * len(out))
        except (ValueError, KeyError, TypeError, RuntimeError) as e:
            log(f"      (attempt unusable, retrying: {str(e).splitlines()[0][:70]})")
            continue
        suggestion = suggestion or sug
        fitted = {}
        for bar, items in repl.items():
            base = (bar - 1) * bb
            fit = [(t.fold_pitch(p, program), s_, d) for p, s_, d in items]
            fit = t.limit_outside(fit, key, 10, span) if key and fit else fit
            fitted[bar] = fit
        thin = None if re.search(r"rest|sparse|silen|pause|breath|fewer|simpl|empty|thin|space", instruction, re.I) else \
            next((b for b, items in fitted.items() if sum(d for _p, _s, d in items) < 0.6 * bb
                  and sum(d for n_p, n_s, d in notes if bar_of(n_s, bb) == b) >= 0.6 * bb), None)
        if thin:
            log(f"      (that attempt left bar {thin} mostly empty; trying again)")
            continue
        missing = [b for b in targets if b not in fitted]
        if missing:
            log(f"      (the model gave nothing for bar {missing[0]}; it stays as it was)")
        try:
            new = splice(notes, fitted, set(targets), bb, total_beats)
        except RuntimeError as e:
            log(f"      ({e})")
            continue
        if new == notes:
            log("      (that attempt changed nothing; trying again)")
            continue
        if any(new == _read(o["path"]) for o in out):
            continue
        k = len(out) + 1
        path = _write(folder, name, k, new, tempo, program, sig, total_beats)
        info = {"index": k, "path": path, "tempo": tempo, "sig": sig,
                "score": t.melody_score(new, total_beats, t.COMPLEXITY[complexity][1])[0]}
        out.append(info)
        summary = summary_ or summary
        log(f"      option {k}: {summary_ or 'rewritten'}")
        if on_candidate:
            on_candidate(info)
    if not out:
        raise RuntimeError("No usable edit was produced; try again, or say more about what you want")
    if suggestion and on_suggestion:
        on_suggestion(suggestion, targets)
    return out


def _read(path):
    return read_melody(path)[0]


def save_edit(source_midi, notes, log=print, on_candidate=None):
    """Write the melody as the user edited it by hand. `notes` is [[pitch, start_beat, duration_beats], ...]."""
    src = Path(source_midi)
    old, tempo, sig, program, total = read_melody(src)
    bb = t.bar_beats(sig)
    total_beats = max(bar_count(total, bb) * bb, 0)
    clean, seen = [], set()
    for p, s, d in sorted(((int(a), float(b), float(c)) for a, b, c in notes), key=lambda n: n[1]):
        s = round(s / GRID) * GRID
        d = max(round(d / GRID) * GRID, GRID)
        if 0 <= p <= 127 and s >= 0 and s not in seen:
            seen.add(s)
            clean.append((p, s, d))
    if not clean:
        raise RuntimeError("A melody needs at least one note")
    clean = [(p, s, min(d, clean[i + 1][1] - s) if i + 1 < len(clean) else d) for i, (p, s, d) in enumerate(clean)]
    total_beats = max(total_beats, bar_count(clean[-1][1] + clean[-1][2], bb) * bb)
    folder = t.ensure_session()
    path = _write(folder, time.strftime("%H%M%S") + "-hand", 1, clean, tempo, program, sig, total_beats)
    changed = [b for b in range(1, bar_count(total_beats, bb) + 1)
               if [n for n in old if bar_of(n[1], bb) == b] != [n for n in clean if bar_of(n[1], bb) == b]]
    log(f"  ▸ Saved your edit: {len(clean)} notes; bars changed: {', '.join(map(str, changed)) or 'none'}.")
    info = {"index": 1, "path": path, "tempo": tempo, "sig": sig,
            "score": t.melody_score(clean, total_beats, t.COMPLEXITY[3][1])[0]}
    if on_candidate:
        on_candidate(info)
    return info
