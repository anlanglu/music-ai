"""Things the Swift app and the Python backend must agree on, checked from the Python side."""
import re
from pathlib import Path

import direction

ROOT = Path(__file__).resolve().parent.parent
VIEWMODEL = (ROOT / "MusicAI/Sources/MusicAI/ViewModel.swift").read_text()


def swift_direction_prefixes():
    m = re.search(r"directionPrefixes\s*=\s*\[([^\]]*)\]", VIEWMODEL)
    assert m, "ViewModel.swift no longer declares directionPrefixes"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_every_direction_report_starts_with_a_prefix_the_app_recognises():
    prefixes = swift_direction_prefixes()
    llm = direction.from_llm("soft pad", '{"summary": "a soft pad", "style": "sustained"}')
    keywords = direction.parse("gentle arpeggios")
    nothing = direction.from_llm("purple", '{"summary": "Nothing musical to act on"}')
    for d in (llm, keywords, nothing):
        line = direction.describe(d)
        assert any(line.startswith(p) for p in prefixes), f"the app would not show: {line!r}"


def test_backend_events_the_app_decodes_are_documented():
    doc = (ROOT / "backend.py").read_text().split('"""')[1]
    swift = (ROOT / "MusicAI/Sources/MusicAI/Backend.swift").read_text()
    for ev in re.findall(r'case "(\w+)":\s*return', swift):
        assert ev in doc, f"Backend.swift decodes '{ev}' but backend.py's header does not list it"
