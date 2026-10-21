"""Engraving: MIDI -> MusicXML -> HTML/SVG, including grand staff for piano and the paged print layout."""
import random

import composer
import notation
import text2music as t


def make_midi(out_dir, program, bars=8, name="m"):
    notes, _ = composer.compose(bars, "4/4", (7, "major"), 3, random.Random(2), 55, 84, 1, 1)
    notes, _ = t.conform_melody(notes, bars, "4/4")
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
