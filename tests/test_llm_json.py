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

    def fake(prompt, bars, sig, level, llm, prev, phrase, tempo, key, chords=None, guidance="", ending=True, climax=True):
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


def test_the_runs_slider_is_said_to_the_llm_and_zero_says_nothing():
    assert "three notes" in t.guidance_text({"runs": 10})
    assert "Scale runs" not in t.guidance_text({"runs": 0})
    assert "Scale runs" in t.guidance_text({"shape": 0, "runs": 10}), "a smooth tune can still avoid runs"


def test_an_automatic_slider_gets_no_instruction():
    assert "Scale runs" in t.guidance_text({"runs": 10})
    assert "Scale runs" not in t.guidance_text({"runs": "auto"})
    assert "angular" not in t.guidance_text({"shape": "auto"}) and "mostly by step" not in t.guidance_text({"shape": "auto"})
    s = t.plan.clean_sliders({"shape": "auto", "pulse": 7})
    assert s["auto"] == ["shape"] and s["shape"] == t.plan.SLIDER_DEFAULTS["shape"], "the sampler still gets a number"
    assert t.plan.clean_sliders(s)["auto"] == ["shape"], "cleaning twice keeps it"


def test_a_direction_leads_the_prompt_and_is_spread_over_the_parts(monkeypatch):
    seen = []

    def fake(ask, n_bars, *args, **kw):
        seen.append((ask, kw))
        return 100, [(60 + i % 5, i, 1.0) for i in range(int(n_bars * 4))], "", "", ""

    monkeypatch.setattr(t, "llm_melody", fake)
    t.compose("a bright tune", 8, "4/4", 3, "7B", lambda s: None, 100, (0, "major"), extra="build to a climax")
    assert seen[0][0].startswith("INSTRUCTION FROM THE USER") and "build to a climax" in seen[0][0]
    assert "part 1 of 2" in seen[0][0] and "part 2 of 2" in seen[1][0]
    assert all(kw.get("climax") is False for _, kw in seen), "the built-in 'climax' line must not fight the direction"
    seen.clear()
    t.compose("a bright tune", 4, "4/4", 3, "7B", lambda s: None, 100, (0, "major"))
    assert seen[0][0].startswith("a bright tune") and seen[0][1].get("climax") is True


def test_a_prompt_that_names_a_mode_but_no_note_sets_the_mode(monkeypatch):
    seen = []

    def fake(*a, **k):
        seen.append(a[0])
        return 100, [(60 + i % 4, i, 1.0) for i in range(16)], "", "", ""

    monkeypatch.setattr(t, "llm_melody", fake)
    monkeypatch.setattr(t, "pick_key", lambda prompt, llm, log: (7, "major"))      # the LLM picks G major
    monkeypatch.setattr(t, "explain_prompt", lambda *a, **k: None)
    got = list(t.melody_candidates("a bright scale in lydian mode", 4, "4/4", 3, "7B", 73, 1, lambda s: None))
    assert got[0]["key"] == (7, "lydian"), "G, from the LLM, in the mode the prompt asked for"
    got = list(t.melody_candidates("a bright scale in lydian mode", 4, "4/4", 3, "7B", 73, 1, lambda s: None, mode="dorian"))
    assert got[0]["key"] == (7, "dorian"), "the picker wins over the prompt"
