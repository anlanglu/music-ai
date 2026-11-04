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
    @AppStorage("showAdvanced") private var showAdvanced = false
    private let lyricsVoiceProgram = 53          // lyrics.VOICE_PROGRAM

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
        .sheet(isPresented: $vm.showLyrics) { LyricsSheet(vm: vm) }
        .sheet(isPresented: $vm.showAddVoice) { AddVoiceSheet(vm: vm) }
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
            Button { vm.showLyrics = true } label: { Label("Lyrics", systemImage: "music.mic") }
                .help("Keep your lyrics here; choose the Voice instrument to write the melody to them")
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

    private static let stepTitles = ["Motif", "Extend melody", "Edit melody", "Add accompaniment", "Enrich"]
    private static let stepShort = ["Motif", "Extend", "Edit", "Parts", "Enrich"]

    /// The left column is a wizard: one step at a time, with Back and Next underneath.
    private var controls: some View {
        VStack(alignment: .leading, spacing: 12) {
            stepper
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    stageHeader("\(vm.step)", Self.stepTitles[vm.step - 1])
                    switch vm.step {
                    case 1: melodyControls
                    case 2: expandControls
                    case 3: editControls
                    case 4: accompControls
                    default: enrichControls
                    }
                }
                .padding(.trailing, 10)
            }
            if vm.step < Self.stepTitles.count, let why = nextBlocked {
                Label(why, systemImage: "info.circle").font(.callout).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button { vm.step -= 1 } label: { Label("Back", systemImage: "chevron.left") }
                    .disabled(vm.step <= 1)
                    .help(vm.step > 1 ? "Back to: \(Self.stepTitles[vm.step - 2])" : "")
                Spacer()
                if vm.step < Self.stepTitles.count {
                    Button { vm.step += 1 } label: {
                        HStack(spacing: 4) { Text("Next: \(Self.stepTitles[vm.step])"); Image(systemName: "chevron.right") }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(vm.selected == nil || vm.busy)
                    .help(nextBlocked ?? "Go on to: \(Self.stepTitles[vm.step])")
                }
            }
        }
    }

    /// Why Next is greyed out, or nil when it is available.
    private var nextBlocked: String? {
        if vm.busy { return "Wait for the current step to finish." }
        if vm.selected != nil { return nil }
        let first = vm.step == 1
        if vm.candidates.isEmpty {
            return first ? "Generate a motif first, then click the one you want to keep." : "Go back and generate a motif first."
        }
        return "Click one of the options on the right to pick your motif (the highlighted one is kept)." 
    }

    /// The four steps as a row of numbered dots. A step can be visited once there is a melody to work on.
    private var stepper: some View {
        HStack(spacing: 4) {
            ForEach(1...Self.stepTitles.count, id: \.self) { n in
                let here = n == vm.step, open = n == 1 || vm.selected != nil
                Button { vm.step = n } label: {
                    VStack(spacing: 3) {
                        Text("\(n)").font(.caption.bold()).foregroundStyle(here ? .white : .secondary)
                            .frame(width: 22, height: 22)
                            .background(Circle().fill(here ? Color.accentColor : Color.gray.opacity(0.25)))
                        Text(Self.stepShort[n - 1]).font(.caption2).lineLimit(1)
                            .foregroundStyle(here ? .primary : .secondary)
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.plain).disabled(!open || vm.busy)
            }
        }
    }

    /// The settings steps 4 and 5 share. Parts always respect each instrument's range and the melody's key.
    private var accompSettings: some View {
        VStack(alignment: .leading, spacing: 14) {
            let dynHelp = "How much the loudness of the accompaniment varies. Each instrument plays a little under the melody, swelling and fading with it, with the beat a little stronger. Zero: each instrument at one steady level. The melody keeps its own dynamics."
            HStack {
                Text("Dynamics"); Spacer()
                Text(vm.accompDynamics <= 0 ? "Flat" : "\(Int(vm.accompDynamics))").monospacedDigit().foregroundStyle(.secondary)
            }
            .contentShape(Rectangle()).help(dynHelp)
            Slider(value: $vm.accompDynamics, in: 0...10, step: 1).help(dynHelp)
            advancedBox {
                Picker("Music model", selection: $vm.size) {
                    ForEach(["small", "medium", "large"], id: \.self) { Text($0.capitalized).tag($0) }
                }
                .pickerStyle(.segmented)
                .help("The Anticipation model that writes the accompaniment parts, and that continues the melody when you expand it. Small is quick; large is slower and usually better.")
            }
        }
    }

    /// Who sings: a lead from the built-in singers and the voices added from recordings, and optionally others with the
    /// lead (a duet or choir), singing the same notes or harmonizing.
    private func singerPicker(_ who: Binding<Singers>) -> some View {
        let others = vm.voiceChoices.filter { $0.key != who.wrappedValue.lead }
        let names = Binding<Set<String>>(
            get: { Set(others.filter { who.wrappedValue.extras.contains($0.key) }.map(\.name)) },
            set: { chosen in who.wrappedValue.extras = Set(others.filter { chosen.contains($0.name) }.map(\.key)) })
        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Picker("Singer", selection: who.lead) {
                    ForEach(vm.voiceChoices, id: \.key) { Text($0.name).tag($0.key) }
                }
                .help("Who sings these bars. Singers A and B are the two reference voices that come with SoulX-Singer.")
                Button("Add voice…") { vm.showAddVoice = true }
                    .disabled(vm.busy || !vm.ready)
                    .help("Make a new singer from a recording of a voice singing alone")
            }
            if !others.isEmpty {
                Text("Also sung by (a duet or choir, up to four voices)").font(.caption).foregroundStyle(.secondary)
                ChipPicker(items: others.map(\.name), selection: names, limit: Lyrics.maxSingers - 1)
                if !who.wrappedValue.extras.isEmpty {
                    Toggle("Harmonize (a third above, a third below, a fifth above)", isOn: who.harmonize)
                        .help("Ticked: each extra singer takes a harmony part, in the key. Unticked: they all sing the same notes. Every extra singer adds the same singing time again.")
                }
            }
        }
    }

    /// Settings most people never change, folded away under one heading (open or closed everywhere at once).
    private func advancedBox<Content: View>(@ViewBuilder _ content: @escaping () -> Content) -> some View {
        DisclosureGroup("Advanced", isExpanded: $showAdvanced) {
            VStack(alignment: .leading, spacing: 12) { content() }.padding(.top, 8)
        }
    }

    /// Step 4 (optional): add instruments one at a time, choosing among several options for each.
    private var accompControls: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Optional. Add instruments one at a time: choose an instrument, hear several options, approve one, repeat. The melody is not changed.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            singingControls
            Divider()
            accompSettings
            oneByOneControls
        }
    }

    /// Step 4: the singing voice, a sung line over the melody. A few bars at a time; the notes can be changed in step 3.
    private var singingControls: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Singing voice").font(.headline)
            Text("A sung line over the melody, written to your lyrics (Lyrics button), a few bars at a time. It does not change the melody. Change its notes in step 3 (choose Singing), or lengthen the melody in step 2 and sing the new bars here.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            singerPicker($vm.singSingers)
            HStack {
                Text("Start at bar"); Spacer()
                Text(vm.singStart == 0 ? "after the singing so far" : "\(Int(vm.singStart))").foregroundStyle(.secondary)
                Stepper("", value: $vm.singStart, in: 0...64).labelsHidden()
            }
            .help("Which bar the singing starts on. Auto starts right after the bars already sung, or at bar 1.")
            barsRow("Bars to sing", $vm.singBars, range: 1...8, help: "How many bars of singing to write. The next lyric lines that fit are used; the melody is not changed.")
            optionsRow($vm.singCount)
            HStack {
                Button { vm.generateSinging() } label: {
                    Text(vm.hasSung ? "Add more singing" : "Add singing").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(!vm.canExpand || !vm.hasLyrics)
                Button("Lyrics…") { vm.showLyrics = true }.controlSize(.large)
            }
            if vm.selected == nil {
                Text("Pick a melody first.").font(.caption).foregroundStyle(.secondary)
            } else if !vm.hasLyrics {
                Text("Add your lyrics first (Lyrics…).").font(.caption).foregroundStyle(.secondary)
            }
            if vm.hasSung {
                Button { vm.singSelected() } label: { Text("Hear it sung (about 15 seconds a line)").frame(maxWidth: .infinity) }
                    .disabled(vm.busy || !vm.ready)
                    .help("Sings the lyric lines with the singers chosen for them and plays the result, with the melody under it")
            }
        }
    }

    /// Step 5 (optional): the music model adds a whole ensemble on top of whatever steps 1 to 4 made.
    private var enrichControls: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Optional. Let the music model add a whole ensemble around the piece so far, including any parts you approved in step 4. Press again to re-roll it from the piece as it was before.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            accompSettings
            autoControls
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
                Text("Instruments to add"); Spacer()
                Text(vm.ensemble == 0 ? "Auto" : "\(Int(vm.ensemble))").monospacedDigit()
            }
            .contentShape(Rectangle())
            .help("How many instruments the accompaniment adds. Auto lets the music model choose.")
            Slider(value: $vm.ensemble, in: 0...15, step: 1)
                .help("How many instruments the accompaniment adds. Auto (0) lets the music model choose.")
            Button(action: vm.addAccompaniment) {
                HStack {
                    if vm.busy && vm.selected != nil { ProgressView().controlSize(.small) }
                    Text(vm.isEnriched ? "Re-roll the enrichment" : "Enrich the piece")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .disabled(!vm.ready || vm.busy || vm.selected == nil)
            if vm.isEnriched {
                Button("Undo the enrichment", action: vm.undoPart).disabled(vm.busy)
            }
            if vm.selected == nil {
                Text("Pick a melody first.").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    /// What accompanies the chosen option and in which bars, with the means to take it out again.
    private var accompanimentList: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("Accompaniment").font(.callout)
                Spacer()
                Button("Undo last") { vm.undoPart() }
                    .controlSize(.small).disabled(vm.partHistory.isEmpty || vm.busy)
            }
            let layers = vm.selected?.layers ?? []
            if layers.isEmpty {
                Text("None yet: the melody and the singing stand alone.").font(.caption).foregroundStyle(.secondary)
            }
            ForEach(Array(layers.enumerated()), id: \.offset) { _, l in
                HStack {
                    Text("\(Instrument.partName(l.program)) · bars \(l.from)–\(l.to)").font(.callout)
                    Spacer()
                    Button("Take out") { vm.clearBars(from: l.from, to: l.to, program: l.program) }
                        .controlSize(.small).disabled(vm.busy)
                        .help("Remove this instrument from the piece (everywhere it plays)")
                }
            }
            HStack {
                Text("Bars"); Spacer()
                Stepper("from \(vm.partBars.from)", value: $vm.partFrom, in: 1...max(vm.barCount, 1))
                Stepper(vm.partTo == 0 ? "to the end" : "to \(vm.partBars.to)", value: $vm.partTo, in: 0...max(vm.barCount, 1))
            }
            .help("The bars the next instrument plays in. Section by section: put a full band in some bars and leave others to the voice.")
            if !layers.isEmpty {
                Button("Take the accompaniment out of these bars") { vm.clearBars(from: vm.partBars.from, to: vm.partBars.to) }
                    .controlSize(.small).disabled(vm.busy)
                    .help("Every instrument stops in these bars, so the melody and the singing stand alone")
            }
        }
    }

    /// Add instruments one at a time: choose an instrument, hear several options, approve one, repeat.
    private var oneByOneControls: some View {
        VStack(alignment: .leading, spacing: 10) {
            if vm.currentPieceURL == nil {
                Text("Pick a melody first.").font(.caption).foregroundStyle(.secondary)
            } else {
                accompanimentList
            }
            Picker("Instrument", selection: $vm.partInstrument) {
                ForEach(Instrument.parts) { inst in
                    Text(vm.presentPrograms.contains(inst.program) ? "\(inst.name) (plays somewhere)" : inst.name).tag(inst)
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
                Text("\(vm.partInstrument.name) already plays in these bars. Take it out of them first, or choose other bars.").font(.caption).foregroundStyle(.secondary)
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
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("Mood and style (up to three)").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Surprise me", action: vm.surpriseMoods).buttonStyle(.link).font(.caption)
                    Button("Clear") { vm.moods = [] }.buttonStyle(.link).font(.caption).disabled(vm.moods.isEmpty)
                }
                ChipPicker(items: ViewModel.moodChips, selection: $vm.moods, limit: 3)
                    .help("Pick the feeling you want. Sad, dark, mysterious, dramatic and epic lean towards a minor key; the others towards major, unless you set the mode yourself.")
            }
            describeBox("Anything else? (optional)", text: $vm.prompt, height: 54,
                        example: "e.g. like a music box, rising slowly then settling")
            barsRow("Bars", $vm.bars, range: 1...4, help: "How long the motif is, in bars (measures), at most 4. At 4/4 and 80 bpm, 4 bars last about 12 seconds.")

            Picker("Instrument", selection: $vm.instrument) {
                ForEach(Instrument.all) { Text($0.name).tag($0) }
            }
            if vm.instrument.program == lyricsVoiceProgram { singerPicker($vm.singers) }
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
            VStack(alignment: .leading, spacing: 12) {
                Divider()
                Text("Character of the melody").font(.callout)
                knobRow("Smooth ↔ Jumpy", $vm.shapeSlider, readout: shapeWord(vm.shapeSlider),
                        help: "How the melody moves from note to note. Smooth: mostly small steps, like singing up and down a scale, and runs along the scale are fine. Jumpy: bigger leaps and more changes of direction, and runs are cut short; more angular and less predictable.")
                knobRow("Steady pulse", $vm.pulseSlider,
                        help: "How strongly notes start on the beat (and most of all on the first beat of a bar). Low: loose, off-beat, more improvised. High: square and regular, easy to tap your foot to.")
                knobRow("Dynamics: Flat ↔ Expressive", $vm.dynamicsSlider, readout: dynamicsWord(vm.dynamicsSlider),
                        help: "How much the loudness varies: notes on the beat a little stronger, each phrase swelling and fading, the line rising towards its highest note, and the ending tapering. Zero: every note at one level. The accompaniment gets its own setting. The volume of each instrument is in the Mixer under the preview.")
                if vm.modeHasFlavor {
                    knobRow("Modal flavor", $vm.flavorSlider,
                            help: "How strongly the melody uses the one note that gives the mode its sound (Lydian's raised 4th, Dorian's raised 6th, Phrygian's flat 2nd, Mixolydian's flat 7th, Locrian's flat 5th). Low: the notes are in the mode but its sound may not come through. High: that note is used often, on strong beats.")
                }
                phrasingControls($vm.phrasing)
                Toggle("Resolve ending (finish on the home note)", isOn: $vm.resolveEnding)
                    .help("Changes only the final note, so it ends on the tonic")
            }
            optionsRow($vm.optionCount)
            advancedBox {
                llmPicker
                knobRow("Scale: Relaxed ↔ Strict", $vm.strictSlider, readout: strictWord(vm.strictSlider),
                        help: "How closely the melody keeps to the notes of the key or mode. The allowed notes are worked out from the mode itself (not by the LLM) and listed in the log. Strict: every note is in the scale. Relaxed: some notes outside it are kept, up to about a quarter of them at zero. The ones that stay are short notes off the beat, like chromatic passing notes. The log says how many ended up outside.")
            }

            Button(action: vm.generateMelodies) {
                HStack {
                    if vm.busy && vm.selected == nil && vm.expansionHistory.isEmpty { ProgressView().controlSize(.small) }
                    Text(vm.candidates.isEmpty ? "Generate motifs" : "Regenerate motifs").frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .tint(vm.candidates.isEmpty ? .accentColor : .gray)
            .keyboardShortcut(.return, modifiers: .command)
            .disabled(!vm.ready || vm.busy)

            Button("Import MIDI as the melody…", action: vm.importMIDI)
                .disabled(!vm.ready || vm.busy)
        }
    }

    /// Step 2: add bars to the chosen melody. The same controls as step 1, with its own values.
    private var expandControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pick a motif or melody above, then grow it. The new options replace the list with the first one selected, so you can press Expand again to keep going, or pick another option first. Undo steps back.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 6) {
                Text("What should the new bars do?").font(.caption).foregroundStyle(.secondary)
                ChipPicker(items: ViewModel.moves.map(\.label), selection: $vm.expandMoves,
                           help: Dictionary(uniqueKeysWithValues: ViewModel.moves.map { ($0.label, $0.text) }))
                if vm.expandEngine == "llm" {
                    Text("“Answer the motif” is written by the language model (Advanced → Melody LLM); the other moves by the Anticipation model.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            describeBox("Anything else? (optional)", text: $vm.expandDirection, height: 54,
                        example: "e.g. build to a climax, then calm down")
                .help("Free text for this expansion. The language model reads it as an instruction; the Anticipation model turns it into how busy, how high or low and how adventurous the new bars are.")
            barsRow("Bars to add", $vm.expandBars, help: "How many bars to add on the end of the chosen melody.")
            Picker("Instrument", selection: $vm.expandInstrument) {
                ForEach(Instrument.all) { Text($0.name).tag($0) }
            }
            .help("The instrument these new bars are written for. Pick another one and the melody is passed to it from here on; with the Voice instrument the bars are written to the next lyric lines.")
            if vm.expandInstrument.program == lyricsVoiceProgram {
                singerPicker($vm.expandSingers)
                Text("These bars are written to the next lines of your lyrics (Lyrics button) and sung by this singer; the moves and sliders below do not apply.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }

            complexityRow($vm.expandComplexity)
            knobRow("Smooth ↔ Jumpy", $vm.expandShape, readout: shapeWord(vm.expandShape),
                    help: "How the new bars move from note to note. Smooth: mostly small steps. Jumpy: bigger leaps and more changes of direction, and runs along the scale are cut short.")
            knobRow("Bring the motif back", $vm.expandTheme,
                    help: "How strongly the opening idea comes back at the start of each new phrase, shifted to fit that phrase's chord. High: an obvious, memorable theme. Zero: the new bars are new material.")
            knobRow("Dynamics: Flat ↔ Expressive", $vm.expandDynamics, readout: dynamicsWord(vm.expandDynamics),
                    help: "How much the loudness varies in the new bars. Zero: every note at one level.")
            Toggle("Resolve ending (finish on the home note)", isOn: $vm.expandResolve)
                .help("Changes only the final note, so it ends on the tonic")
            optionsRow($vm.expandCount)
            advancedBox { llmPicker }

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
            } else if !vm.partOptions.isEmpty {
                Text("Approve or discard the instrument options first (step 4).")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } else if !(vm.selected?.layers.isEmpty ?? true) {
                Text("The accompaniment stays as it is, in the bars it was written for; the new bars start without it (step 4 adds it).")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Step 3: change the melody by hand (the piano roll becomes an editor) or by asking the LLM to change named bars.
    private var editControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Fix what you do not like. Only the bars you name change; every other bar stays exactly as it is, and so does the accompaniment already written.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if vm.hasSung {
                Picker("Change", selection: $vm.editPart) {
                    Text("The melody").tag("melody")
                    Text("The singing").tag("vocal")
                }
                .pickerStyle(.segmented)
                .help("Which line the edit changes. The other one is kept exactly. Each bar of the singing keeps its number of notes, one for each syllable.")
            }
            Button(action: vm.beginEditing) {
                Label("Edit notes by hand", systemImage: "pencil").frame(maxWidth: .infinity)
            }
            .controlSize(.large).disabled(!vm.canEdit || vm.editing)
            .help("Turns the piano roll into an editor: click to add a note, drag a note to change its pitch or position, drag its right edge to change its length, Delete to remove it. Save changes redraws the sheet music.")

            describeBox("Or ask the LLM to change something", text: $vm.editInstruction, height: 66,
                        example: "e.g. I don't think measure 2 leads into measure 3 very well, so connect them better")
                .help("Say what is wrong and name the bars (\"measure 2\", \"bars 3-4\"). The LLM is only given those bars to rewrite.")
            HStack {
                Text("Bars to change").foregroundStyle(.secondary)
                TextField("click bars in the roll, or type 2-3, 5", text: $vm.editBarsText)
                    .textFieldStyle(.roundedBorder)
                    .help("Exactly the bars that may change; nothing else is touched, however the sentence above is worded. Click bars in the piano roll or sheet music, or type them. If this is empty, the bars named in the sentence (\"measure 2\") are used instead.")
            }
            optionsRow($vm.editCount)
            advancedBox { llmPicker }
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
        VStack(alignment: .leading, spacing: 4) {
            Picker("Melody LLM", selection: $vm.llm) {
                Section("On this Mac") {
                    Text("Qwen 7B" + (ModelCache.isDownloaded("mlx-community/Qwen2.5-7B-Instruct-4bit") ? " (downloaded)" : " (4 GB, downloads on first use)")).tag("7B")
                    Text("Qwen 14B" + (ModelCache.isDownloaded("mlx-community/Qwen2.5-14B-Instruct-4bit") ? " (downloaded)" : " (8 GB, downloads on first use)")).tag("14B")
                    Text("Mistral Small 24B" + (ModelCache.isDownloaded("mlx-community/Mistral-Small-24B-Instruct-2501-3bit") ? " (downloaded, heavy)" : " (10 GB, downloads on first use)")).tag("mistral24")
                }
                Section("Claude through your subscription (needs Claude Code)") {
                    Text("Claude Haiku 5.5 · cheap, fastest, lightest on your limits").tag("cc-haiku-5-5")
                    Text("Claude Sonnet 5.5 · moderate cost").tag("cc-sonnet-5-5")
                    Text("Claude Opus 5.5 · expensive, uses your limits fastest").tag("cc-opus-5-5")
                }
            }
            if vm.llm.hasPrefix("cc-") {
                Text("Runs the `claude` tool on this Mac, signed in with your subscription. Sent to Anthropic; counts against your usage limits.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .help("The language model that writes the melody, and the expansions and edits if you choose LLM only. Local models download once and are kept on disk. 14B needs about 8 GB of free memory and Mistral 24B about 10 GB, which is tight on a 16 GB Mac (close other apps first). The Claude entries run through the claude tool on this Mac (your subscription, online).")
    }

    private func phrasingControls(_ phrasing: Binding<Int>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Pauses between phrases").font(.callout)
            Picker("Phrasing", selection: phrasing) {
                Text("Flowing").tag(0)
                Text("Balanced").tag(1)
                Text("Spacious").tag(2)
            }
            .pickerStyle(.segmented).labelsHidden()
            .help("Rests between ideas and phrases. Flowing: it never stops. Spacious: longer pauses and fewer notes, so each idea stands apart.")
        }
    }

    /// A 0 to 10 slider with its name and current value; hovering anywhere on it explains it.
    private func knobRow(_ title: String, _ value: Binding<Double>, readout: String? = nil, help: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title); Spacer()
                Text(readout ?? (value.wrappedValue == 0 ? "off" : "\(Int(value.wrappedValue))"))
                    .monospacedDigit().foregroundStyle(.secondary)
            }
            .contentShape(Rectangle())
            .help(help)                  // hovering the name (or anywhere on the label row) explains the slider
            Slider(value: value, in: 0...10, step: 1).help(help)
        }
    }

    private func shapeWord(_ v: Double) -> String {
        v < 2.5 ? "Smooth" : v < 7.5 ? "Balanced" : "Jumpy"
    }
    private func dynamicsWord(_ v: Double) -> String {
        v <= 0 ? "Flat" : v >= 10 ? "Very expressive" : "\(Int(v))"
    }
    private func strictWord(_ v: Double) -> String {
        v >= 10 ? "Strict" : v <= 0 ? "Relaxed" : "\(Int(v))"
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
            } else if !vm.candidates.isEmpty {
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
