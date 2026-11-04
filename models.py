"""The language models and the Anticipatory Music Transformer, loaded on first use and cached."""

import torch

from config import ANT, LLMS


# Loading either model takes a while, so keep them around between runs (matters for the GUI).
_llm = {}
_ant = {}


def _get_llm(key="7B"):
    import claude_llm

    if claude_llm.is_claude(key):   # nothing to load: the `claude` tool runs it
        return claude_llm.ClaudeModel(key), claude_llm.ClaudeTok()
    if key not in _llm:
        from mlx_lm import load

        _llm.clear()  # only one LLM fits comfortably in memory next to the music model
        try:          # give the old model's memory back before the new one loads (the big ones need the room)
            import gc
            import mlx.core as mx
            gc.collect()
            mx.clear_cache()
        except Exception:
            pass
        _llm[key] = load(LLMS[key])
    return _llm[key]


def _generate(model, tok, prompt, max_tokens, temp=None, top_p=None):
    """One reply from whichever LLM `_get_llm` returned: local (MLX) or Claude."""
    import claude_llm

    if isinstance(model, claude_llm.ClaudeModel):
        return claude_llm.complete_cli(model, prompt)
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler

    kw = {"sampler": make_sampler(temp=temp, top_p=top_p)} if temp is not None else {}
    return generate(model, tok, prompt=prompt, max_tokens=max_tokens, **kw)


def _get_ant(size):
    if size not in _ant:
        from transformers import AutoModelForCausalLM

        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        _ant[size] = AutoModelForCausalLM.from_pretrained(ANT.format(size=size)).to(dev)
    return _ant[size]
