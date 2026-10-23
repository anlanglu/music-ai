import SwiftUI
import WebKit

private let channelColors: [Color] = [.blue, .orange, .green, .purple, .yellow, .red, .teal, .indigo]

struct PianoRoll: View {
    let notes: [Note]
    let duration: Double
    let playhead: Double
    var highlight: Int? = nil   // when set, every other channel is dimmed so one part stands out
    /// When set, bar lines and numbers are drawn; `chosen` bars are shaded, and clicking a bar calls `toggle`.
    var secondsPerBar: Double? = nil
    var chosen: Set<Int> = []
    var toggle: ((Int) -> Void)? = nil

    var body: some View {
        GeometryReader { geo in
            Canvas { ctx, size in
                guard let lo = notes.map(\.pitch).min(), let hi = notes.map(\.pitch).max(), duration > 0 else { return }
                let channels = Array(Set(notes.map(\.channel))).sorted()
                let top: CGFloat = secondsPerBar == nil ? 0 : 16
                let rows = Double(hi - lo + 3)
                let rowH = (size.height - top) / rows
                if let spb = secondsPerBar, spb > 0 {
                    var bar = 1
                    while Double(bar - 1) * spb < duration - 0.001 {
                        let x = Double(bar - 1) * spb / duration * size.width
                        let w = min(spb / duration * size.width, size.width - x)
                        if chosen.contains(bar) {
                            ctx.fill(Path(CGRect(x: x, y: 0, width: w, height: size.height)), with: .color(.yellow.opacity(0.18)))
                        }
                        ctx.fill(Path(CGRect(x: x, y: 0, width: 1, height: size.height)), with: .color(.white.opacity(0.28)))
                        ctx.draw(Text("\(bar)").font(.caption2.bold()).foregroundStyle(chosen.contains(bar) ? Color.yellow : Color.secondary),
                                 at: CGPoint(x: x + 4, y: top / 2), anchor: .leading)
                        bar += 1
                    }
                }
                for n in notes {
                    let x = n.start / duration * size.width
                    let w = max((n.end - n.start) / duration * size.width, 2)
                    let y = top + Double(hi + 1 - n.pitch) * rowH
                    let color = channelColors[(channels.firstIndex(of: n.channel) ?? 0) % channelColors.count]
                    ctx.fill(Path(roundedRect: CGRect(x: x, y: y, width: w, height: rowH * 0.85), cornerRadius: 2),
                             with: .color(color.opacity(highlight == nil || n.channel == highlight ? 0.85 : 0.25)))
                }
                if playhead > 0 {
                    let x = playhead / duration * size.width
                    ctx.fill(Path(CGRect(x: x, y: 0, width: 1.5, height: size.height)), with: .color(.white))
                }
            }
            .contentShape(Rectangle())
            .gesture(SpatialTapGesture().onEnded { tap in
                guard let spb = secondsPerBar, spb > 0, duration > 0, let toggle else { return }
                let bar = Int(Double(tap.location.x / geo.size.width) * duration / spb) + 1
                if bar >= 1, Double(bar - 1) * spb < duration { toggle(bar) }
            })
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
    @AppStorage("showHistory") private var showHistory = true

    var body: some View {
        VStack(spacing: 0) {
            sessionBar
            Divider()
            HStack(alignment: .top, spacing: 20) {
                controls.frame(width: 310)
                results.frame(maxWidth: .infinity)
                if showHistory { historyPane.frame(width: 210) }
            }
            .padding(20)
        }
        .frame(minWidth: showHistory ? 1120 : 900, minHeight: 640)
        .onAppear { vm.start() }
        .onChange(of: vm.displayURL) { _, _ in vm.loadMix() }
        .onChange(of: vm.ready) { _, _ in vm.loadMix() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in vm.shutdown() }
    }

    // MARK: Session bar

    private var sessionBar: some View {
        HStack(spacing: 10) {
            Image(systemName: "folder").foregroundStyle(.secondary)
            TextField("Session name", text: $vm.sessionName)
                .textFieldStyle(.roundedBorder)
                .onSubmit { vm.renameSession() }
                .help("Press Return to rename. The session's folder is renamed to match.")
            Toggle(isOn: $showHistory) { Label("History", systemImage: "clock.arrow.circlepath") }
                .toggleStyle(.button)
                .help("Show or hide the list of steps you have taken, to go back to any of them")
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
                stageHeader("1", "Motif")
                melodyControls

                Divider().padding(.vertical, 4)

                stageHeader("2", "Extend melody")
                expandControls

                Divider().padding(.vertical, 4)

                stageHeader("3", "Edit melody")
                editControls

                Divider().padding(.vertical, 4)

                stageHeader("4", "Accompaniment")
                Picker("Music model", selection: $vm.size) {
                    ForEach(["small", "medium", "large"], id: \.self) { Text($0.capitalized).tag($0) }
                }
                .pickerStyle(.segmented)
                .help("The Anticipation model that writes the accompaniment parts, and that continues the melody when you expand it with Hybrid. Small is quick; large is slower and usually better.")
                Picker("Mode", selection: $vm.accompMode) {
                    Text("Automatic").tag(AccompMode.auto)
                    Text("One at a time").tag(AccompMode.oneByOne)
                }
                .pickerStyle(.segmented).labelsHidden()
                Toggle("Playable parts (respect instrument ranges)", isOn: $vm.playable)
                    .help("Keeps every instrument inside its real range and within the number of notes one player can sound at once")
                Toggle("Keep parts in the melody's key", isOn: $vm.inKey)
                    .help("When a key was chosen for the melody, the added parts only use notes from that key")
                let dynHelp = "How much the loudness of the accompaniment varies. Each instrument plays a little under the melody, swelling and fading with it, with the beat a little stronger. Zero: each instrument at one steady level. The melody keeps its own dynamics."
                HStack {
                    Text("Dynamics"); Spacer()
                    Text(vm.accompDynamics <= 0 ? "Flat" : "\(Int(vm.accompDynamics))").monospacedDigit().foregroundStyle(.secondary)
                }
                .contentShape(Rectangle()).help(dynHelp)
                Slider(value: $vm.accompDynamics, in: 0...10, step: 1).help(dynHelp)
                if vm.accompMode == .auto { autoControls } else { oneByOneControls }
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
            .contentShape(Rectangle())
            .help("How many instruments the accompaniment adds. Auto lets the music model choose.")
            Slider(value: $vm.ensemble, in: 0...15, step: 1)
                .help("How many instruments the accompaniment adds. Auto (0) lets the music model choose.")
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
                .contentShape(Rectangle()).help("How many versions of this instrument's part are written for you to choose from.")
            Slider(value: $vm.partOptionCount, in: 1...6, step: 1)
                .help("How many versions of this instrument's part are written for you to choose from.")
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

    // MARK: The settings both melody steps share
    //
    // Step 1 (write a melody) and step 2 (expand it) show the same controls in the same order; each step only hides
    // what does not apply to it: step 1 also asks for the instrument, time signature, key and tempo, and step 2 asks
    // which engine continues the melody. Each step keeps its own values, and step 2 starts from step 1's.

    private var melodyControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Start small: a motif of one to four bars is the musical idea the rest of the piece grows from. Extend it in step 2.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            describeBox("Describe the motif", text: $vm.prompt, height: 80)
            barsRow("Bars", $vm.bars, range: 1...4, help: "How long the motif is, in bars (measures), at most 4. At 4/4 and 80 bpm, 4 bars last about 12 seconds.")

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
            .help("The home note. Every melody note stays in the key's scale. Auto: a key named in your prompt (such as 'in D minor'), otherwise the language model picks one that suits it.")
            Picker("Mode", selection: $vm.keyMode) {
                ForEach(Mode.all, id: \.id) { Text($0.name).tag($0.id) }
            }
            .help("The scale built on that home note. Major and minor are the usual two. Dorian, Phrygian, Lydian, Mixolydian and Locrian each change notes to get a different colour (Lydian: bright and dreamy; Dorian: minor but warmer; Mixolydian: major but bluesy; Phrygian: dark, Spanish; Locrian: unstable and tense, rarely used for a whole tune). Auto: a mode named in your prompt, otherwise major or minor to suit the mood.")
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

            complexityRow($vm.complexity)
            llmPicker
            characterControls(shape: $vm.shapeSlider, runs: $vm.runsSlider, pulse: $vm.pulseSlider,
                              harmony: $vm.harmonySlider, endings: $vm.endingsSlider, theme: $vm.themeSlider, flavor: $vm.flavorSlider, strict: $vm.strictSlider, dynamics: $vm.dynamicsSlider,
                              phrasing: $vm.phrasing, resolve: $vm.resolveEnding, auto: $vm.autoSliders, what: "the melody")
            optionsRow($vm.optionCount)

            Button(action: vm.generateMelodies) {
                HStack {
                    if vm.busy && vm.orchestration == nil && vm.selected == nil && vm.expansionHistory.isEmpty { ProgressView().controlSize(.small) }
                    Text(vm.candidates.isEmpty ? "Generate motifs" : "Regenerate motifs").frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .tint(vm.candidates.isEmpty ? .accentColor : .gray)
            .keyboardShortcut(.return, modifiers: .command)
            .disabled(!vm.ready || vm.busy || !vm.hasPrompt)

            Button("Import MIDI as the melody…", action: vm.importMIDI)
                .disabled(!vm.ready || vm.busy)
        }
    }

    /// Step 2: add bars to the chosen melody. The same controls as step 1, with its own values.
    private var expandControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pick a motif or melody above, then grow it. The new options replace the list with the first one selected, so you can press Expand again to keep going, or pick another option first. Undo steps back.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            describeBox("What should the new bars do? (optional)", text: $vm.expandDirection, height: 54,
                        example: "e.g. build to a climax, then calm down")
                .help("Free text for this expansion. LLM only reads it as an instruction; Hybrid turns it into how busy, how high or low and how adventurous the new bars are.")
            barsRow("Bars to add", $vm.expandBars, help: "How many bars to add on the end of the chosen melody.")

            Picker("Written by", selection: $vm.expandEngine) {
                Text("Hybrid: LLM + Anticipation").tag("hybrid")
                Text("LLM only").tag("llm")
            }
            .help("Hybrid: the Anticipation model continues the melody, steered by the sliders; its rhythms are the more natural. LLM only: the language model continues it phrase by phrase and reads your direction.")

            complexityRow($vm.expandComplexity)
            if vm.expandEngine == "llm" { llmPicker }
            characterControls(shape: $vm.expandShape, runs: $vm.expandRuns, pulse: $vm.expandPulse,
                              harmony: $vm.expandHarmony, endings: $vm.expandEndings, theme: $vm.expandTheme, flavor: $vm.expandFlavor, strict: $vm.expandStrict, dynamics: $vm.expandDynamics,
                              phrasing: $vm.expandPhrasing, resolve: $vm.expandResolve, auto: $vm.expandAuto, what: "the new bars")
            optionsRow($vm.expandCount)

            HStack {
                Button(action: vm.expandMelody) {
                    HStack {
                        if vm.busy && vm.selected == nil && !vm.expansionHistory.isEmpty { ProgressView().controlSize(.small) }
                        Text("Expand by \(Int(vm.expandBars)) bars").frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(!vm.canExpand)
                if !vm.expansionHistory.isEmpty {
                    Button("Undo", action: vm.undoExpansion).controlSize(.large).disabled(vm.busy)
                        .help("Go back to the options from before the last expansion")
                }
            }
            if vm.selected == nil {
                Text("Pick one of the options first.").font(.caption).foregroundStyle(.secondary)
            } else if !(vm.orchestration == nil && vm.partsAdded.isEmpty && vm.partOptions.isEmpty) {
                Text("The accompaniment has been started, so the melody can no longer change. Generate new melodies to start over.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Step 3: change the melody by hand (the piano roll becomes an editor) or by asking the LLM to change named bars.
    private var editControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Fix what you do not like before adding the accompaniment. Only the bars you name change; every other bar stays exactly as it is.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Button(action: vm.beginEditing) {
                Label("Edit notes by hand", systemImage: "pencil").frame(maxWidth: .infinity)
            }
            .controlSize(.large).disabled(!vm.canEdit || vm.editing)
            .help("Turns the piano roll into an editor: click to add a note, drag a note to change its pitch or position, drag its right edge to change its length, Delete to remove it. Save changes redraws the sheet music.")

            describeBox("Or ask the LLM to change something", text: $vm.editInstruction, height: 66,
                        example: "e.g. I don't think measure 2 leads into measure 3 very well, so connect them better")
                .help("Say what is wrong and name the bars (\"measure 2\", \"bars 3-4\"). The LLM is only given those bars to rewrite.")
            HStack {
                Text("Bars").foregroundStyle(.secondary)
                TextField("or click bars in the roll", text: $vm.editBarsText)
                    .textFieldStyle(.roundedBorder)
                    .help("Optional: the bars to change, as well as any you named above. The bar numbers are shown along the top of the hand editor and in the sheet music. If neither names a bar, nothing is changed.")
            }
            optionsRow($vm.editCount)
            llmPicker
            HStack {
                Button(action: vm.startLLMEdit) {
                    HStack {
                        if vm.busy && vm.selected == nil && vm.canEditBusyHint { ProgressView().controlSize(.small) }
                        Text("Change those bars").frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(!vm.canEdit || vm.editing)
                if !vm.expansionHistory.isEmpty {
                    Button("Undo", action: vm.undoExpansion).controlSize(.large).disabled(vm.busy)
                        .help("Go back to the options from before the last edit or expansion")
                }
            }
            if let sug = vm.editSuggestion {
                VStack(alignment: .leading, spacing: 8) {
                    Text("The LLM says bar\(sug.bars.count == 1 ? "" : "s") \(sug.bars.map(String.init).joined(separator: ", ")) would have to change too" + (sug.reason.isEmpty ? "." : ": \(sug.reason)"))
                        .font(.callout).fixedSize(horizontal: false, vertical: true)
                    Text("They were left exactly as they were. May it change them?").font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button("Yes, change them too", action: vm.allowSuggestedBars).disabled(vm.busy)
                        Button("No, leave them", action: vm.declineSuggestedBars)
                    }
                }
                .padding(10).background(.orange.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
            }
            if vm.selected == nil {
                Text("Pick one of the options first.").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func describeBox(_ title: String, text: Binding<String>, height: CGFloat, example: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            TextEditor(text: text)
                .font(.body).frame(height: height).padding(4)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            if let example, text.wrappedValue.isEmpty {
                Text(example).font(.caption).foregroundStyle(.tertiary)
            }
        }
    }

    private func barsRow(_ title: String, _ value: Binding<Double>, range: ClosedRange<Double> = 2...16, help: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack { Text(title); Spacer(); Text("\(Int(value.wrappedValue))").monospacedDigit() }
                .contentShape(Rectangle()).help(help)
            Slider(value: value, in: range, step: 1).help(help)
        }
    }

    private func complexityRow(_ value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            let help = "How busy and varied the notes are. Simple: mostly quarter and half notes, stepwise, singable. Varied: a mix of note lengths and some leaps. Virtuosic: many fast notes, wide leaps and rhythmic variety."
            HStack {
                Text("Complexity"); Spacer()
                Text(["Simple", "Easy", "Varied", "Rich", "Virtuosic"][Int(value.wrappedValue) - 1])
            }
            .contentShape(Rectangle()).help(help)
            Slider(value: value, in: 1...5, step: 1).help(help)
        }
    }

    private func optionsRow(_ value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            let help = "How many different versions are written for you to choose from. More options give more to pick from but take longer."
            HStack { Text("Options to choose from"); Spacer(); Text("\(Int(value.wrappedValue))").monospacedDigit() }
                .contentShape(Rectangle()).help(help)
            Slider(value: value, in: 1...8, step: 1).help(help)
        }
    }

    private var llmPicker: some View {
        Picker("Melody LLM", selection: $vm.llm) {
            Text("Qwen 7B" + (ModelCache.isDownloaded("mlx-community/Qwen2.5-7B-Instruct-4bit") ? " (downloaded)" : " (4 GB, downloads on first use)")).tag("7B")
            Text("Qwen 14B" + (ModelCache.isDownloaded("mlx-community/Qwen2.5-14B-Instruct-4bit") ? " (downloaded)" : " (8 GB, downloads on first use)")).tag("14B")
        }
        .help("The language model that writes the melody, and the expansions if you choose LLM only. Models download once and are kept on disk. 14B needs about 8 GB of free memory, which is tight on a 16 GB Mac.")
    }

    /// The character sliders, phrasing and ending: identical in both steps. `what` is "the melody" or "the new bars".
    private func characterControls(shape: Binding<Double>, runs: Binding<Double>, pulse: Binding<Double>,
                                   harmony: Binding<Double>, endings: Binding<Double>, theme: Binding<Double>, flavor: Binding<Double>, strict: Binding<Double>, dynamics: Binding<Double>,
                                   phrasing: Binding<Int>, resolve: Binding<Bool>, auto: Binding<Set<String>>, what: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Divider()
            Text("Character of \(what)").font(.callout)
            knobRow("Smooth ↔ Jumpy", shape, 0...10, auto: autoBinding(auto, "shape"), readout: shapeWord(shape.wrappedValue),
                    help: "How \(what) moves from note to note. Smooth: mostly small steps, like singing up and down a scale. Jumpy: bigger leaps and more changes of direction, which sounds more angular and less predictable.")
            knobRow("Avoid scale runs", runs, 0...10, auto: autoBinding(auto, "runs"),
                    help: "How much \(what) is kept from running up or down the scale in one direction. High: runs are cut short with a leap or a turn. Zero: runs are fine. Independent of Smooth ↔ Jumpy, so a smooth tune can still avoid runs.")
            knobRow("Steady pulse", pulse, 0...10, auto: autoBinding(auto, "pulse"),
                    help: "How strongly notes start on the beat (and most of all on the first beat of a bar). Low: loose, off-beat, more improvised. High: square and regular, easy to tap your foot to.")
            knobRow("Fit the harmony", harmony, 0...10, auto: autoBinding(auto, "harmony"),
                    help: "Every bar is given a chord underneath. High: \(what) mostly uses that chord's notes, so it sounds firmly harmonised. Low: it roams freely through the key. Zero: ignore the chords (the accompaniment then follows the key alone).")
            knobRow("Phrase endings", endings, 0...10, auto: autoBinding(auto, "endings"),
                    help: "How strongly each phrase finishes on a note that sounds like a stopping place, the way a sentence ends with a full stop. High: clear, satisfying endings. Zero: phrases just trail off wherever they land.")
            knobRow("Recurring theme", theme, 0...10, auto: autoBinding(auto, "theme"),
                    help: "How strongly the opening idea comes back at the start of each new phrase, shifted to fit that phrase's chord. High: an obvious, memorable theme. Zero: every phrase is new material.")
            knobRow("Scale: Relaxed ↔ Strict", strict, 0...10, auto: autoBinding(auto, "strict"),
                    readout: strict.wrappedValue >= 10 ? "Strict" : strict.wrappedValue <= 0 ? "Relaxed" : "\(Int(strict.wrappedValue))",
                    help: "How closely \(what) keeps to the notes of the key or mode. The allowed notes are worked out from the mode itself (not by the LLM) and listed in the log. Strict: every note is in the scale. Relaxed: some notes outside it are kept, up to about a quarter of them at zero. The ones that stay are short notes off the beat, like chromatic passing notes. The log says how many ended up outside.")
            knobRow("Dynamics: Flat ↔ Expressive", dynamics, 0...10, auto: autoBinding(auto, "dynamics"),
                    readout: dynamics.wrappedValue <= 0 ? "Flat" : dynamics.wrappedValue >= 10 ? "Very expressive" : "\(Int(dynamics.wrappedValue))",
                    help: "How much the loudness varies in \(what): notes on the beat a little stronger, each phrase swelling and fading, the line rising towards its highest note, and the ending tapering. Zero: every note at one level. The accompaniment gets its own setting. The volume of each instrument is in the Mixer under the preview.")
            knobRow("Modal flavor", flavor, 0...10, auto: autoBinding(auto, "flavor"),
                    help: "Only for the modes other than major and minor. How strongly \(what) uses the one note that gives the mode its sound (Lydian's raised 4th, Dorian's raised 6th, Phrygian's flat 2nd, Mixolydian's flat 7th, Locrian's flat 5th). Low: the notes are in the mode but its sound may not come through. High: that note is used often, on strong beats, and the chords stay modal. Does nothing in major or minor.")
            Text("Pauses between phrases").font(.callout)
            Picker("Phrasing", selection: phrasing) {
                Text("Flowing").tag(0)
                Text("Balanced").tag(1)
                Text("Spacious").tag(2)
            }
            .pickerStyle(.segmented).labelsHidden()
            .help("Rests between ideas and phrases. Flowing: it never stops. Spacious: longer pauses and fewer notes, so each idea stands apart.")
            Toggle("Resolve ending (finish on the home note)", isOn: resolve)
                .help("Changes only the final note, so it ends on the tonic")
        }
    }

    private func autoBinding(_ set: Binding<Set<String>>, _ key: String) -> Binding<Bool> {
        Binding(get: { set.wrappedValue.contains(key) },
                set: { on in if on { set.wrappedValue.insert(key) } else { set.wrappedValue.remove(key) } })
    }

    /// A slider with an Auto switch: on Auto the slider is greyed and the LLM is given no instruction for it.
    private func knobRow(_ title: String, _ value: Binding<Double>, _ range: ClosedRange<Double>, auto: Binding<Bool>,
                         readout: String? = nil, help: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title); Spacer()
                Text(auto.wrappedValue ? "Auto" : readout ?? (value.wrappedValue == 0 ? "off" : "\(Int(value.wrappedValue))"))
                    .monospacedDigit().foregroundStyle(.secondary)
                    .help(help)
                Toggle("Auto", isOn: auto).toggleStyle(.checkbox).controlSize(.small)
                    .help("Auto: don't set this. The LLM decides from your description (the engine uses its usual setting).")
            }
            .contentShape(Rectangle())
            .help(help)                  // hovering the name (or anywhere on the label row) explains the slider
            Slider(value: value, in: range, step: 1).disabled(auto.wrappedValue).help(help)
        }
    }

    private func shapeWord(_ v: Double) -> String {
        v < 2.5 ? "Smooth" : v < 7.5 ? "Balanced" : "Jumpy"
    }

    // MARK: History

    private var historyPane: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("History").font(.headline)
            Text("Click a step to go back to it and carry on from there. Nothing is deleted: what you do next is added to the list.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if vm.history.isEmpty {
                Text("Your steps will appear here.").font(.caption).foregroundStyle(.tertiary).padding(.top, 6)
                Spacer()
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(spacing: 6) {
                            ForEach(Array(vm.history.enumerated()), id: \.element.id) { i, e in
                                historyRow(i + 1, e).id(e.id)
                            }
                        }
                        .padding(2)
                    }
                    .onChange(of: vm.history.count) { _, _ in
                        if let last = vm.history.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
                    }
                }
            }
        }
    }

    private func historyRow(_ n: Int, _ e: HistoryEntry) -> some View {
        let current = e.id == vm.currentEntryID
        return Button { vm.goBack(to: e) } label: {
            HStack(alignment: .top, spacing: 8) {
                Text("\(n)").font(.caption.bold()).foregroundStyle(current ? .white : .secondary)
                    .frame(width: 20, height: 20)
                    .background(Circle().fill(current ? Color.accentColor : Color.secondary.opacity(0.25)))
                VStack(alignment: .leading, spacing: 2) {
                    Text(e.title).font(.subheadline.bold()).lineLimit(2).multilineTextAlignment(.leading)
                    if !e.detail.isEmpty {
                        Text(e.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2).multilineTextAlignment(.leading)
                    }
                    Text(e.time.formatted(date: .omitted, time: .shortened)).font(.caption2).foregroundStyle(.tertiary)
                }
                Spacer(minLength: 0)
            }
            .padding(8).frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10).fill(current ? Color.accentColor.opacity(0.15) : Color.clear))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(current ? Color.accentColor : Color.secondary.opacity(0.3),
                                                               lineWidth: current ? 2 : 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(vm.busy)
        .help(current ? "You are here" : "Go back to this step")
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

            if vm.editing {
                HStack {
                    Text(vm.editDirty ? "Unsaved changes" : "No changes yet").foregroundStyle(.secondary)
                    Spacer()
                    Button("Discard", action: vm.cancelEditing)
                    Button("Save changes", action: vm.saveEditing)
                        .buttonStyle(.borderedProminent).disabled(!vm.editDirty || vm.editNotes.isEmpty)
                        .keyboardShortcut("s", modifiers: .command)
                }
            }

            transport
            mixer

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
            if vm.editing {
                MelodyEditor(notes: $vm.editNotes, selected: $vm.editSelected, beatsPerBar: vm.beatsPerBar,
                             chosen: vm.chosenBars, toggle: { vm.toggleBar($0) })
            } else if vm.displayNotes.isEmpty {
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
                          playhead: vm.playingURL == vm.displayURL ? vm.playhead : 0, highlight: vm.highlightChannel,
                          secondsPerBar: vm.barsShown ? vm.secondsPerBar : nil, chosen: vm.chosenBars,
                          toggle: vm.canEdit ? { vm.toggleBar($0) } : nil)
                    .help(vm.canEdit ? "Click a bar to choose it for the LLM edit (step 3). Chosen bars are shaded." : "")
            }
        }
        .frame(minHeight: sheet ? 320 : (vm.editing ? 300 : 200), maxHeight: sheet ? .infinity : (vm.editing ? 300 : 200))
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

    /// The volume of each instrument in the piece shown above. It is written into the MIDI file (controller 7).
    @ViewBuilder private var mixer: some View {
        if !vm.mix.isEmpty {
            DisclosureGroup("Mixer — volume of each instrument") {
                VStack(spacing: 4) {
                    ForEach(vm.mix) { ch in
                        HStack {
                            Text(ch.name).lineLimit(1).frame(width: 130, alignment: .leading)
                            Slider(value: Binding(get: { Double(ch.volume) }, set: { vm.setVolume(ch.channel, Int($0)) }),
                                   in: 0...127, step: 1)
                            Text("\(ch.volume)").monospacedDigit().foregroundStyle(.secondary).frame(width: 32, alignment: .trailing)
                        }
                        .help("The volume of \(ch.name) (MIDI controller 7, 0 to 127; 100 is the usual level). It is saved in the MIDI file, so Logic and notation apps use it too, and the notes are not changed. Adding or regenerating an accompaniment writes a new file at the usual level.")
                    }
                }
                .padding(.top, 4)
            }
            .font(.callout)
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
