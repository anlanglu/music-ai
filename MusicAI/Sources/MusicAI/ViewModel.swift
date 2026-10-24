import AVFoundation
import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct Instrument: Identifiable, Hashable {
    let name: String
    let program: Int
    /// A rule-based piano chords / arpeggios entry rather than a model-generated part.
    var texture = false
    var id: Int { program + (texture ? 1000 : 0) }
    static let all = [
        Instrument(name: "Piano", program: 0), Instrument(name: "Electric piano", program: 4),
        Instrument(name: "Guitar (nylon)", program: 24), Instrument(name: "Violin", program: 40),
        Instrument(name: "Cello", program: 42), Instrument(name: "Trumpet", program: 56),
        Instrument(name: "Alto sax", program: 65), Instrument(name: "Flute", program: 73),
        Instrument(name: "Synth lead", program: 80),
    ]
}

enum ViewMode: String { case roll, sheet }

/// Whether a Hugging Face model is already on disk, so the UI can say "downloaded" instead of "8 GB download".
enum ModelCache {
    static func isDownloaded(_ repo: String) -> Bool {
        let name = "models--" + repo.replacingOccurrences(of: "/", with: "--")
        let snaps = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".cache/huggingface/hub/\(name)/snapshots")
        guard let shas = try? FileManager.default.contentsOfDirectory(atPath: snaps.path) else { return false }
        return shas.contains { sha in
            let dir = snaps.appendingPathComponent(sha)
            let files = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
            // fileExists follows the symlink into blobs/, so a missing or partial download reads as not downloaded
            return files.contains { $0.hasSuffix(".safetensors") && FileManager.default.fileExists(atPath: dir.appendingPathComponent($0).path) }
        }
    }
}

struct Orchestration {
    let full: URL
    let notes: [Note]
    var programs: [Int] = []
}

/// Everything the results area shows, so that a step can be gone back to. The files themselves stay on disk.
struct Snapshot {
    var candidates: [Candidate]
    var selectedID: UUID?
    var orchestration: Orchestration?
    var partOptions: [PartCandidate]
    var selectedPartID: UUID?
    var partsAdded: [String]
    var partHistory: [Orchestration?]
    var expansionHistory: [(candidates: [Candidate], selectedID: UUID?)]
}

/// One step in the history pane: what was done, and the state it left behind.
struct HistoryEntry: Identifiable {
    let id = UUID()
    let title: String
    let detail: String
    let time: Date
    var snapshot: Snapshot
}

/// A note in the hand editor, in beats (not seconds), so it lines up with the bars.
struct EditNote: Identifiable, Equatable {
    var id = UUID()
    var pitch: Int
    var start: Double
    var dur: Double
}

/// The editing model's question: "bars N would have to change as well; may I?"
struct EditSuggestion {
    let bars: [Int]
    let reason: String
}

/// A key's tonic (pitch class 0-11). The mode is chosen separately, so any tonic can be combined with any mode.
struct KeyChoice: Hashable, Identifiable {
    let name: String
    let tonic: Int
    var id: String { name }
    static let all: [KeyChoice] = [("C", 0), ("C♯ / D♭", 1), ("D", 2), ("E♭", 3), ("E", 4), ("F", 5), ("F♯ / G♭", 6),
                                   ("G", 7), ("A♭", 8), ("A", 9), ("B♭", 10), ("B", 11)]
        .map { KeyChoice(name: $0.0, tonic: $0.1) }
}

/// The scales the melody can be written in. "auto" leaves it to the prompt: minor for a sad request, major otherwise.
enum Mode {
    static let all: [(id: String, name: String)] = [
        ("auto", "Auto (from prompt)"), ("major", "1 · Ionian (major)"), ("dorian", "2 · Dorian"), ("phrygian", "3 · Phrygian"),
        ("lydian", "4 · Lydian"), ("mixolydian", "5 · Mixolydian"), ("minor", "6 · Aeolian (minor)"), ("locrian", "7 · Locrian"),
    ]
}

extension Instrument {
    /// Instruments offered in "one at a time" mode (General MIDI program numbers; 128 = drums).
    static let parts: [Instrument] = [
        ("Piano", 0), ("Electric piano", 4), ("Harpsichord", 6), ("Vibraphone", 11), ("Marimba", 12), ("Organ", 19),
        ("Nylon guitar", 24), ("Steel guitar", 25), ("Acoustic bass", 32), ("Finger bass", 33), ("Violin", 40),
        ("Viola", 41), ("Cello", 42), ("Contrabass", 43), ("Harp", 46), ("Timpani", 47), ("String ensemble", 48),
        ("Choir", 52), ("Trumpet", 56), ("Trombone", 57), ("Tuba", 58), ("French horn", 60), ("Brass section", 61),
        ("Alto sax", 65), ("Tenor sax", 66), ("Oboe", 68), ("Bassoon", 70), ("Clarinet", 71), ("Piccolo", 72),
        ("Flute", 73), ("Pan flute", 75), ("Warm pad", 89), ("Drums", 128),
    ].map { Instrument(name: $0.0, program: $0.1) }
        + [Instrument(name: "Piano chords & arpeggios", program: 0, texture: true)]

    /// Left-hand patterns for the piano chords & arpeggios entry (matches texture.py).
    static let pianoStyles: [(id: String, name: String)] = [
        ("mix", "Mix of patterns"), ("arpeggio", "Arpeggios"), ("alberti", "Alberti bass"),
        ("block", "Block chords"), ("oompah", "Bass + chords"), ("sustained", "Sustained chords"),
    ]
}

@MainActor
final class ViewModel: ObservableObject {
    /// Which step the wizard in the left column shows (1 motif, 2 extend, 3 edit, 4 accompaniment).
    @Published var step = 1
    // Stage 1: melody
    @Published var prompt = ""          // anything else to say, beyond the mood chips (optional)
    @Published var moods: Set<String> = ["gentle", "hopeful"]
    static let moodChips = ["gentle", "hopeful", "playful", "joyful", "dreamy", "lullaby", "folk", "dance", "march",
                            "sad", "dark", "mysterious", "dramatic", "epic"]
    /// What is sent as the prompt: the chosen moods, then whatever was typed; a plain default when both are empty.
    var effectivePrompt: String {
        let chosen = Self.moodChips.filter { moods.contains($0) }
        let extra = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        var parts: [String] = []
        if !chosen.isEmpty { parts.append("Mood and style: \(chosen.joined(separator: ", ")).") }
        if !extra.isEmpty { parts.append(extra) }
        return parts.isEmpty ? "A simple, memorable motif." : parts.joined(separator: " ")
    }
    func surpriseMoods() { moods = Set(Self.moodChips.shuffled().prefix(2)) }
    @Published var bars = 4.0
    @Published var instrument = Instrument.all[0]
    @Published var sig = "4/4"
    static let timeSigs = ["4/4", "3/4", "2/4", "6/8", "5/4", "7/8", "12/8"]
    @Published var bpmText = "80"
    @Published var keyChoice: KeyChoice?   // nil = Auto: use a key named in the prompt, else the LLM picks one
    @Published var keyMode = "auto"        // "auto", or one of Mode.all
    @Published var resolveEnding = true
    @Published var phrasing = 1   // 0 flowing, 1 balanced, 2 spacious: rests between ideas and phrases
    // The five character sliders, each 0...10. The backend turns them into the sampler's numbers, and tells the app
    // where they start (plan.SLIDER_DEFAULTS), so neither the scale nor the defaults are repeated here.
    @Published var shapeSlider = 5.0    // smooth (0) .. jumpy (10)
    @Published var pulseSlider = 2.0    // notes start on the beat
    @Published var harmonySlider = 3.0  // the melody fits each bar's chord
    @Published var endingsSlider = 3.0  // phrases end on a stopping note
    @Published var themeSlider = 0.0    // the opening idea returns in each phrase
    @Published var runsSlider = 5.0     // one-way runs up or down the scale are avoided
    @Published var strictSlider = 10.0  // relaxed (0) .. strict (10): how many notes may be outside the scale
    @Published var dynamicsSlider = 5.0 // flat (0) .. expressive (10): how much the loudness varies
    @Published var flavorSlider = 5.0   // how much the mode's own note (Lydian #4, Dorian ♮6...) is brought out
    /// Sliders left on Auto are sent as "auto": the backend gives the LLM no instruction for them.
    @Published var autoSliders: Set<String> = []
    var sliderValues: [String: Any] {
        let v: [String: Double] = ["shape": shapeSlider, "pulse": pulseSlider, "harmony": harmonySlider,
                                   "endings": endingsSlider, "theme": themeSlider, "runs": runsSlider,
                                   "flavor": flavorSlider, "strict": strictSlider,
                                   "dynamics": dynamicsSlider]
        return v.reduce(into: [String: Any]()) { $0[$1.key] = autoSliders.contains($1.key) ? "auto" : $1.value }
    }
    private func applySliderDefaults(_ d: [String: Double]) {
        shapeSlider = d["shape"] ?? shapeSlider; pulseSlider = d["pulse"] ?? pulseSlider
        harmonySlider = d["harmony"] ?? harmonySlider; endingsSlider = d["endings"] ?? endingsSlider
        themeSlider = d["theme"] ?? themeSlider; runsSlider = d["runs"] ?? runsSlider
        flavorSlider = d["flavor"] ?? flavorSlider; strictSlider = d["strict"] ?? strictSlider
        dynamicsSlider = d["dynamics"] ?? dynamicsSlider; accompDynamics = dynamicsSlider
        copySettingsToExpansion()
    }
    /// Step 2 starts from step 1's settings; after that each step keeps its own.
    private func copySettingsToExpansion() {
        expandShape = shapeSlider; expandPulse = pulseSlider; expandHarmony = harmonySlider
        expandEndings = endingsSlider; expandTheme = themeSlider; expandRuns = runsSlider; expandFlavor = flavorSlider; expandStrict = strictSlider; expandDynamics = dynamicsSlider
        expandComplexity = complexity; expandCount = optionCount
        expandPhrasing = phrasing; expandResolve = resolveEnding; expandAuto = autoSliders
    }
    @Published var complexity = 3.0
    @Published var optionCount = 4.0
    @Published var llm = "7B" {
        didSet { if llm.hasPrefix("claude-") && !claudeKeySaved { showClaudeSheet = true } }
    }
    // Claude (cloud) as an alternative melody LLM; the key itself is only in the Keychain
    @Published var claudeKeySaved = Keychain.get() != nil
    @Published var claudeKeyOK = false
    @Published var claudeChecking = false
    @Published var claudeStatus = ""
    @Published var showClaudeSheet = false

    // Step 2: expanding the chosen melody. It has its own settings, because each expansion can want a different character.
    @Published var expandBars = 4.0                // bars added at a time
    @Published var expandEngine = "hybrid"         // "hybrid" (Anticipation continues it) or "llm"
    @Published var expandComplexity = 3.0
    @Published var expandCount = 3.0
    @Published var expandDirection = ""            // anything else to say about the new bars (optional)
    @Published var expandMoves: Set<String> = []
    /// What each "what should the new bars do" chip tells the engine. The first two only mean something to the LLM.
    static let moves: [(label: String, text: String)] = [
        ("continue", "Continue the melody naturally."), ("answer the motif", "Answer the opening motif, like a reply to it."),
        ("build up", "Build up: gradually busier and higher, with more energy."), ("climax", "Reach a climax: the highest and most intense point."),
        ("calm down", "Calm down: quieter, simpler and lower."), ("return", "Bring back the opening idea."),
        ("contrast", "Contrast with what came before: a different character."),
    ]
    static let llmOnlyMoves: Set<String> = ["answer the motif", "return"]
    var effectiveDirection: String {
        let chosen = Self.moves.filter { expandMoves.contains($0.label) }.map(\.text)
        return (chosen + [expandDirection.trimmingCharacters(in: .whitespacesAndNewlines)]).filter { !$0.isEmpty }.joined(separator: " ")
    }
    @Published var expandShape = 5.0
    @Published var expandPulse = 2.0
    @Published var expandHarmony = 3.0
    @Published var expandEndings = 3.0
    @Published var expandTheme = 0.0
    @Published var expandRuns = 5.0
    @Published var expandFlavor = 5.0
    @Published var expandStrict = 10.0
    @Published var expandDynamics = 5.0
    @Published var expandPhrasing = 1
    @Published var expandResolve = true
    @Published var expansionHistory: [(candidates: [Candidate], selectedID: UUID?)] = []   // the options before each expansion
    private var expandJustRan = false
    private var lastExpandBars = 4.0
    @Published var expandAuto: Set<String> = []
    var expandSliderValues: [String: Any] {
        let v: [String: Double] = ["shape": expandShape, "pulse": expandPulse, "harmony": expandHarmony,
                                   "endings": expandEndings, "theme": expandTheme, "runs": expandRuns,
                                   "flavor": expandFlavor, "strict": expandStrict,
                                   "dynamics": expandDynamics]
        return v.reduce(into: [String: Any]()) { $0[$1.key] = expandAuto.contains($1.key) ? "auto" : $1.value }
    }
    // Step 3: editing the melody, by hand or by asking the LLM to change only the bars that are named
    @Published var editing = false                 // the piano roll is the note editor
    @Published var editNotes: [EditNote] = []
    @Published var editSelected: UUID?
    @Published var editInstruction = ""
    @Published var editBarsText = ""
    @Published var editCount = 2.0
    @Published var editSuggestion: EditSuggestion?
    private var editLabel: String?                 // set while an edit is running; the history entry it will get
    private var editRequest: (source: URL, instruction: String, bars: [Int])?
    var editDirty: Bool { guard let c = selected else { return false }; return editNotes != Self.editNotes(of: c) }
    /// Editing, like expanding, changes the melody, so it is only offered before any accompaniment exists.
    var canEdit: Bool { canExpand }
    var canEditBusyHint: Bool { editLabel != nil }
    /// Expanding changes the melody, so it is only offered before any accompaniment exists.
    var canExpand: Bool { ready && !busy && selected != nil && orchestration == nil && partsAdded.isEmpty && partOptions.isEmpty }
    // Stage 2: accompaniment
    @Published var ensemble = 0.0
    @Published var playable = true
    @Published var inKey = true
    @Published var partDirection = ""
    @Published var directionNote = ""   // what the backend understood from the direction
    // One-at-a-time mode: pick an instrument, choose among its options, approve, repeat
    @Published var partInstrument = Instrument.parts.first { $0.program == 40 } ?? Instrument.parts[0]
    @Published var partOptionCount = 3.0
    @Published var accompDynamics = 5.0   // how much the accompaniment's loudness varies; 5 = normal
    @Published var pianoStyle = "mix"
    @Published var partOptions: [PartCandidate] = []
    @Published var selectedPartID: UUID?
    @Published var partHistory: [Orchestration?] = []   // the piece before each approval, for Undo
    @Published var partsAdded: [String] = []
    /// Step 5 is kept in the same history as step 4's parts, so Undo and History treat it like one more part.
    static let enrichLabel = "Enrichment"
    private var pendingEnrich: (base: Orchestration?, rerolled: Bool)?
    var isEnriched: Bool { partsAdded.last == Self.enrichLabel }
    @Published var size = "small"

    @Published var status = "Starting backend…"
    @Published var log = ""
    @Published var ready = false
    @Published var busy = false
    /// True when a request has been running for a long time without a single message from the backend.
    @Published var stalled = false
    /// True after the Python process has exited unexpectedly, until a new one reports ready.
    @Published var backendDown = false
    private var lastEvent = Date()
    private var watchdog: Timer?
    private static let stallSeconds: TimeInterval = 240
    @Published var candidates: [Candidate] = []
    @Published var selectedID: UUID?
    @Published var orchestration: Orchestration?

    /// Piano roll or engraved sheet music for the main view; the user's choice is remembered.
    @Published var viewMode: ViewMode = ViewMode(rawValue: UserDefaults.standard.string(forKey: "viewMode") ?? "") ?? .roll {
        didSet {
            UserDefaults.standard.set(viewMode.rawValue, forKey: "viewMode")
            ensureSheet()
        }
    }
    @Published var sheetPages: [URL: URL] = [:]   // MIDI file -> rendered HTML
    @Published var sheetLoading: Set<URL> = []
    @Published var sheetErrors: [URL: String] = [:]
    @Published var pdfBusy = false

    // Sessions: one folder per project holding every file it produces
    @Published var sessionName = ViewModel.defaultSessionName()
    @Published var sessionURL: URL?
    @Published var sessions: [SessionInfo] = []
    private var pdfJob: (midi: URL, destination: URL)?
    /// How the backend's direction report starts (see direction.describe); the app shows that line under the box.
    private static let directionPrefixes = ["Understood", "Matched keywords", "Couldn't turn"]
    private var pendingOpen: URL?   // MIDI whose MusicXML should open in a notation app once engraved
    private var exporter: PDFExporter?

    @Published var playingURL: URL?
    @Published var playhead: Double = 0

    private let backend = Backend()
    private var player: AVMIDIPlayer?
    private var timer: Timer?
    private static let soundBank = URL(fileURLWithPath: "/System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls")

    var selected: Candidate? { candidates.first { $0.id == selectedID } }
    /// What the big piano roll shows: the finished piece, else the picked melody, else the first option.
    var selectedPart: PartCandidate? { partOptions.first { $0.id == selectedPartID } }
    /// What the main view shows: a part option being auditioned (the whole piece with it), else the piece so far.
    var displayNotes: [Note] { selectedPart?.notes ?? orchestration?.notes ?? (selected ?? candidates.first)?.notes ?? [] }
    var displayURL: URL? { selectedPart?.url ?? orchestration?.full ?? (selected ?? candidates.first)?.url }
    var highlightChannel: Int? { selectedPart.map(\.channel) }
    /// The piece the next instrument will be added to: the approved piece so far, else the chosen melody.
    var currentPieceURL: URL? { orchestration?.full ?? selected?.url }
    var presentPrograms: Set<Int> { Set(orchestration?.programs ?? selected?.programs ?? []) }
    /// The piano chords entry adds notes to a piano that is already there, so it is never "taken".
    var partInstrumentTaken: Bool { !partInstrument.texture && presentPrograms.contains(partInstrument.program) }
    var displayDuration: Double { displayNotes.map(\.end).max() ?? 0 }
    var displayTitle: String {
        if orchestration != nil { return "Full score" }
        if let c = selected ?? candidates.first { return "Option \(c.index)" }
        return ""
    }
    /// The tempo typed in the field, kept inside what the app supports (blank or junk falls back to 80).
    var bpm: Int { min(max(Int(bpmText) ?? 80, 40), 220) }
    var hasPrompt: Bool { true }   // the prompt is optional: with nothing chosen a plain default is used

    func start() {
        backend.onEvent = { [weak self] ev in self?.handle(ev) }
        launchBackend()
        watchdog = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, self.busy, !self.stalled,
                      Date().timeIntervalSince(self.lastEvent) > Self.stallSeconds else { return }
                self.stalled = true
                self.status = "No response from the backend for \(Int(Self.stallSeconds / 60)) minutes. "
                    + "It may be stuck: use Restart backend."
            }
        }
    }

    private func launchBackend() {
        lastEvent = Date()
        stalled = false
        backendDown = false
        do { try backend.start() } catch {
            status = "Could not start Python backend: \(error.localizedDescription)"
        }
    }

    /// Kill the Python process and start a fresh one (used when it has crashed or stopped responding). Loaded models
    /// are lost, so the next generation loads them again. Files already written are untouched.
    func restartBackend() {
        stop()
        ready = false
        busy = false
        pdfBusy = false
        pdfJob = nil
        sheetLoading = []
        status = "Restarting the backend…"
        launchBackend()
    }

    func shutdown() { watchdog?.invalidate(); backend.stop() }

    private func handle(_ ev: BackendEvent) {
        lastEvent = Date()
        stalled = false
        switch ev {
        case .session(let name, let path, let oldPath):
            if let oldPath, let path, oldPath != path { remap(from: oldPath, to: path) }
            sessionURL = path
            sessionName = name
        case .sessions(let items):
            sessions = items
        case .ready(let sliders):
            applySliderDefaults(sliders)
            ready = true
            backendDown = false
            if let key = Keychain.get() { send(["cmd": "claude_key", "key": key]) }
            send(["cmd": "session_list"])
            status = "Ready. Describe a motif and press Generate motifs."
        case .vocal(let url):
            vocalURL = url
            playVocal()
        case .claudeStatus(let ok, let text):
            claudeChecking = false
            claudeKeyOK = ok
            claudeStatus = text
            if ok { Keychain.set(pendingClaudeKey); claudeKeySaved = true }
            pendingClaudeKey = ""
        case .log(let text):
            // direction.describe() in the backend starts its one-line report with one of these
            let trimmed = text.trimmingCharacters(in: .whitespaces)
            if Self.directionPrefixes.contains(where: { trimmed.hasPrefix($0) }) { directionNote = trimmed }
            log += text + "\n"
            status = text.trimmingCharacters(in: .whitespaces)
        case .error(let text):
            busy = false
            if expandJustRan || editLabel != nil, let last = expansionHistory.popLast() {   // the expansion or edit failed: keep what there was
                candidates = last.candidates
                selectedID = last.selectedID
            }
            expandJustRan = false
            editLabel = nil
            pendingEnrich = nil
            pdfBusy = false      // whatever was running is over: don't leave the buttons locked
            pdfJob = nil
            sheetLoading = []
            if text.hasPrefix("Python backend exited") { ready = false; backendDown = true }
            status = text
            log += "ERROR: \(text)\n"
        case .candidate(let c):
            candidates.append(c)
            status = "Option \(c.index) ready"
            ensureSheet()
        case .notation(let midi, let html, let paged):
            if paged {
                if let job = pdfJob, job.midi == midi { writePDF(html: html, to: job.destination) }
            } else {
                sheetLoading.remove(midi)
                sheetPages[midi] = html
                if pendingOpen == midi { pendingOpen = nil; openMusicXML(for: midi) }
            }
        case .notationError(let midi, let text):
            sheetLoading.remove(midi)
            sheetErrors[midi] = text
            if pdfBusy, pdfJob?.midi == midi {
                pdfBusy = false
                pdfJob = nil
                status = "PDF export failed: \(text)"
            }
        case .melodiesDone:
            busy = false
            if let label = editLabel {
                editLabel = nil
                selectedID = candidates.first?.id
                record(label, "\(candidates.count) option\(candidates.count == 1 ? "" : "s")")
                status = candidates.count == 1 ? "Done. The sheet music shows the change. Edit again, or add the accompaniment."
                                               : "Pick the version you like, edit again, or add the accompaniment."
                if let c = candidates.first { ensureSheet(c.url) }
            } else if expandJustRan {
                expandJustRan = false
                selectedID = candidates.first?.id     // so Expand can go straight on from it; click another to switch
                record("Expanded by \(Int(lastExpandBars)) bars", "\(candidates.count) options")
                status = "Option 1 is selected. Pick the one you like, then expand again or add the accompaniment."
            } else {
                copySettingsToExpansion()
                record("Motifs", "\(candidates.count) option\(candidates.count == 1 ? "" : "s")")
                status = candidates.count == 1 ? "Motif ready — extend it, edit it, or generate again."
                                               : "Pick the melody you like, or generate new options."
            }
        case .mix(let path, let channels):
            gotMix(path, channels)
        case .editSuggestion(let bars, let reason, _):
            editSuggestion = EditSuggestion(bars: bars, reason: reason)
        case .partCandidate(let c):
            partOptions.append(c)
            status = "\(partInstrument.name) option \(c.index) ready"
        case .partsDone:
            busy = false
            record("\(partInstrument.name) options", "\(partOptions.count) to choose from")
            status = "Choose the \(partInstrument.name.lowercased()) part you like, then approve it — or generate new options."
        case .partApproved(let url, let notes, let programs):
            busy = false
            partHistory.append(orchestration)
            partsAdded.append(partInstrument.name)
            orchestration = Orchestration(full: url, notes: notes, programs: programs)
            partOptions = []
            selectedPartID = nil
            record("Added \(partInstrument.name.lowercased())", partsAdded.joined(separator: ", "))
            status = "Added \(partInstrument.name.lowercased()). Pick another instrument, or you're done."
            ensureSheet()
            play(url)
        case .done(_, let full, let notes, let programs):
            busy = false
            if let full {
                if let p = pendingEnrich {                    // an enrichment, not a session being reopened
                    if p.rerolled { _ = partHistory.popLast(); _ = partsAdded.popLast() }
                    partHistory.append(p.base)
                    partsAdded.append(Self.enrichLabel)
                    pendingEnrich = nil
                }
                orchestration = Orchestration(full: full, notes: notes, programs: programs)
                record("Enriched", "\(notes.count) notes")
                ensureSheet()
                status = "Done — \(notes.count) notes. Press Play, or re-roll the accompaniment."
                play(full)
            }
        }
    }

    private var pendingClaudeKey = ""

    // MARK: Lyrics (demo): a melody with one note per syllable, sung by SoulX-Singer

    @Published var lyrics = ""
    @Published var showLyrics = false
    @Published var vocalURL: URL?
    @Published var vocalPlaying = false
    private var vocalPlayer: AVAudioPlayer?

    func makeSong() {
        guard ready, !busy, !lyrics.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        clearWork()
        vocalURL = nil
        busy = true
        status = "Writing a melody for the lyrics, then singing it (about a minute for four lines)…"
        send(["cmd": "lyrics_song", "lyrics": lyrics, "bpm": bpm, "sig": sig,
              "key": keyChoice.map { [$0.tonic, keyMode] as [Any] } ?? [], "mode": keyMode, "complexity": Int(complexity),
              "llm": llm, "mood": effectivePrompt, "session_name": sessionName])
    }

    func playVocal() {
        guard let url = vocalURL else { return }
        stop()
        do {
            let p = try AVAudioPlayer(contentsOf: url)
            vocalPlayer = p
            p.play()
            vocalPlaying = true
            DispatchQueue.main.asyncAfter(deadline: .now() + p.duration + 0.2) { [weak self] in
                if self?.vocalPlayer === p, !p.isPlaying { self?.vocalPlaying = false }
            }
        } catch { status = "Couldn't play the voice: \(error.localizedDescription)" }
    }

    func stopVocal() { vocalPlayer?.stop(); vocalPlaying = false }

    /// Tests the key with one tiny request, and keeps it in the Keychain only if Anthropic accepts it.
    func saveClaudeKey(_ key: String) {
        let key = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { return }
        pendingClaudeKey = key
        claudeChecking = true
        claudeStatus = "Checking the key…"
        send(["cmd": "claude_key", "key": key, "test": true])
    }

    func removeClaudeKey() {
        Keychain.remove()
        claudeKeySaved = false; claudeKeyOK = false; claudeStatus = ""
        send(["cmd": "claude_key", "key": ""])
        if llm.hasPrefix("claude-") { llm = "7B" }
    }

    private func send(_ request: [String: Any]) {
        do { try backend.send(request) } catch {
            busy = false
            status = "Failed to send request: \(error.localizedDescription)"
        }
    }

    // MARK: Sessions

    /// A rename moves the session folder, so every file URL we hold has to move with it.
    private func remap(from old: URL, to new: URL) {
        // match whole path components, so renaming "x" doesn't also move a sibling folder called "x-2"
        func moved(_ u: URL) -> URL {
            let o = old.standardizedFileURL.path
            guard u.path == o || u.path.hasPrefix(o + "/") else { return u }
            return URL(fileURLWithPath: new.path + u.path.dropFirst(o.count))
        }
        stop()
        candidates = candidates.map {
            Candidate(id: $0.id, index: $0.index, url: moved($0.url), tempo: $0.tempo, sig: $0.sig, score: $0.score,
                      notes: $0.notes, programs: $0.programs)
        }
        if let o = orchestration { orchestration = Orchestration(full: moved(o.full), notes: o.notes, programs: o.programs) }
        partOptions = partOptions.map {
            PartCandidate(id: $0.id, index: $0.index, url: moved($0.url), program: $0.program, channel: $0.channel,
                          added: $0.added, label: $0.label, notes: $0.notes, programs: $0.programs)
        }
        partHistory = partHistory.map { prev in
            prev.map { Orchestration(full: moved($0.full), notes: $0.notes, programs: $0.programs) }
        }
        sheetPages = [:]
        sheetErrors = [:]
        sheetLoading = []
        ensureSheet()
    }

    private func clearWork() {
        stop()
        editing = false
        editSuggestion = nil
        expansionHistory = []
        candidates = []
        selectedID = nil
        orchestration = nil
        partOptions = []
        selectedPartID = nil
        partHistory = []
        partsAdded = []
        sheetPages = [:]
        sheetErrors = [:]
        sheetLoading = []
        log = ""
    }

    /// Commit the name typed in the session field. Before the first generation this just sets the name the
    /// session will be created with; afterwards it renames the folder.
    static func defaultSessionName() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return "Session " + f.string(from: Date())
    }

    func renameSession() {
        let name = sessionName.trimmingCharacters(in: .whitespaces)
        guard ready, !busy, !name.isEmpty else { return }
        send(["cmd": "session_rename", "name": name])
    }

    func newSession() {
        guard ready, !busy else { return }
        clearWork()
        resetHistory()
        step = 1
        sessionName = Self.defaultSessionName()
        status = "New session. Describe a motif and press Generate motifs."
        send(["cmd": "session_new"])
    }

    func openSession(_ info: SessionInfo) {
        guard ready, !busy, info.path != sessionURL else { return }
        clearWork()
        resetHistory()
        step = 1
        status = "Opening \(info.name)…"
        send(["cmd": "session_open", "path": info.path.path])
    }

    func revealSession() {
        if let url = sessionURL { NSWorkspace.shared.activateFileViewerSelecting([url]) }
    }

    // MARK: Stage 1

    func generateMelodies() {
        guard ready, !busy, hasPrompt else { return }
        clearWork()
        busy = true
        bpmText = String(bpm)  // show the value actually used
        status = "Composing \(Int(optionCount)) motif options…"
        send([
            "cmd": "melodies", "prompt": effectivePrompt, "bars": Int(bars), "program": instrument.program,
            "sig": sig, "bpm": bpm, "resolve": resolveEnding, "phrasing": phrasing,
            "key": keyChoice.map { [$0.tonic, keyMode] as [Any] } ?? [], "mode": keyMode, "complexity": Int(complexity), "llm": llm,
            "count": Int(optionCount), "session_name": sessionName,
            "sliders": sliderValues,
        ])
    }

    func importMIDI() {
        guard ready, !busy else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = ["mid", "midi"].compactMap { UTType(filenameExtension: $0) }
        panel.allowsMultipleSelection = false
        panel.message = "Choose a MIDI file to use as the melody"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        clearWork()
        busy = true
        status = "Importing \(url.lastPathComponent)…"
        send(["cmd": "import", "path": url.path, "session_name": sessionName])
    }

    func select(_ c: Candidate) {
        guard !busy else { return }
        if selectedID != c.id {
            editing = false
            selectedID = c.id
            orchestration = nil  // a different melody means the old accompaniment no longer applies
            partOptions = []
            selectedPartID = nil
            partHistory = []
            partsAdded = []
            ensureSheet()
        }
        refreshCurrentEntry()
    }

    // MARK: History: every step taken in this session, each one a place to go back to

    @Published var history: [HistoryEntry] = []
    @Published var currentEntryID: UUID?

    private func snapshot() -> Snapshot {
        Snapshot(candidates: candidates, selectedID: selectedID, orchestration: orchestration, partOptions: partOptions,
                 selectedPartID: selectedPartID, partsAdded: partsAdded, partHistory: partHistory,
                 expansionHistory: expansionHistory)
    }

    private func record(_ title: String, _ detail: String = "") {
        let e = HistoryEntry(title: title, detail: detail, time: Date(), snapshot: snapshot())
        history.append(e)
        currentEntryID = e.id
    }

    /// Choosing an option is not a step of its own, but the step it happened in should remember it.
    private func refreshCurrentEntry() {
        guard let id = currentEntryID, let i = history.firstIndex(where: { $0.id == id }) else { return }
        history[i].snapshot = snapshot()
    }

    private func resetHistory() {
        history = []
        currentEntryID = nil
    }

    /// Go back to how things were after a step. Later steps stay in the list, and whatever you do next is added after them.
    func goBack(to entry: HistoryEntry) {
        guard !busy, entry.id != currentEntryID else { return }
        stop()
        editing = false
        editSuggestion = nil
        let s = entry.snapshot
        candidates = s.candidates
        selectedID = s.selectedID
        orchestration = s.orchestration
        partOptions = s.partOptions
        selectedPartID = s.selectedPartID
        partsAdded = s.partsAdded
        partHistory = s.partHistory
        expansionHistory = s.expansionHistory
        expandJustRan = false
        currentEntryID = entry.id
        status = "Back to: \(entry.title). Carry on from here."
        ensureSheet()
    }

    // MARK: Stage 2: expand the melody

    func expandMelody() {
        guard canExpand, let c = selected else { return }
        stop()
        expansionHistory.append((candidates, selectedID))
        candidates = []
        selectedID = nil
        expandJustRan = true
        lastExpandBars = expandBars
        busy = true
        status = "Expanding the melody by \(Int(expandBars)) bars…"
        send([
            "cmd": "expand", "source_midi": c.url.path, "bars": Int(expandBars), "engine": expandEngine,
            "count": Int(expandCount), "size": size, "llm": llm, "complexity": Int(expandComplexity),
            "sliders": expandSliderValues, "direction": effectiveDirection, "resolve": expandResolve, "phrasing": expandPhrasing,
        ])
    }

    /// Go back to the options (and the choice) from before the last expansion.
    func undoExpansion() {
        guard !busy, let last = expansionHistory.popLast() else { return }
        stop()
        candidates = last.candidates
        selectedID = last.selectedID
        record("Undid the expansion")
        status = "Back to the melody before the expansion."
    }

    // MARK: Stage 3: edit the melody

    private static func editNotes(of c: Candidate) -> [EditNote] {
        let k = Double(c.tempo) / 60
        return c.notes.filter { $0.channel != 9 }.sorted { $0.start < $1.start }.map {
            EditNote(pitch: $0.pitch, start: (($0.start * k) * 4).rounded() / 4, dur: max((($0.end - $0.start) * k * 4).rounded() / 4, 0.25))
        }
    }

    /// Beats in a bar of the chosen melody (6/8 counts as 3 quarter notes).
    var beatsPerBar: Double {
        let p = (selected?.sig ?? "4/4").split(separator: "/").compactMap { Double($0) }
        return p.count == 2 ? p[0] * 4 / p[1] : 4
    }

    func beginEditing() {
        guard canEdit, let c = selected else { return }
        stop()
        editNotes = Self.editNotes(of: c)
        editSelected = nil
        editSuggestion = nil
        viewMode = .roll
        editing = true
        status = "Editing by hand. Save changes when you are done; the sheet music is redrawn from the saved melody."
    }

    func cancelEditing() {
        editing = false
        status = "Edit discarded."
    }

    /// Write the hand-edited melody as a new version. The sheet music is engraved again from the new file.
    func saveEditing() {
        guard ready, !busy, let c = selected, !editNotes.isEmpty else { return }
        editing = false
        guard editDirty else { status = "Nothing was changed."; return }
        startEdit(label: "Edited by hand")
        busy = true
        status = "Saving your edit…"
        send(["cmd": "save_edit", "source_midi": c.url.path,
              "notes": editNotes.sorted { $0.start < $1.start }.map { [$0.pitch, $0.start, $0.dur] as [Any] }])
    }

    private func startEdit(label: String) {
        stop()
        expansionHistory.append((candidates, selectedID))
        candidates = []
        selectedID = nil
        editLabel = label
    }

    /// Bar lines are drawn while a single melody is shown (not the accompanied piece or a part audition).
    var barsShown: Bool { selected != nil && orchestration == nil && selectedPart == nil }
    var secondsPerBar: Double { beatsPerBar * 60 / Double(max(selected?.tempo ?? 120, 1)) }
    var chosenBars: Set<Int> { Set(barsFromText(editBarsText)) }

    /// Click a bar to choose it or drop it; the Bars box always shows the choice.
    func toggleBar(_ bar: Int) {
        var bars = chosenBars
        if bars.contains(bar) { bars.remove(bar) } else { bars.insert(bar) }
        var parts: [String] = [], run: [Int] = []
        func flush() { if let f = run.first, let l = run.last { parts.append(f == l ? "\(f)" : "\(f)-\(l)") }; run = [] }
        for b in bars.sorted() { if let l = run.last, b != l + 1 { flush() }; run.append(b) }
        flush()
        editBarsText = parts.joined(separator: ", ")
    }

    /// "2-3, 5" -> [2, 3, 5]
    private func barsFromText(_ text: String) -> [Int] {
        var out = Set<Int>()
        for part in text.split(whereSeparator: { ",; ".contains($0) }) {
            let ends = part.split(whereSeparator: { "-–".contains($0) }).compactMap { Int($0) }
            if ends.count == 2, ends[0] <= ends[1], ends[1] - ends[0] < 64 { out.formUnion(ends[0]...ends[1]) }
            else if ends.count == 1 { out.insert(ends[0]) }
        }
        return out.sorted()
    }

    /// Ask the LLM to change the bars named in the instruction (and in the bars box) and nothing else.
    func editWithLLM(extraBars: [Int] = []) {
        guard ready, canEdit, let c = selected else { return }
        let instruction = editRequest?.instruction ?? editInstruction
        let source = editRequest?.source ?? c.url
        let bars = extraBars.isEmpty ? barsFromText(editBarsText) : extraBars
        editRequest = (source, instruction, bars)
        guard !instruction.trimmingCharacters(in: .whitespaces).isEmpty || !bars.isEmpty else {
            status = "Say what to change, and which bars (for example \"measure 2 should lead into measure 3\")."
            return
        }
        editing = false
        editSuggestion = nil
        startEdit(label: "Edited with the LLM")
        busy = true
        status = "Rewriting only the bars you named…"
        send(["cmd": "edit", "source_midi": source.path, "instruction": instruction, "bars": bars,
              "count": Int(editCount), "llm": llm, "complexity": Int(complexity)])
    }

    func startLLMEdit() {
        editRequest = nil
        editWithLLM()
    }

    /// The user agrees that the extra bars may change too: take back the answer that left them alone and run it again.
    func allowSuggestedBars() {
        guard let sug = editSuggestion, let req = editRequest, !busy else { return }
        if let last = expansionHistory.popLast() {
            candidates = last.candidates
            selectedID = last.selectedID
        }
        let known = barsFromText(editBarsText)
        editWithLLM(extraBars: Array(Set(req.bars + known + sug.bars)).sorted())
    }

    func declineSuggestedBars() {
        editSuggestion = nil
    }

    // MARK: Stage 4: accompaniment

    /// Step 5: let the music model add a whole ensemble around the piece so far (the melody and any parts approved in
    /// step 4). Pressing it again starts from the piece as it was before the enrichment.
    func addAccompaniment() {
        guard ready, !busy, let c = selected else { return }
        stop()
        let rerolling = isEnriched
        let base: Orchestration? = rerolling ? (partHistory.last ?? nil) : orchestration
        pendingEnrich = (base, rerolling)
        partOptions = []
        selectedPartID = nil
        busy = true
        status = "Enriching the piece…"
        send([
            "cmd": "orchestrate", "source_midi": (base?.full ?? c.url).path, "ensemble": Int(ensemble), "size": size,
            "playable": playable, "in_key": inKey, "dynamics": Int(accompDynamics),
        ])
    }

    /// Engrave whatever the main view is showing, once, when the sheet music view is on.
    func ensureSheet() {
        guard viewMode == .sheet, let url = displayURL else { return }
        ensureSheet(url)
    }

    /// Engrave one file now, whichever view is showing, so its sheet music is waiting (used for freshly edited melodies).
    func ensureSheet(_ url: URL) {
        guard ready,
              sheetPages[url] == nil, !sheetLoading.contains(url), sheetErrors[url] == nil else { return }
        sheetLoading.insert(url)
        send(["cmd": "notation", "path": url.path, "title": displayTitle])
    }

    // MARK: Stage 2, one instrument at a time

    func generatePartOptions() {
        guard ready, !busy, let source = currentPieceURL, !partInstrumentTaken else { return }
        stop()
        busy = true
        partOptions = []
        selectedPartID = nil
        directionNote = ""
        status = "Writing \(Int(partOptionCount)) \(partInstrument.name.lowercased()) options…"
        send([
            "cmd": "add_part", "source_midi": source.path, "program": partInstrument.program,
            "count": Int(partOptionCount), "size": size,
            "playable": playable, "in_key": inKey, "dynamics": Int(accompDynamics),
            "texture": partInstrument.texture, "style": pianoStyle, "direction": partDirection,
        ])
    }

    func selectPart(_ c: PartCandidate) {
        guard !busy else { return }
        selectedPartID = c.id
        refreshCurrentEntry()
        ensureSheet()
    }

    func approvePart() {
        guard ready, !busy, let c = selectedPart else { return }
        stop()
        busy = true
        status = "Adding \(partInstrument.name.lowercased()) to the piece…"
        send(["cmd": "approve_part", "path": c.url.path, "label": partInstrument.name])
    }

    /// Throw away the most recently approved instrument and go back to the piece before it.
    func undoPart() {
        guard !busy, let previous = partHistory.popLast() else { return }
        stop()
        orchestration = previous
        let removed = partsAdded.popLast() ?? "part"
        partOptions = []
        selectedPartID = nil
        record("Removed \(removed.lowercased())")
        status = "Removed \(removed.lowercased())."
        ensureSheet()
    }

    // MARK: Edit in a notation app

    private func musicXMLURL(for midi: URL) -> URL { midi.deletingPathExtension().appendingPathExtension("musicxml") }

    /// Open the score's MusicXML in whatever app handles it (MuseScore, Dorico, Sibelius...), engraving first if needed.
    func openInNotationApp() {
        guard let midi = displayURL else { return }
        if FileManager.default.fileExists(atPath: musicXMLURL(for: midi).path) {
            openMusicXML(for: midi)
        } else {
            pendingOpen = midi
            status = "Preparing the score…"
            send(["cmd": "notation", "path": midi.path, "title": displayTitle])
        }
    }

    private func openMusicXML(for midi: URL) {
        let xml = musicXMLURL(for: midi)
        guard let app = NSWorkspace.shared.urlForApplication(toOpen: xml) else {
            status = "No app is set up to open MusicXML. Install MuseScore Studio (free, musescore.org), then try again."
            NSWorkspace.shared.activateFileViewerSelecting([xml])
            return
        }
        NSWorkspace.shared.open([xml], withApplicationAt: app, configuration: NSWorkspace.OpenConfiguration())
        status = "Opened in \(app.deletingPathExtension().lastPathComponent)."
    }

    // MARK: PDF export

    /// Ask where to save, have the backend lay the score out on A4 pages, then print those pages to PDF.
    func exportPDF() {
        guard ready, !pdfBusy, let midi = displayURL else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.pdf]
        panel.nameFieldStringValue = midi.deletingPathExtension().lastPathComponent + ".pdf"
        panel.directoryURL = midi.deletingLastPathComponent()
        panel.message = "Save the sheet music as a PDF"
        guard panel.runModal() == .OK, let dest = panel.url else { return }
        pdfJob = (midi, dest)
        pdfBusy = true
        status = "Preparing PDF…"
        send(["cmd": "notation", "path": midi.path, "title": displayTitle, "paged": true])
    }

    private func writePDF(html: URL, to destination: URL) {
        let exporter = PDFExporter()
        self.exporter = exporter
        Task {
            do {
                try await exporter.export(html: html, to: destination)
                status = "Saved \(destination.lastPathComponent)"
                NSWorkspace.shared.activateFileViewerSelecting([destination])
            } catch {
                status = "PDF export failed: \(error.localizedDescription)"
            }
            pdfBusy = false
            pdfJob = nil
            self.exporter = nil
        }
    }

    // MARK: Mixer: the volume of each instrument, kept in the MIDI file itself

    @Published var mix: [MixChannel] = []
    private var mixURL: URL?
    private var volumeWork: DispatchWorkItem?
    private var resumeAt: Double?       // where to carry on playing once a changed volume has been written to the file

    /// Ask for the volumes of whatever the main view is showing (called when that changes).
    func loadMix() {
        guard ready, let url = displayURL else { mix = []; mixURL = nil; return }
        guard url != mixURL else { return }
        mix = []
        send(["cmd": "mix_info", "path": url.path])
    }

    func setVolume(_ channel: Int, _ volume: Int) {
        guard let url = displayURL, let i = mix.firstIndex(where: { $0.channel == channel }) else { return }
        mix[i].volume = volume
        volumeWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            if self.playingURL == url { self.resumeAt = self.playhead }
            self.send(["cmd": "set_volume", "path": url.path, "channel": channel, "volume": volume])
        }
        volumeWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: work)    // one write when the slider stops moving
    }

    private func gotMix(_ path: URL, _ channels: [MixChannel]) {
        if path == displayURL { mix = channels; mixURL = path }
        if let at = resumeAt, path == playingURL {                 // the file changed under the player: carry on from where it was
            resumeAt = nil
            play(path, from: at)
        }
    }

    // MARK: Playback

    func play(_ url: URL, from position: Double = 0) {
        stop()
        do {
            let p = try AVMIDIPlayer(contentsOf: url, soundBankURL: Self.soundBank)
            p.prepareToPlay()
            p.currentPosition = position
            player = p
            playingURL = url
            p.play { [weak self] in
                DispatchQueue.main.async { self?.finishedPlaying() }
            }
            timer = Timer.scheduledTimer(withTimeInterval: 1 / 30, repeats: true) { [weak self] _ in
                DispatchQueue.main.async { if let s = self, let pl = s.player { s.playhead = pl.currentPosition } }
            }
        } catch {
            status = "Playback failed: \(error.localizedDescription)"
        }
    }

    func stop() {
        player?.stop()
        player = nil
        finishedPlaying()
    }

    private func finishedPlaying() {
        timer?.invalidate()
        timer = nil
        playingURL = nil
        playhead = 0
    }

    func reveal() {
        if let url = displayURL { NSWorkspace.shared.activateFileViewerSelecting([url]) }
    }
}
