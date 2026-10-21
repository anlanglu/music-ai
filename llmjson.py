"""Pull a JSON object out of a language model's reply. Small models wrap the answer in prose or code fences and
sometimes trail off, so a parse is tried at every '{' instead of assuming first-brace-to-last-brace is valid JSON."""
import json
import re


def extract_json(raw, must_have=None):
    """The first JSON object in `raw` (a dict that has the key `must_have`, if given), else None."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", raw or ""):
        try:
            obj, _ = dec.raw_decode(raw[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and (must_have is None or must_have in obj):
            return obj
    return None
