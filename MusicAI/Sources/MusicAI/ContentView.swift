import SwiftUI
import WebKit

private let channelColors: [Color] = [.blue, .orange, .green, .purple, .yellow, .red, .teal, .indigo]

struct PianoRoll: View {
    let notes: [Note]
    let duration: Double
    let playhead: Double
    var highlight: Int? = nil   // when set, every other channel is dimmed so one part stands out

    var body: some View {
        Canvas { ctx, size in
            guard let lo = notes.map(\.pitch).min(), let hi = notes.map(\.pitch).max(), duration > 0 else { return }
            let channels = Array(Set(notes.map(\.channel))).sorted()
            let rows = Double(hi - lo + 3)
            let rowH = size.height / rows
            for n in notes {
                let x = n.start / duration * size.width
                let w = max((n.end - n.start) / duration * size.width, 2)
                let y = Double(hi + 1 - n.pitch) * rowH
                let color = channelColors[(channels.firstIndex(of: n.channel) ?? 0) % channelColors.count]
                ctx.fill(Path(roundedRect: CGRect(x: x, y: y, width: w, height: rowH * 0.85), cornerRadius: 2),
                         with: .color(color.opacity(highlight == nil || n.channel == highlight ? 0.85 : 0.25)))
            }
            if playhead > 0 {
                let x = playhead / duration * size.width
                ctx.fill(Path(CGRect(x: x, y: 0, width: 1.5, height: size.height)), with: .color(.white))
            }
        }
        .background(Color(white: 0.09), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// Shows an engraved score (a local HTML file with inline SVG).
struct SheetView: NSViewRepresentable {
    let html: URL

    func makeCoordinator() -> Coordinator { Coordinator() }
    final class Coordinator { var loaded: URL? }

    func makeNSView(context: Context) -> WKWebView {
        let v = WKWebView(frame: .zero)
        v.setValue(false, forKey: "drawsBackground")
        return v
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        guard context.coordinator.loaded != html else { return }
        context.coordinator.loaded = html
        view.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
    }
}

struct ContentView: View {
    @StateObject private var vm = ViewModel()

    var body: some View {
        VStack(spacing: 0) {
            sessionBar
            Divider()
            HStack(alignment: .top, spacing: 20) {
                controls.frame(width: 310)
                results.frame(maxWidth: .infinity)
            }
            .padding(20)
        }
        .frame(minWidth: 900, minHeight: 640)
        .onAppear { vm.start() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in vm.shutdown() }
    }

    // MARK: Session bar

    private var sessionBar: some View {
        HStack(spacing: 10) {
            Image(systemName: "folder").foregroundStyle(.secondary)
            TextField("Untitled session — named after your prompt unless you type a name", text: $vm.sessionName)
                .textFieldStyle(.roundedBorder)
                .onSubmit { vm.renameSession() }
                .help("Press Return to rename. The session's folder is renamed to match.")
            Menu("Sessions") {
                Button("New session", action: vm.newSession)
                if !vm.sessions.isEmpty {
                    Divider()
                    ForEach(vm.sessions) { s in
                        Button("\(s.name)  ·  \(s.files) files") { vm.openSession(s) }
                    }
                }
            }
            .fixedSize()
            .disabled(!vm.ready || vm.busy)
            Button("Show folder", action: vm.revealSession).disabled(vm.sessionURL == nil)
        }
        .padding(.horizontal, 20).padding(.vertical, 10)
    }

    // MARK: Left column

    private var controls: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                stageHeader("1", "Melody")
                melodyControls
                Button(action: vm.generateMelodies) {
                    HStack {
                        if vm.busy && vm.orchestration == nil && vm.selected == nil { ProgressView().controlSize(.small) }
                        Text(vm.candidates.isEmpty ? "Generate melodies" : "Regenerate melodies").frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .tint(vm.candidates.isEmpty ? .accentColor : .gray)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(!vm.ready || vm.busy || !vm.hasPrompt)

                Divider().padding(.vertical, 4)

                stageHeader("2", "Accompaniment")
                Picker("Mode", selection: $vm.accompMode) {
                    Text("Automatic").tag(AccompMode.auto)
                    Text("One at a time").tag(AccompMode.oneByOne)
                }
                .pickerStyle(.segmented).labelsHidden()
                Toggle("Playable parts (respect instrument ranges)", isOn: $vm.playable)
                    .help("Keeps every instrument inside its real range and within the number of notes one player can sound at once")
                Toggle("Keep parts in the melody's key", isOn: $vm.inKey)
                    .help("When a key was chosen for the melody, the added parts only use notes from that key")
                if vm.accompMode == .auto { autoControls } else { oneByOneControls }
                DisclosureGroup("Generation settings", isExpanded: $vm.showGeneration) {
                    generationSettings.padding(.top, 8)
                }
            }
            .padding(.trailing, 10)
        }
    }

    private func stageHeader(_ n: String, _ title: String) -> some View {
        HStack(spacing: 8) {
            Text(n).font(.caption.bold()).foregroundStyle(.white)
                .frame(width: 20, height: 20).background(Circle().fill(Color.accentColor))
            Text(title).font(.headline)
            Spacer()
        }
    }

    private var autoControls: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Added instruments"); Spacer()
                Text(vm.ensemble == 0 ? "Auto" : "\(Int(vm.ensemble))").monospacedDigit()
            }
            Slider(value: $vm.ensemble, in: 0...15, step: 1)
            Button(action: vm.addAccompaniment) {
                HStack {
                    if vm.busy && vm.selected != nil { ProgressView().controlSize(.small) }
                    Text(vm.orchestration == nil ? "Add accompaniment" : "Re-roll accompaniment")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .disabled(!vm.ready || vm.busy || vm.selected == nil)
            if vm.selected == nil {
                Text("Pick a melody first.").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    /// Add instruments one at a time: choose an instrument, hear several options, approve one, repeat.
    private var oneByOneControls: some View {
        VStack(alignment: .leading, spacing: 10) {
            if vm.currentPieceURL == nil {
                Text("Pick a melody first.").font(.caption).foregroundStyle(.secondary)
            } else {
                HStack(alignment: .firstTextBaseline) {
                    Text(vm.partsAdded.isEmpty ? "Piece: melody only" : "Piece: melody + " + vm.partsAdded.joined(separator: ", "))
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    Button("Undo last") { vm.undoPart() }
                        .controlSize(.small).disabled(vm.partHistory.isEmpty || vm.busy)
                }
            }
            Picker("Instrument", selection: $vm.partInstrument) {
                ForEach(Instrument.parts) { inst in
                    Text(vm.presentPrograms.contains(inst.program) ? "\(inst.name) (in piece)" : inst.name).tag(inst)
                }
            }
            if vm.partInstrument.texture {
                Picker("Pattern", selection: $vm.pianoStyle) {
                    ForEach(Instrument.pianoStyles, id: \.id) { Text($0.name).tag($0.id) }
                }
                Text("Adds left-hand chords or arpeggios under your melody. The melody is not changed, and if the piano already plays it, the new notes join that piano.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("What should it do? (optional)").font(.caption).foregroundStyle(.secondary)
                TextEditor(text: $vm.partDirection)
                    .font(.body).frame(height: 54).padding(4)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                    .help("Describe it in your own words, e.g. “dreamy and floating underneath”, “answer the melody in the gaps”, “double the tune a sixth below”, “driving and urgent”. Leave empty for the default part.")
                if !vm.directionNote.isEmpty {
                    Text(vm.directionNote).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                } else if vm.partDirection.isEmpty {
                    Text("e.g. dreamy and floating underneath · answer the melody · double the tune a sixth below")
                        .font(.caption).foregroundStyle(.tertiary).fixedSize(horizontal: false, vertical: true)
                }
            }
            HStack { Text("Options"); Spacer(); Text("\(Int(vm.partOptionCount))").monospacedDigit() }
            Slider(value: $vm.partOptionCount, in: 1...6, step: 1)
            Button(action: vm.generatePartOptions) {
                HStack {
                    if vm.busy { ProgressView().controlSize(.small) }
                    Text(vm.partOptions.isEmpty ? "Generate \(vm.partInstrument.name.lowercased()) options"
                                                : "Regenerate \(vm.partInstrument.name.lowercased()) options")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .tint(vm.partOptions.isEmpty ? .accentColor : .gray)
            .disabled(!vm.ready || vm.busy || vm.currentPieceURL == nil || vm.partInstrumentTaken)
            if vm.partInstrumentTaken {
                Text("\(vm.partInstrument.name) is already in the piece.").font(.caption).foregroundStyle(.secondary)
            }
            if !vm.partOptions.isEmpty {
                Button(action: vm.approvePart) {
                    Text("Approve selected \(vm.partInstrument.name.lowercased()) part").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(vm.busy || vm.selectedPart == nil)
            }
        }
    }

    private var melodyControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextEditor(text: $vm.prompt)
                .font(.body)
                .frame(height: 80)
                .padding(4)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))

            HStack { Text("Bars"); Spacer(); Text("\(Int(vm.bars))").monospacedDigit() }
            Slider(value: $vm.bars, in: 2...16, step: 1)

            Picker("Melody engine", selection: $vm.engine) {
                Text("Hybrid: LLM + Anticipation").tag("hybrid")
                Text("LLM only").tag("llm")
                Text("Theory composer (rules, no AI)").tag("theory")
            }
            .help("Which generator writes the melody. Switching never loses anything: each engine makes options the same way.")
            if vm.engine == "theory" {
                Text("Writes phrases, chords and a developing motif by music theory. Your prompt is used only for the key and mood (e.g. “sad” → minor); set the Key below to be sure.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Picker("Instrument", selection: $vm.instrument) {
                ForEach(Instrument.all) { Text($0.name).tag($0) }
            }
            Picker("Time signature", selection: $vm.sig) {
                ForEach(ViewModel.timeSigs, id: \.self) { Text($0).tag($0) }
            }
            Picker("Key", selection: $vm.keyChoice) {
                Text("Auto (from prompt)").tag(KeyChoice?.none)
                Divider()
                ForEach(KeyChoice.all) { Text($0.name).tag(Optional($0)) }
            }
            .help("Every melody note stays in this key. Auto uses a key named in your prompt, such as 'in D minor'.")

            HStack {
                Text("Tempo"); Spacer()
                TextField("80", text: $vm.bpmText)
                    .textFieldStyle(.roundedBorder).multilineTextAlignment(.trailing).frame(width: 64)
                    .onChange(of: vm.bpmText) { _, new in
                        let digits = String(new.filter(\.isNumber).prefix(3))
                        if digits != new { vm.bpmText = digits }
                    }
                    .help("Beats per minute, 40–220")
                Text("bpm").foregroundStyle(.secondary)
            }

            HStack {
                Text("Complexity"); Spacer()
                Text(["Simple", "Easy", "Varied", "Rich", "Virtuosic"][Int(vm.complexity) - 1])
            }
            Slider(value: $vm.complexity, in: 1...5, step: 1)

            Picker("Anticipation model", selection: $vm.size) {
                ForEach(["small", "medium", "large"], id: \.self) { Text($0.capitalized).tag($0) }
            }
            .pickerStyle(.segmented)
            .disabled(vm.engine != "hybrid")
            .help("The Anticipatory Music Transformer used by the Hybrid engine, and later for the accompaniment. Larger is slower and usually better.")
            Picker("Melody LLM", selection: $vm.llm) {
                Text("Qwen 7B" + (ModelCache.isDownloaded("mlx-community/Qwen2.5-7B-Instruct-4bit") ? " (downloaded)" : " (4 GB, downloads on first use)")).tag("7B")
                Text("Qwen 14B" + (ModelCache.isDownloaded("mlx-community/Qwen2.5-14B-Instruct-4bit") ? " (downloaded)" : " (8 GB, downloads on first use)")).tag("14B")
            }
            .disabled(vm.engine == "theory")
            .help("Models download once and are kept on disk. 14B needs about 8 GB of free memory, which is tight on a 16 GB Mac.")

            Divider()
            Text("Character of the melody").font(.callout)
            knobRow("Melodic shape", $vm.shapeAmount, 0...10, readout: shapeWord(vm.shapeAmount),
                    help: "Stepwise: the line walks up and down the scale. Angular: it leaps and turns. This one slider sets how much the melody avoids scale runs, repeated notes and a few pitches, and how much it likes leaps. Applies to Hybrid and Theory composer.")
            knobRow("Sit on the beat", $vm.knobBeat, 0...10,
                    help: "How strongly note beginnings prefer the beat, and the barline twice as much. Hybrid engine.")
            knobRow("Follow the chords", $vm.knobChord, 0...10,
                    help: "How strongly the melody prefers the notes of the chord under each bar. Zero ignores the harmony; the accompaniment then follows the key alone. Hybrid engine.")
            knobRow("Land on the cadence", $vm.knobCadence, 0...10,
                    help: "How strongly each phrase is pulled towards the note it ends on, so it arrives somewhere. Hybrid engine.")
            knobRow("Bring the idea back", $vm.knobMotif, 0...10,
                    help: "How strongly the opening idea returns at the start of every later phrase, moved onto that phrase's chord. Zero: it never does. Hybrid engine.")
            Text("Rests between ideas").font(.callout)
            Picker("Phrasing", selection: $vm.phrasing) {
                Text("Flowing").tag(0)
                Text("Balanced").tag(1)
                Text("Spacious").tag(2)
            }
            .pickerStyle(.segmented).labelsHidden()
            .help("Rests between ideas and phrases. Flowing: the melody never stops. Spacious: longer pauses and fewer notes, so each idea stands apart.")
            Toggle("Resolve ending (finish on the home note)", isOn: $vm.resolveEnding)
                .help("Changes only the final note, so the melody ends on the tonic")

            HStack { Text("Options to choose from"); Spacer(); Text("\(Int(vm.optionCount))").monospacedDigit() }
            Slider(value: $vm.optionCount, in: 1...8, step: 1)

            Button("Import MIDI as the melody…", action: vm.importMIDI)
                .disabled(!vm.ready || vm.busy)
        }
    }

    /// A labelled slider with its value beside it.
    private func knobRow(_ title: String, _ value: Binding<Double>, _ range: ClosedRange<Double>,
                         readout: String? = nil, help: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title); Spacer()
                Text(readout ?? (value.wrappedValue == 0 ? "off" : value.wrappedValue.formatted(.number.precision(.fractionLength(1)))))
                    .monospacedDigit().foregroundStyle(.secondary)
            }
            Slider(value: value, in: range)
        }
        .help(help)
    }

    private func shapeWord(_ v: Double) -> String {
        v < 2.5 ? "Stepwise" : v < 7.5 ? "Balanced" : "Angular"
    }

    /// Settings of the accompaniment generator, kept out of the way.
    private var generationSettings: some View {
        VStack(alignment: .leading, spacing: 10) {
            Toggle("Read part directions with an LLM", isOn: $vm.llmDirections)
                .help("On: free-text directions are understood by the language model. Off: only a fixed set of keywords is recognised.")
            Toggle("Enforce meter (snap added parts to the beat grid)", isOn: $vm.enforceMeter)
            LabeledContent("Top-p \(vm.topP, specifier: "%.2f")") { Slider(value: $vm.topP, in: 0.5...1) }
                .help("Lower is safer and more repetitive; higher is more adventurous.")
            LabeledContent("Retries \(Int(vm.attempts))") { Slider(value: $vm.attempts, in: 1...20, step: 1) }
                .help("How many times a part is regenerated if it is too sparse.")
            LabeledContent("Min notes/s \(vm.minDensity, specifier: "%.1f")") {
                Slider(value: $vm.minDensity, in: 0...5, step: 0.5)
            }
        }
    }

    // MARK: Right column

    private var results: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("View", selection: $vm.viewMode) {
                Text("Piano roll").tag(ViewMode.roll)
                Text("Sheet music").tag(ViewMode.sheet)
            }
            .pickerStyle(.segmented).labelsHidden().frame(width: 260)

            mainView

            transport

            if !vm.partOptions.isEmpty {
                Text(vm.selectedPart == nil ? "Choose a \(vm.partInstrument.name.lowercased()) part (highlighted in the roll)"
                                            : "Selected — approve it, or pick another")
                    .font(.subheadline).foregroundStyle(.secondary)
                partGrid
            } else if !vm.candidates.isEmpty && vm.orchestration == nil {
                Text(vm.selected == nil ? "Choose a melody" : "Selected — add the accompaniment, or pick another")
                    .font(.subheadline).foregroundStyle(.secondary)
                optionGrid
            }

            HStack(alignment: .firstTextBaseline) {
                Text(vm.status).font(.callout).foregroundStyle(.secondary).lineLimit(3)
                Spacer()
                if vm.stalled || vm.backendDown {
                    Button("Restart backend", action: vm.restartBackend)
                }
            }
            ScrollView {
                Text(vm.log).font(.system(.caption, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
            }
            .frame(minHeight: 60)
            .padding(8).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    @ViewBuilder private var mainView: some View {
        let sheet = vm.viewMode == .sheet
        Group {
            if vm.displayNotes.isEmpty {
                RoundedRectangle(cornerRadius: 8).fill(Color(white: 0.09))
                    .overlay(Text("Your melody options will appear here").foregroundStyle(.secondary))
            } else if sheet, let url = vm.displayURL {
                if let html = vm.sheetPages[url] {
                    SheetView(html: html).clipShape(RoundedRectangle(cornerRadius: 8))
                } else {
                    RoundedRectangle(cornerRadius: 8).fill(Color(white: 0.93))
                        .overlay(Group {
                            if let err = vm.sheetErrors[url] {
                                Text(err).foregroundStyle(.red).padding()
                            } else {
                                HStack { ProgressView().controlSize(.small); Text("Engraving sheet music…") }
                                    .foregroundStyle(.secondary)
                            }
                        })
                }
            } else {
                PianoRoll(notes: vm.displayNotes, duration: vm.displayDuration,
                          playhead: vm.playingURL == vm.displayURL ? vm.playhead : 0, highlight: vm.highlightChannel)
            }
        }
        .frame(minHeight: sheet ? 320 : 200, maxHeight: sheet ? .infinity : 200)
    }

    private var transport: some View {
        HStack {
            Button(vm.playingURL == vm.displayURL && vm.playingURL != nil ? "Stop" : "Play") {
                if let url = vm.displayURL { vm.playingURL == url ? vm.stop() : vm.play(url) }
            }
            .disabled(vm.displayURL == nil)
            Button("Show in Finder", action: vm.reveal).disabled(vm.displayURL == nil)
            Button(action: vm.exportPDF) {
                HStack(spacing: 4) {
                    if vm.pdfBusy { ProgressView().controlSize(.small) }
                    Text("Export PDF…")
                }
            }
            .disabled(vm.displayURL == nil || vm.pdfBusy || !vm.ready)
            .help("Save the sheet music as a PDF")
            Button("Open in notation app", action: vm.openInNotationApp)
                .disabled(vm.displayURL == nil || !vm.ready)
                .help("Edit the notes in MuseScore, Dorico, Sibelius… (opens the score as MusicXML)")
            if let url = vm.displayURL {
                Label(url.lastPathComponent, systemImage: "music.note")
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(.quaternary, in: Capsule())
                    .help("Drag into Logic Pro")
                    .onDrag { NSItemProvider(contentsOf: url) ?? NSItemProvider() }
            }
            Spacer()
        }
    }

    private var optionGrid: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: 12)], spacing: 12) {
                ForEach(vm.candidates) { c in optionCard(c) }
            }
            .padding(2)
        }
        .frame(maxHeight: vm.viewMode == .sheet ? 170 : 260)
    }

    private var partGrid: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: 12)], spacing: 12) {
                ForEach(vm.partOptions) { c in partCard(c) }
            }
            .padding(2)
        }
        .frame(maxHeight: vm.viewMode == .sheet ? 170 : 260)
    }

    private func partCard(_ c: PartCandidate) -> some View {
        let isSelected = vm.selectedPartID == c.id
        let isPlaying = vm.playingURL == c.url
        let duration = c.notes.map(\.end).max() ?? 0
        return VStack(alignment: .leading, spacing: 6) {
            PianoRoll(notes: c.notes, duration: duration, playhead: isPlaying ? vm.playhead : 0, highlight: c.channel)
                .frame(height: 70)
            HStack {
                Text(c.label.isEmpty ? "Option \(c.index)" : "\(c.index) · \(c.label)")
                    .font(.subheadline.bold()).lineLimit(1)
                Spacer()
                Text("\(c.added) notes").font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button(isPlaying ? "Stop" : "Play") { isPlaying ? vm.stop() : vm.play(c.url) }
                    .controlSize(.small)
                Spacer()
                if isSelected { Label("Selected", systemImage: "checkmark.circle.fill").font(.caption).foregroundStyle(.tint) }
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 10).fill(isSelected ? Color.accentColor.opacity(0.15) : Color.clear))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(isSelected ? Color.accentColor : Color.secondary.opacity(0.3),
                                                           lineWidth: isSelected ? 2 : 1))
        .contentShape(Rectangle())
        .onTapGesture { vm.selectPart(c) }
    }

    private func optionCard(_ c: Candidate) -> some View {
        let isSelected = vm.selectedID == c.id
        let isPlaying = vm.playingURL == c.url
        return VStack(alignment: .leading, spacing: 6) {
            PianoRoll(notes: c.notes, duration: c.duration, playhead: isPlaying ? vm.playhead : 0)
                .frame(height: 70)
            HStack {
                Text("Option \(c.index)").font(.subheadline.bold())
                Spacer()
                Text("\(c.tempo) bpm").font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button(isPlaying ? "Stop" : "Play") { isPlaying ? vm.stop() : vm.play(c.url) }
                    .controlSize(.small)
                Spacer()
                if isSelected { Label("Selected", systemImage: "checkmark.circle.fill").font(.caption).foregroundStyle(.tint) }
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 10).fill(isSelected ? Color.accentColor.opacity(0.15) : Color.clear))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(isSelected ? Color.accentColor : Color.secondary.opacity(0.3),
                                                           lineWidth: isSelected ? 2 : 1))
        .contentShape(Rectangle())
        .onTapGesture { vm.select(c) }
    }
}
