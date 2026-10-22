"""Engraving: MIDI -> MusicXML -> HTML/SVG, including grand staff for piano and the paged print layout."""
import random
import sys
from pathlib import Path

import notation
import text2music as t


def make_midi(out_dir, program, bars=8, name="m"):
    sys.path.insert(0, str(Path(__file__).parent))
    from stub_backend import fake_notes

    notes, _ = t.conform_melody(fake_notes(bars, "4/4", (7, "major"), random.Random(2)), bars, "4/4")
    path = out_dir / f"{name}.mid"
    t.write_melody_midi(path, 90, notes, program, "4/4", bars * 4)
    return path


def test_renders_html_and_musicxml(out_dir):
    path = make_midi(out_dir, 73)
    html = notation.render(str(path), "Test piece")
    text = open(html, encoding="utf-8").read()
    assert "<svg" in text
    xml = path.with_suffix(".musicxml")
    assert xml.exists() and "<score-partwise" in xml.read_text()


def test_piano_is_engraved_on_a_grand_staff(out_dir):
    path = make_midi(out_dir, 0, name="piano")
    notation.render(str(path), "Piano")
    xml = path.with_suffix(".musicxml").read_text()
    assert "<staves>2</staves>" in xml


def test_paged_layout_has_a4_pages(out_dir):
    path = make_midi(out_dir, 73, bars=16, name="paged")
    html = open(notation.render(str(path), "Paged", paged=True), encoding="utf-8").read()
    assert 'class="page' in html or "class='page" in html


def test_the_dynamics_are_printed_as_markings_and_hairpins(tmp_path):
    """A melody that starts soft and ends loud shows a marking where it settles at a new level, with a hairpin between."""
    import re
    import mido

    notes = [(60 + (i % 5), float(i), 1.0) for i in range(32)]            # 8 bars of 4/4
    path = tmp_path / "m.mid"
    t.write_melody_midi(path, 100, notes, 73, "4/4", 32)
    mid = mido.MidiFile(str(path))
    for trk in mid.tracks:
        seen = 0
        for m in trk:
            if m.type == "note_on" and m.velocity > 0:
                m.velocity = 45 if seen < 16 else 100                    # soft for four bars, then loud
                seen += 1
    mid.save(str(path))
    notation.render(path, "dyn")
    xml = (tmp_path / "m.musicxml").read_text()
    marks = re.findall(r"<dynamics[^>]*>\s*<(\w+)\s*/>", xml)
    assert marks[0] in ("pp", "p") and marks[-1] in ("f", "ff"), marks
    assert "<wedge" in xml, "a crescendo hairpin between the two levels"
