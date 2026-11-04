"""Claude as an alternative melody LLM, through the `claude` command-line tool (Claude Code, signed in with your
subscription). This is the one cloud option: the prompt, the melody so far and the instructions are sent to Anthropic."""
import os
import shutil
import subprocess
import tempfile

CODE_MODELS = {   # dropdown id -> (label, --model value): the `claude` command-line tool, signed in with your subscription
    "cc-haiku-5-5": ("Claude Haiku 5.5", "claude-haiku-5-5"),
    "cc-sonnet-5-5": ("Claude Sonnet 5.5", "claude-sonnet-5-5"),
    "cc-opus-5-5": ("Claude Opus 5.5", "claude-opus-5-5"),
}


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
    return str(key).startswith("cc-")
