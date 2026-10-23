import SwiftUI

/// The piano roll as a note editor (step 3). Notes are in beats, so the bars line up with the sheet music and with the
/// bar numbers you give the LLM. Click empty space to add a note, drag a note to move it, drag its right edge to change
/// its length, Delete removes it, and the arrow keys nudge it (Shift: an octave). Nothing is saved until Save changes.
struct MelodyEditor: View {
    @Binding var notes: [EditNote]
    @Binding var selected: UUID?
    let beatsPerBar: Double

    private let ppb: CGFloat = 46          // points per beat
    private let ruler: CGFloat = 20
    private let grid = 0.25                // sixteenth notes
    @StateObject private var track = Tracker()
    @FocusState private var focused: Bool

    private struct Drag {
        enum Mode { case move, resize }
        let id: UUID, mode: Mode
        let start: Double, dur: Double, pitch: Int
    }

    /// What the mouse is doing right now. Plain storage (not @State: the notes binding is what redraws the view).
    private final class Tracker: ObservableObject {
        var drag: Drag?
        var frozen: (lo: Int, hi: Int)?
        var lastLength = 1.0
    }

    private var span: (lo: Int, hi: Int) {
        if let f = track.frozen { return f }          // the rows must not move under the mouse during a drag
        let ps = notes.map(\.pitch)
        return (max((ps.min() ?? 60) - 5, 0), min((ps.max() ?? 72) + 5, 127))
    }
    private var totalBeats: Double {
        let end = notes.map { $0.start + $0.dur }.max() ?? 0
        return (end / beatsPerBar).rounded(.up) * beatsPerBar + beatsPerBar     // one empty bar to grow into
    }

    var body: some View {
        VStack(spacing: 6) {
            GeometryReader { geo in
                let rows = span.hi - span.lo + 1
                let rowH = min(max((geo.size.height - ruler) / CGFloat(rows), 6), 16)
                ScrollView(.horizontal) {
                    Canvas { ctx, size in draw(ctx, size, rowH) }
                        .frame(width: max(CGFloat(totalBeats) * ppb, geo.size.width), height: ruler + rowH * CGFloat(rows))
                        .contentShape(Rectangle())
                        .gesture(DragGesture(minimumDistance: 0)
                            .onChanged { onDrag($0, rowH) }
                            .onEnded { _ in onEnd() })
                }
                .background(Color(white: 0.09), in: RoundedRectangle(cornerRadius: 8))
            }
            Text(caption).font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
        }
        .focusable()
        .focused($focused)
        .focusEffectDisabled()
        .onKeyPress(phases: [.down, .repeat]) { press in key(press) }
    }

    private var caption: String {
        guard let n = notes.first(where: { $0.id == selected }) else {
            return "Click to add a note · drag a note to move it · drag its right edge to resize · Delete removes it"
        }
        let bar = Int(n.start / beatsPerBar) + 1, beat = n.start - Double(bar - 1) * beatsPerBar + 1
        return "\(Self.name(n.pitch)) · bar \(bar), beat \(beat.formatted(.number.precision(.fractionLength(0...2)))) · "
            + "\(n.dur.formatted(.number.precision(.fractionLength(0...2)))) beat\(n.dur == 1 ? "" : "s") · arrows nudge, Delete removes"
    }

    static func name(_ p: Int) -> String {
        ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"][p % 12] + String(p / 12 - 1)
    }

    private func rect(_ n: EditNote, _ rowH: CGFloat) -> CGRect {
        CGRect(x: CGFloat(n.start) * ppb, y: ruler + CGFloat(span.hi - n.pitch) * rowH,
               width: max(CGFloat(n.dur) * ppb, 4), height: rowH)
    }

    private func draw(_ ctx: GraphicsContext, _ size: CGSize, _ rowH: CGFloat) {
        let (lo, hi) = span
        for p in lo...hi {                                        // black-key rows are a little darker, C is marked
            let y = ruler + CGFloat(hi - p) * rowH
            if [1, 3, 6, 8, 10].contains(p % 12) {
                ctx.fill(Path(CGRect(x: 0, y: y, width: size.width, height: rowH)), with: .color(.black.opacity(0.25)))
            }
            if p % 12 == 0 {
                ctx.fill(Path(CGRect(x: 0, y: y + rowH - 0.5, width: size.width, height: 0.5)), with: .color(.white.opacity(0.25)))
                ctx.draw(Text(Self.name(p)).font(.system(size: 8)).foregroundStyle(.secondary), at: CGPoint(x: 4, y: y + rowH / 2), anchor: .leading)
            }
        }
        var b = 0
        while Double(b) * beatsPerBar <= totalBeats + 0.001 {          // bar lines and numbers
            let x = CGFloat(Double(b) * beatsPerBar) * ppb
            ctx.fill(Path(CGRect(x: x, y: 0, width: 1, height: size.height)), with: .color(.white.opacity(0.28)))
            ctx.draw(Text("\(b + 1)").font(.caption2.bold()).foregroundStyle(.secondary), at: CGPoint(x: x + 5, y: ruler / 2), anchor: .leading)
            var beat = 1.0
            while beat < beatsPerBar {
                ctx.fill(Path(CGRect(x: x + CGFloat(beat) * ppb, y: ruler, width: 0.5, height: size.height - ruler)), with: .color(.white.opacity(0.1)))
                beat += 1
            }
            b += 1
        }
        for n in notes {
            let r = rect(n, rowH), on = n.id == selected
            ctx.fill(Path(roundedRect: r.insetBy(dx: 0, dy: 1), cornerRadius: 3), with: .color(on ? .yellow : .blue.opacity(0.85)))
            if on { ctx.fill(Path(CGRect(x: r.maxX - 4, y: r.minY + 1, width: 3, height: r.height - 2)), with: .color(.black.opacity(0.35))) }
        }
    }

    private func snap(_ beats: Double) -> Double { (beats / grid).rounded() * grid }

    private func onDrag(_ v: DragGesture.Value, _ rowH: CGFloat) {
        focused = true
        if track.drag == nil {
            track.frozen = span
            let p = v.startLocation
            if let n = notes.last(where: { rect($0, rowH).contains(p) }) {
                selected = n.id
                let r = rect(n, rowH)
                track.drag = Drag(id: n.id, mode: p.x > r.maxX - 9 && r.width > 18 ? .resize : .move, start: n.start, dur: n.dur, pitch: n.pitch)
            } else if p.y > ruler {
                let pitch = span.hi - Int((p.y - ruler) / rowH)
                let n = EditNote(pitch: min(max(pitch, 0), 127), start: max(0, (Double(p.x / ppb) / grid).rounded(.down) * grid), dur: track.lastLength)
                notes.removeAll { $0.start == n.start }
                notes.append(n)
                selected = n.id
                track.drag = Drag(id: n.id, mode: .move, start: n.start, dur: n.dur, pitch: n.pitch)
            } else {
                selected = nil
                track.frozen = nil
                return
            }
        }
        guard let d = track.drag, let i = notes.firstIndex(where: { $0.id == d.id }) else { return }
        let dBeats = snap(Double(v.translation.width / ppb))
        switch d.mode {
        case .move:
            notes[i].start = max(0, d.start + dBeats)
            notes[i].pitch = min(max(d.pitch - Int((v.translation.height / rowH).rounded()), 0), 127)
        case .resize:
            notes[i].dur = max(grid, d.dur + dBeats)
        }
    }

    private func onEnd() {
        track.drag = nil
        track.frozen = nil
        tidy()
    }

    /// One note at a time: a note moved onto another's start replaces it, and each note ends where the next begins.
    private func tidy() {
        guard let sel = selected.flatMap({ id in notes.first { $0.id == id } }) else { return }
        notes.removeAll { $0.id != sel.id && $0.start == sel.start }
        notes.sort { $0.start < $1.start }
        for i in notes.indices.dropLast() where notes[i].start + notes[i].dur > notes[i + 1].start {
            notes[i].dur = max(notes[i + 1].start - notes[i].start, grid)
        }
        if let n = notes.first(where: { $0.id == sel.id }) { track.lastLength = n.dur }
    }

    private func key(_ press: KeyPress) -> KeyPress.Result {
        guard let id = selected, let i = notes.firstIndex(where: { $0.id == id }) else { return .ignored }
        switch press.key {
        case .delete, .deleteForward:
            notes.remove(at: i); selected = nil
        case .upArrow: notes[i].pitch = min(notes[i].pitch + (press.modifiers.contains(.shift) ? 12 : 1), 127)
        case .downArrow: notes[i].pitch = max(notes[i].pitch - (press.modifiers.contains(.shift) ? 12 : 1), 0)
        case .rightArrow: notes[i].start += grid; tidy()
        case .leftArrow: notes[i].start = max(0, notes[i].start - grid); tidy()
        default: return .ignored
        }
        return .handled
    }
}
