"""The LLM's replies are untrusted text: parsing must tolerate junk and retries must be bounded."""
import pytest

import direction
import text2music as t
from llmjson import extract_json


@pytest.mark.parametrize("raw,expected_notes", [
    ('{"tempo":90,"notes":[[60,0,1],[62,1,1]]}', 2),
    ('Sure! ```json\n{"tempo": 100, "notes": [[60,0,1],[64,1,0.5]]}\n``` hope that helps {not json}', 2),
    ('{"notes": [[60,0,1]], "tempo": 80} and also {"x": 1}', 1),
    ('{"notes": [{"pitch": 60, "start": 0, "duration": 1}, {"pitch": 62, "start": 1, "duration": 1}]}', 2),
    ('{"notes": [[60,0,1], [67], "x", [61,"a",1], [62,1,1], null, [200,0,1]], "tempo": "fast"}', 2),
])
def test_notes_are_recovered_from_messy_replies(raw, expected_notes):
    data = t._json_with_notes(raw)
    assert data is not None
    assert len(t._clean_notes(data["notes"])) == expected_notes


@pytest.mark.parametrize("raw", ['', 'no json here', '{"notes": [[60,0,1],[62,1,1],[64,2', '{"notes": "none"}'])
def test_unusable_replies_give_none(raw):
    assert t._json_with_notes(raw) is None


def test_extract_json_skips_objects_without_the_required_key():
    assert extract_json('{"a": 1} {"notes": []}', "notes") == {"notes": []}


def test_direction_reply_parsing_is_tolerant_too():
    raw = 'Here you go:\n```\n{"summary": "soft pad", "style": "sustained", "density": -1}\n```'
    got = direction.from_llm("soft pad", raw)
    assert got is not None and got["style"] == "sustained" and got["density"] == -1
    assert direction.from_llm("x", "I can't help with that") is None


def _stub(monkeypatch, fail_times):
    calls = {"n": 0}
    good = [(60 + i, i, 1) for i in range(8)]

    def fake(*a, **k):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise ValueError("LLM returned no usable JSON:\nxx")
        return 90, good, "plan", "C major", "reading"

    monkeypatch.setattr(t, "llm_melody", fake)
    return calls


def test_compose_retries_an_unusable_reply(monkeypatch):
    _stub(monkeypatch, 2)
    notes, tempo, _, declared = t.compose("x", 2, "4/4", 3, "7B", lambda s: None, 90, (0, "major"))
    assert notes and tempo == 90 and declared == "C major"


def test_compose_gives_up_with_a_clear_error(monkeypatch):
    calls = _stub(monkeypatch, 99)
    with pytest.raises(RuntimeError, match="usable melody"):
        t.compose("x", 2, "4/4", 3, "7B", lambda s: None, 90, (0, "major"))
    assert calls["n"] == 3   # three tries for the one phrase, not an endless loop


def test_compose_keeps_the_enforced_key_as_a_pair_across_phrases(monkeypatch):
    """Regression: the key the LLM declared used to overwrite the enforced (tonic, mode) pair, so phrase 2 crashed."""
    seen = []

    def fake(prompt, bars, sig, level, llm, prev, phrase, tempo, key, chords=None, guidance="", ending=True):
        seen.append(key)
        return 90, [(60 + i, i, 1) for i in range(bars * 4)], "plan", "C minor", "reading"

    monkeypatch.setattr(t, "llm_melody", fake)
    t.compose("x", 8, "4/4", 3, "7B", lambda s: None, 90, (0, "minor"))
    assert seen and all(k == (0, "minor") for k in seen)


def test_the_log_says_what_the_llm_understood(monkeypatch):
    def fake(*a, **k):
        return 120, [(60 + i, i, 1) for i in range(8)], "a rising motif, climax in bar 2", "D minor", "a calm mood, so slow and low"

    monkeypatch.setattr(t, "llm_melody", fake)
    lines = []
    t.compose("x", 2, "4/4", 3, "7B", lines.append, 90, (0, "major"))
    text = "\n".join(lines)
    assert "understood: a calm mood" in text
    assert "suggested 120 bpm; your 90 bpm is used" in text
    assert "fitted to your C major" in text
    assert "plan for phrase 1: a rising motif" in text


def test_the_log_explains_how_the_prompt_is_used():
    lines = []
    t.explain_prompt("a sad tune", None, 80, 8, 3, "7B", lines.append)
    text = "\n".join(lines)
    assert "Your prompt" in text and "writes every one of the 8 bars" in text and "chosen by the LLM" in text
    lines = []
    t.explain_prompt("a tune in D minor", (7, "major"), 80, 8, 3, "7B", lines.append)
    assert "G major (set by you)" in "\n".join(lines)


def test_the_sliders_are_said_to_the_llm_in_words():
    smooth = t.guidance_text({"shape": 0, "pulse": 0})
    jumpy = t.guidance_text({"shape": 10, "pulse": 10})
    assert "mostly by step" in smooth and "syncopated" in smooth
    assert "angular" in jumpy and "square and regular" in jumpy
    assert "Theme" not in t.guidance_text({"theme": 8})                  # no motif yet, so nothing to restate
    assert "opening idea" in t.guidance_text({"theme": 8}, [(60, 0, 1)])
    assert "Theme" not in t.guidance_text({"theme": 0}, [(60, 0, 1)])
