"""Settings and tables shared by the whole composer: sampling knobs, the LLM list, GM instrument names, MIDI conventions."""
import os
from pathlib import Path


from playable import DRUMS

# Hugging Face downloads live on the T7 drive when it is plugged in (else the default ~/.cache/huggingface); set before torch/mlx import
if not os.environ.get("HF_HUB_CACHE") and Path("/Volumes/T7/AIModels/huggingface-hub").is_dir():
    os.environ["HF_HUB_CACHE"] = "/Volumes/T7/AIModels/huggingface-hub"


# How much of the piece's plan the melody follows (see plan.py): the harmony of every bar, the motif restated at
# each phrase, and a cadence at the end of every phrase. The "Form" slider in the app sets this.
DEFAULT_FORM = 2               # 0 free, 1 harmony, 2 cadences, 3 the whole plan
# How the accompaniment is sampled. Fixed on purpose: they are safeguards, not musical choices.
TOP_P = 0.98         # nucleus sampling: nearly the model's own distribution (a part direction may make it safer or bolder)
ATTEMPTS = 8         # resamples when the model returns nothing or a very sparse part
MIN_DENSITY = 1.5    # notes per second below which an automatic accompaniment counts as sparse
BEAT_BOOST = 1.0     # how strongly onsets favour the beat (a part direction may change it)
PLAN_TARGET_TOL = 1.0          # how far (in beats) a note may land from a target and still be pulled
PLAN_TARGET_SIGMA = 1.0        # how wide the pull is, in semitones (measured: a narrow pull brings the idea back,
                               # a wide one lets the model's own preferences win)

LLMS = {
    "7B": "mlx-community/Qwen2.5-7B-Instruct-4bit",
    "14B": "mlx-community/Qwen2.5-14B-Instruct-4bit",  # ~8 GB download, richer melodies, slower
    "3B": "mlx-community/Qwen2.5-3B-Instruct-4bit",    # ~1.8 GB: plenty for reading directions, light on memory
    "mistral24": "mlx-community/Mistral-Small-24B-Instruct-2501-3bit",  # ~10 GB: the biggest that fits a 16 GB Mac next to the music model
}
ANT = "stanford-crfm/music-{size}-800k"

SYSTEM = """You are a composer. Reply with ONLY a JSON object, no prose, in this form:
{"reading": "<one sentence: what you understood the request to ask for (mood, imagery, character) and how you used it: tempo, key, rhythm, contour>",
 "plan": "<one or two sentences: the motif, how it develops, where the climax is>",
 "key": "<tonic> <mode>, the key the notes are actually in (e.g. D minor, or G lydian when a mode is asked for)", "tempo": <bpm int>,
 "notes": [[midi_pitch, start_beat, duration_beats], ...]}
Rules: a single monophonic melody in {sig} time (one bar = {bar_beats:g} quarter-note beats), pitches
{lo}-{hi}, start_beat and duration_beats in quarter-note beats (multiples of 0.25), notes sorted by
start_beat and never overlapping; gaps between notes are rests. The first note starts at beat 0.
Cover exactly the requested number of bars and end on a stable note.
Style: {style}"""

COMPLEXITY = {
    1: ("Simple and singable: mostly quarter and half notes, stepwise motion.", 0.6, (57, 79)),
    2: ("Mostly stepwise with some small leaps; mix quarter and eighth notes; one repeated motif.", 0.9, (55, 81)),
    3: ("Varied rhythm (quarters, eighths, dotted rhythms, a few rests), occasional leaps up to a sixth. "
        "State a motif, repeat it varied, and build to a high point.", 1.3, (53, 84)),
    4: ("Rich and expressive: eighth and sixteenth notes, syncopation, dotted rhythms, rests, leaps up to an "
        "octave. Develop the motif by sequence, inversion and rhythmic variation across clear phrases (like "
        "A A' B A''), with a distinct climax and a range of about an octave and a half.", 1.8, (50, 88)),
    5: ("Virtuosic: dense sixteenth-note runs, arpeggios, chromatic passing tones, syncopation, rests and "
        "ornaments; at least two octaves of range; several contrasting phrases with a dramatic climax. "
        "Every bar should have different rhythm from its neighbour.", 2.4, (48, 91)),
}
TEMPERATURE = {1: 0.6, 2: 0.7, 3: 0.8, 4: 0.9, 5: 1.0}

# Accompaniment instruments in priority order (GM programs; 128 = drums). Asking for N instruments
# uses the first N of these, skipping whichever one is playing the melody.
ENSEMBLE = [32, 48, 0, 128, 42, 40, 73, 71, 60, 56, 46, 24, 41, 68, 12, 52]

TIME_SIGS = ["4/4", "3/4", "2/4", "6/8", "5/4", "7/8", "12/8"]


def bar_beats(sig):
    """Length of one bar in quarter-note beats."""
    num, den = (int(x) for x in sig.split("/"))
    return num * 4 / den

GM_NAMES = ["Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet", "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica", "Tango Accordion", "Nylon Guitar", "Steel Guitar", "Jazz Guitar", "Clean Guitar", "Muted Guitar", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass", "Finger Bass", "Pick Bass", "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani", "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina", "Square Lead", "Saw Lead", "Calliope Lead", "Chiff Lead", "Charang Lead", "Voice Lead", "Fifths Lead", "Bass + Lead", "New Age Pad", "Warm Pad", "Polysynth Pad", "Choir Pad", "Bowed Pad", "Metallic Pad", "Halo Pad", "Sweep Pad", "Rain FX", "Soundtrack FX", "Crystal FX", "Atmosphere FX", "Brightness FX", "Goblins FX", "Echoes FX", "Sci-Fi FX", "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot"]


MAX_FAILED_ATTEMPTS = 3      # expanding or editing stops after this many unusable attempts in a row, so it cannot loop for long


VOCAL_CHANNEL = 1       # the sung line of a melody file (see lyrics.py) plays on this channel, as the Voice instrument (program 53)
VOCAL_PROGRAM = 53


ACC_PREFIX = "acc:"     # the name of a track that holds one instrument of the accompaniment ("layer") in a melody file
DRUM_PROGRAM = 128


def instrument_name(program):
    return "Drums" if program == DRUMS else GM_NAMES[program]
