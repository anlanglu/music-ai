"""Claude as a melody LLM, through the `claude` command-line tool: the call is shaped correctly (no network used)."""
import types

import pytest

import claude_llm
import text2music as t


def test_a_cc_id_gives_the_claude_model_without_loading_anything():
    model, tok = t._get_llm("cc-sonnet-5-5")
    assert isinstance(model, claude_llm.ClaudeModel) and model.name == "cc-sonnet-5-5"
    assert tok.apply_chat_template([{"role": "user", "content": "x"}], add_generation_prompt=True) == [{"role": "user", "content": "x"}]


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
