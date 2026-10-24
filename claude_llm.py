"""Claude (Anthropic API) as an alternative melody LLM. This is the one cloud option: the prompt, the melody so far and the
instructions are sent to Anthropic. The key is held in memory only (the app keeps it in the macOS Keychain and sends it at
start-up); it is never written to disk or to the log."""
import os
import shutil
import subprocess
import tempfile

import anthropic

MODELS = {   # dropdown id -> label
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
}
CODE_MODELS = {   # dropdown id -> (label, --model value): the `claude` command-line tool, signed in with your subscription
    "cc-haiku-5-5": ("Claude Haiku 5.5", "claude-haiku-5-5"),
    "cc-sonnet-5-5": ("Claude Sonnet 5.5", "claude-sonnet-5-5"),
    "cc-opus-5-5": ("Claude Opus 5.5", "claude-opus-5-5"),
}
_key = None


def cli_path():
    return shutil.which("claude") or next((p for p in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude", os.path.expanduser("~/.local/bin/claude")) if os.path.exists(p)), None)


def complete_cli(model, msgs, timeout=300):
    """One reply from `claude -p` (Claude Code, signed in with your subscription: no API key, no per-request billing,
    but it counts against the subscription's usage limits). Runs with no tools, in an empty folder, with no session saved."""
    exe = cli_path()
    if not exe:
        raise RuntimeError("The `claude` command-line tool wasn't found. Install Claude Code and sign in with `claude`.")
    system = "\n\n".join(m["content"] for m in msgs if m["role"] == "system")
    user = "\n\n".join(m["content"] for m in msgs if m["role"] != "system")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}   # never bill an API key by accident
    try:
        with tempfile.TemporaryDirectory() as cwd:
            r = subprocess.run([exe, "-p", "--model", CODE_MODELS[model.name][1], "--system-prompt", system, "--tools", "",
                                "--no-session-persistence", "--disable-slash-commands", "--output-format", "text"],
                               input=user, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude Code took too long to answer (over five minutes).") from None
    if r.returncode != 0 or not r.stdout.strip():
        detail = (r.stderr or r.stdout).strip().splitlines()
        raise RuntimeError("Claude Code failed: " + (detail[-1] if detail else f"exit {r.returncode}") +
                           ". If it asks you to log in, run `claude` in Terminal once.")
    return r.stdout.strip()


class ClaudeModel:
    def __init__(self, name):
        self.name = name


class ClaudeTok:
    """Stands in for the MLX tokenizer: the 'prompt' is simply the message list."""
    def apply_chat_template(self, msgs, **_kw):
        return msgs


def is_claude(key):
    return str(key).startswith(("claude-", "cc-"))


def set_key(key):
    global _key
    _key = (key or "").strip() or None


def has_key():
    return _key is not None


def complete(model, msgs, max_tokens):
    """One reply as text. No temperature or top_p: the current Claude models fix their own sampling."""
    if _key is None:
        raise RuntimeError("No Claude API key set. Add one under Melody LLM → Claude API key.")
    system = "\n\n".join(m["content"] for m in msgs if m["role"] == "system")
    chat = [m for m in msgs if m["role"] != "system"]
    client = anthropic.Anthropic(api_key=_key, max_retries=2, timeout=180)
    try:
        r = client.messages.create(
            model=model.name, system=system, messages=chat,
            max_tokens=min(16000, max(8000, max_tokens * 4)),      # thinking tokens count against this, so leave room
            output_config={"effort": "medium"},
        )
    except anthropic.AuthenticationError:
        raise RuntimeError("Anthropic rejected the API key. Check it under Melody LLM → Claude API key.") from None
    except anthropic.PermissionDeniedError as e:
        raise RuntimeError(f"This API key can't use {model.name}: {e.message}") from None
    except anthropic.RateLimitError:
        raise RuntimeError("Anthropic rate limit reached; wait a moment and try again.") from None
    except anthropic.APIConnectionError:
        raise RuntimeError("Couldn't reach Anthropic. Check the internet connection.") from None
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Anthropic API error {e.status_code}: {e.message}") from None
    if r.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    return "".join(b.text for b in r.content if b.type == "text")


def check(key):
    """('ok'|'error', message): one tiny request with the cheapest model."""
    set_key(key)
    try:
        complete(ClaudeModel("claude-haiku-5-5"), [{"role": "user", "content": "Reply with the word ok."}], 50)
        return True, "Connected."
    except RuntimeError as e:
        return False, str(e)
