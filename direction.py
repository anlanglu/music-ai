"""Turn a short plain-English direction for an accompaniment part ("gentle arpeggios, low and sparse") into the
controls the generators actually have. A local LLM interprets the text (see SYSTEM / from_llm); when the LLM is
switched off or unavailable, `parse` falls back to a fixed keyword vocabulary that is instant and deterministic.

Controls:
  style    a rule-based pattern (arpeggio, alberti, block, oompah, sustained, bassline, walking) instead of the model
  density  -1 sparse .. +1 busy        register  -1 low .. +1 high
  length   'long' (held notes) or 'short' (detached)
  rhythm   'syncopated' (off the beat) or 'straight' (on the beat)
  velocity how hard the part is played (soft / loud)
"""
import re

# (label shown back to the user, keywords/phrases). Longer phrases are tried first.
_STYLE = [
    ("walking bass line", "walking", ["walking bass", "walking"]),
    ("bass line", "bassline", ["bass line", "bassline", "root notes", "root note", "roots only"]),
    ("arpeggios", "arpeggio", ["arpeggio", "arpeggiated", "arpeggios", "broken chord", "broken chords"]),
    ("Alberti bass", "alberti", ["alberti"]),
    ("oom-pah / waltz pattern", "oompah", ["oom-pah", "oompah", "oom pah", "waltz"]),
    ("sustained chords", "sustained", ["sustained chord", "sustained chords", "pad", "pads", "drone", "held chords"]),
    ("block chords", "block", ["block chord", "block chords", "chords", "chordal", "comping", "stabs"]),
]
_DENSITY = [
    (-1, "sparse", ["sparse", "minimal", "minimalist", "spacious", "airy", "few notes", "simple", "less busy",
                    "slow moving", "slow-moving", "restrained"]),
    (1, "busy", ["busy", "dense", "active", "lively", "driving", "running", "many notes", "intricate", "more notes",
                 "fast", "energetic", "flowing", "moving"]),
]
_REGISTER = [
    (-1, "low register", ["low", "deep", "lower", "dark", "in the bass", "rumbling"]),
    (1, "high register", ["high", "bright", "upper", "sparkling", "soprano", "shimmering"]),
]
_LENGTH = [
    ("long", "long, held notes", ["long notes", "held", "legato", "sustained", "smooth", "ringing", "sustain"]),
    ("short", "short, detached notes", ["staccato", "short", "detached", "plucked", "pizzicato", "choppy", "crisp",
                                         "stabs", "bouncy"]),
]
_RHYTHM = [
    ("syncopated", "syncopated", ["syncopated", "syncopation", "off-beat", "offbeat", "off beat", "groovy", "funky"]),
    ("straight", "steady, on the beat", ["steady", "straight", "on the beat", "marching", "march", "regular"]),
]
_VELOCITY = [
    (45, "soft", ["soft", "quiet", "gentle", "delicate", "pianissimo", "subtle", "whisper", "tender", "calm"]),
    (105, "loud", ["loud", "strong", "forte", "powerful", "bold", "fortissimo", "dramatic", "intense"]),
]


def _has(text, words):
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", text) for w in sorted(words, key=len, reverse=True))


def _blank(text):
    t = " ".join((text or "").lower().split())
    return {"style": None, "double_interval": None, "density": 0, "register": 0, "length": 0, "rhythm": 0, "dynamics": 0,
            "adventurous": 0, "complementary": False, "understood": [], "empty": not t, "text": t, "source": "keywords",
            "summary": ""}


def parse(text):
    """Keyword fallback: the same controls as the LLM interpreter produces, from a fixed vocabulary. Used when the
    LLM is switched off or unavailable. Values: density / register / length / rhythm / dynamics are -2..2 (0 = default;
    rhythm -2 = syncopated, +2 = steady), style is a pattern name or None."""
    out = _blank(text)
    t = out["text"]
    if not t:
        return out
    for label, style, words in _STYLE:
        if _has(t, words):
            out["style"] = style
            out["understood"].append(label)
            break
    for value, label, words in _DENSITY:
        if _has(t, words):
            out["density"] = value
            out["understood"].append(label)
            break
    for value, label, words in _REGISTER:
        if _has(t, words):
            out["register"] = value
            out["understood"].append(label)
            break
    for value, label, words in _LENGTH:
        if _has(t, words):
            out["length"] = 1 if value == "long" else -1
            out["understood"].append(label)
            break
    for value, label, words in _RHYTHM:
        if _has(t, words):
            out["rhythm"] = -1 if value == "syncopated" else 1
            out["understood"].append(label)
            break
    for value, label, words in _VELOCITY:
        if _has(t, words):
            out["dynamics"] = -1 if value < 72 else 1
            out["understood"].append(label)
            break
    out["summary"] = ", ".join(out["understood"])
    return out


# ---------------------------------------------------------------------------------------------------------------
# LLM interpretation: free text in, the same controls out.

STYLES = ["arpeggio", "alberti", "block", "oompah", "sustained", "bassline", "walking", "pulse", "double"]

SYSTEM = """You translate a musician's free-text instructions for ONE accompaniment part into JSON controls for a
music generator. Reply with ONLY a JSON object, no other text, with exactly these keys (use null for anything the
instruction does not say; never invent preferences):
{"summary": "<one short sentence: what you understood the part should do>",
 "style": null | "arpeggio" | "alberti" | "block" | "oompah" | "sustained" | "bassline" | "walking" | "pulse" | "double",
 "double_interval": null | <semitones, only with style "double": -12 = an octave below, 12 = an octave above, -3 / -4 = a third below, -8 / -9 = a sixth below, 7 = a fifth above>,
 "density": null | -2..2,        -2 very sparse, 0 normal, 2 very busy
 "register": null | -2..2,       -2 very low in the instrument's range, 2 very high
 "note_length": null | -2..2,    -2 very short / staccato, 2 very long / legato
 "rhythm_feel": null | -2..2,    -2 heavily syncopated / off the beat, 2 very steady / on the beat
 "dynamics": null | -2..2,       -2 very soft, 2 very loud
 "complementary": null | true,   true if it should answer or fill the gaps of the melody instead of playing along
 "adventurous": null | -2..2}    -2 safe and predictable, 2 surprising and experimental
Meaning of "style": arpeggio = broken chords; alberti = low-high-middle-high figure; block = chords struck on the beat;
oompah = bass note then chords; sustained = held chords or a pad or drone; bassline = root-and-fifth bass notes;
walking = a walking bass line; pulse = repeated chords in steady eighth notes; double = play the melody itself at an
interval. Pick a style only if the instruction clearly asks for that kind of part. Counter-melodies, responses,
fills and "conversation with the melody" are complementary=true (no style). "double_interval" is only for style
"double". If the instruction says nothing about how the music should sound or be played (a random word, a question,
something unrelated), return every control as null and say so in the summary: do not guess.

Examples:
Instruction: "something dreamy that floats underneath, nothing busy"
{"summary": "Soft, slow, held background that stays out of the way", "style": "sustained", "double_interval": null, "density": -2, "register": null, "note_length": 2, "rhythm_feel": null, "dynamics": -1, "complementary": null, "adventurous": null}
Instruction: "play the tune along with it an octave lower, quietly"
{"summary": "Doubles the melody an octave below, quietly", "style": "double", "double_interval": -12, "density": null, "register": null, "note_length": null, "rhythm_feel": null, "dynamics": -1, "complementary": null, "adventurous": null}
Instruction: "answer the melody in the gaps, a bit cheeky and high up"
{"summary": "A playful high counter-line that fills the melody's gaps", "style": null, "double_interval": null, "density": -1, "register": 1, "note_length": -1, "rhythm_feel": -1, "dynamics": null, "complementary": true, "adventurous": 1}
Instruction: "driving and urgent, hammering away on the beat"
{"summary": "Busy, loud, steady repeated chords", "style": "pulse", "double_interval": null, "density": 2, "register": null, "note_length": -1, "rhythm_feel": 2, "dynamics": 2, "complementary": null, "adventurous": null}
Instruction: "purple"
{"summary": "Nothing musical to act on", "style": null, "double_interval": null, "density": null, "register": null, "note_length": null, "rhythm_feel": null, "dynamics": null, "complementary": null, "adventurous": null}"""


def _num(v, lo=-2, hi=2):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def from_llm(text, raw):
    """Turn the LLM's raw reply into the controls dict, or None if it can't be used."""
    from llmjson import extract_json

    j = extract_json(raw)
    if j is None:
        return None
    out = _blank(text)
    out["source"] = "llm"
    style = j.get("style")
    out["style"] = style if style in STYLES else None
    try:
        out["double_interval"] = int(j["double_interval"]) if out["style"] == "double" and j.get("double_interval") is not None else None
    except (TypeError, ValueError):
        out["double_interval"] = None
    for key, name in (("density", "density"), ("register", "register"), ("note_length", "length"),
                      ("rhythm_feel", "rhythm"), ("dynamics", "dynamics"), ("adventurous", "adventurous")):
        out[name] = _num(j.get(key))
    out["complementary"] = bool(j.get("complementary"))
    out["summary"] = str(j.get("summary") or "").strip()
    return out


def has_effect(d):
    return bool(d["style"] or d["complementary"] or any(d[k] for k in ("density", "register", "length", "rhythm", "dynamics", "adventurous")))


def build_prompt(text, instrument, context):
    return f"Instrument to write: {instrument}.\nThe piece so far: {context}\nInstruction: \"{text.strip()}\""


def describe(d):
    """One line telling the user what we took from their direction (and how)."""
    if d["empty"]:
        return ""
    how = "Understood" if d["source"] == "llm" else "Matched keywords"
    if not has_effect(d):
        return (f"Couldn't turn \"{d['text']}\" into anything the generators can do, so the default part was generated."
                + ("" if d["source"] == "llm" else " (LLM off or unavailable: only a fixed vocabulary is understood.)"))
    bits = (d["summary"] or ", ".join(d["understood"])).rstrip(". ")
    return f"{how}: {bits}."
