"""Claude (Anthropic API) as an alternative melody LLM. This is the one cloud option: the prompt, the melody so far and the
instructions are sent to Anthropic. The key is held in memory only (the app keeps it in the macOS Keychain and sends it at
start-up); it is never written to disk or to the log."""
import anthropic

MODELS = {   # dropdown id -> label
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
}
_key = None


class ClaudeModel:
    def __init__(self, name):
        self.name = name


class ClaudeTok:
    """Stands in for the MLX tokenizer: the 'prompt' is simply the message list."""
    def apply_chat_template(self, msgs, **_kw):
        return msgs


def is_claude(key):
    return str(key).startswith("claude-")


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
