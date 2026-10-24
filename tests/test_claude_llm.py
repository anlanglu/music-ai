"""Claude as a melody LLM: the request is shaped correctly and a missing key is a clear error (no network used)."""
import types

import pytest

import claude_llm
import text2music as t


class FakeClient:
    seen = {}

    def __init__(self, **kw):
        FakeClient.seen["client"] = kw
        self.messages = types.SimpleNamespace(create=self.create)

    def create(self, **kw):
        FakeClient.seen["call"] = kw
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="thinking"),
                                                                       types.SimpleNamespace(type="text", text='{"ok": 1}')])


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(claude_llm.anthropic, "Anthropic", FakeClient)
    claude_llm.set_key("sk-ant-test")
    yield FakeClient.seen
    claude_llm.set_key(None)


def test_a_claude_id_gives_the_claude_model_without_loading_anything():
    model, tok = t._get_llm("claude-sonnet-5-5")
    assert isinstance(model, claude_llm.ClaudeModel) and model.name == "claude-sonnet-5-5"
    assert tok.apply_chat_template([{"role": "user", "content": "x"}], add_generation_prompt=True) == [{"role": "user", "content": "x"}]


def test_the_system_prompt_goes_in_system_and_no_sampling_options_are_sent(fake):
    model, tok = t._get_llm("claude-opus-5-5")
    msgs = [{"role": "system", "content": "be a composer"}, {"role": "user", "content": "a tune"}]
    out = t._generate(model, tok, tok.apply_chat_template(msgs), 700, 0.8, 0.95)
    assert out == '{"ok": 1}'                                     # only the text block, not the thinking
    call = fake["call"]
    assert call["system"] == "be a composer" and call["messages"] == [{"role": "user", "content": "a tune"}]
    assert "temperature" not in call and "top_p" not in call
    assert call["max_tokens"] >= 8000                             # room for thinking as well as the answer


def test_no_key_is_a_clear_error():
    claude_llm.set_key(None)
    with pytest.raises(RuntimeError, match="API key"):
        claude_llm.complete(claude_llm.ClaudeModel("claude-haiku-5-5"), [{"role": "user", "content": "x"}], 100)


def test_the_key_check_reports_failure_as_text(monkeypatch):
    monkeypatch.setattr(claude_llm, "complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Anthropic rejected the API key.")))
    assert claude_llm.check("bad") == (False, "Anthropic rejected the API key.")
    claude_llm.set_key(None)


def test_the_claude_code_route_runs_the_cli_without_tools_or_an_api_key(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return types.SimpleNamespace(returncode=0, stdout=' {"ok": 1}\n', stderr="")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-passed")
    monkeypatch.setattr(claude_llm, "cli_path", lambda: "/bin/claude")
    monkeypatch.setattr(claude_llm.subprocess, "run", fake_run)
    model, tok = t._get_llm("cc-sonnet-5-5")
    msgs = [{"role": "system", "content": "be a composer"}, {"role": "user", "content": "a tune"}]
    assert t._generate(model, tok, tok.apply_chat_template(msgs), 700, 0.8, 0.95) == '{"ok": 1}'
    cmd = seen["cmd"]
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5" and cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--system-prompt") + 1] == "be a composer" and seen["input"] == "a tune"
    assert "ANTHROPIC_API_KEY" not in seen["env"]


def test_the_claude_code_route_explains_a_failure(monkeypatch):
    monkeypatch.setattr(claude_llm, "cli_path", lambda: "/bin/claude")
    monkeypatch.setattr(claude_llm.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="Not logged in"))
    with pytest.raises(RuntimeError, match="Not logged in"):
        claude_llm.complete_cli(claude_llm.ClaudeModel("cc-sonnet-5-5"), [{"role": "user", "content": "x"}])
