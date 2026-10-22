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

enum AccompMode: String { case auto, oneByOne }

/// A musical key the melody can be forced into (tonic pitch class 0-11, mode).
struct KeyChoice: Hashable, Identifiable {
    let name: String
    let tonic: Int
    let mode: String
    var id: String { name }
    static let all: [KeyChoice] = {
        let major = [("C", 0), ("D♭", 1), ("D", 2), ("E♭", 3), ("E", 4), ("F", 5), ("F♯", 6), ("G", 7), ("A♭", 8), ("A", 9), ("B♭", 10), ("B", 11)]
        let minor = [("C", 0), ("C♯", 1), ("D", 2), ("E♭", 3), ("E", 4), ("F", 5), ("F♯", 6), ("G", 7), ("G♯", 8), ("A", 9), ("B♭", 10), ("B", 11)]
        return major.map { KeyChoice(name: "\($0.0) major", tonic: $0.1, mode: "major") }
             + minor.map { KeyChoice(name: "\($0.0) minor", tonic: $0.1, mode: "minor") }
    }()
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
    // Stage 1: melody
    @Published var prompt = "a gentle, hopeful melody in C major"
    @Published var bars = 8.0
    @Published var instrument = Instrument.all[0]
    @Published var sig = "4/4"
    static let timeSigs = ["4/4", "3/4", "2/4", "6/8", "5/4", "7/8", "12/8"]
    @Published var bpmText = "80"
    @Published var keyChoice: KeyChoice?   // nil = Auto: use a key named in the prompt, else none
    @Published var resolveEnding = true
    @Published var shapeAmount = 1.0   // 0 stepwise .. 1 balanced .. 2 angular: leaps and turns vs scale runs
    @Published var phrasing = 1   // 0 flowing, 1 balanced, 2 spacious: rests between ideas and phrases
    // How strongly the melody follows the plan, each from 0 (not at all) to its maximum. All three at 0 is a free
    // melody. The defaults are what the app did before these were sliders: chords and cadences on, the idea off.
    @Published var knobChord = 2.2      // prefers the notes of the chord of the bar it is in
    @Published var knobCadence = 5.0    // is pulled towards the note each phrase cadences on
    @Published var knobMotif = 0.0      // is pulled back to the opening idea at the start of each phrase
    @Published var knobBeat = 0.5       // note beginnings prefer the beat
    @Published var complexity = 3.0
    @Published var optionCount = 4.0
    /// Which generator writes the melody: "hybrid" (LLM seed + Anticipation, the default), "llm", or "theory".
    @Published var engine: String = UserDefaults.standard.string(forKey: "melodyEngine") ?? "hybrid" {
        didSet { UserDefaults.standard.set(engine, forKey: "melodyEngine") }
    }
    @Published var llm = "7B"
    // Stage 2: accompaniment
    @Published var ensemble = 0.0
    @Published var playable = true
    @Published var inKey = true
    @Published var partDirection = ""
    @Published var llmDirections = true   // an LLM reads the direction; off = fixed keyword vocabulary only
    @Published var directionNote = ""   // what the backend understood from the direction
    @Published var accompMode: AccompMode = AccompMode(rawValue: UserDefaults.standard.string(forKey: "accompMode") ?? "") ?? .auto {
        didSet { UserDefaults.standard.set(accompMode.rawValue, forKey: "accompMode") }
    }
    // One-at-a-time mode: pick an instrument, choose among its options, approve, repeat
    @Published var partInstrument = Instrument.parts.first { $0.program == 40 } ?? Instrument.parts[0]
    @Published var partOptionCount = 3.0
    @Published var pianoStyle = "mix"
    @Published var partOptions: [PartCandidate] = []
    @Published var selectedPartID: UUID?
    @Published var partHistory: [Orchestration?] = []   // the piece before each approval, for Undo
    @Published var partsAdded: [String] = []
    @Published var size = "small"
    @Published var topP = 0.98
    @Published var attempts = 8.0
    @Published var minDensity = 1.5
    @Published var enforceMeter = true
    @Published var showGeneration = false

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
    @Published var sessionName = ""
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
    var hasPrompt: Bool { !prompt.trimmingCharacters(in: .whitespaces).isEmpty }

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

    /// The plan's strengths, as the backend is sent them. It works out from these how much of the plan is in use.
    var knobValues: [String: Double] {
        ["chord": knobChord, "cadence": knobCadence, "motif": knobMotif, "beat": knobBeat]
    }

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
        case .ready:
            ready = true
            backendDown = false
            send(["cmd": "session_list"])
            status = "Ready. Describe a melody and press Generate melodies."
        case .log(let text):
            // direction.describe() in the backend starts its one-line report with one of these
            let trimmed = text.trimmingCharacters(in: .whitespaces)
            if Self.directionPrefixes.contains(where: { trimmed.hasPrefix($0) }) { directionNote = trimmed }
            log += text + "\n"
            status = text.trimmingCharacters(in: .whitespaces)
        case .error(let text):
            busy = false
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
            status = candidates.count == 1 ? "Melody ready — add the accompaniment, or generate again."
                                           : "Pick the melody you like, or generate new options."
        case .partCandidate(let c):
            partOptions.append(c)
            status = "\(partInstrument.name) option \(c.index) ready"
        case .partsDone:
            busy = false
            status = "Choose the \(partInstrument.name.lowercased()) part you like, then approve it — or generate new options."
        case .partApproved(let url, let notes, let programs):
            busy = false
            partHistory.append(orchestration)
            partsAdded.append(partInstrument.name)
            orchestration = Orchestration(full: url, notes: notes, programs: programs)
            partOptions = []
            selectedPartID = nil
            status = "Added \(partInstrument.name.lowercased()). Pick another instrument, or you're done."
            ensureSheet()
            play(url)
        case .done(_, let full, let notes, let programs):
            busy = false
            if let full {
                orchestration = Orchestration(full: full, notes: notes, programs: programs)
                ensureSheet()
                status = "Done — \(notes.count) notes. Press Play, or re-roll the accompaniment."
                play(full)
            }
        }
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
    func renameSession() {
        let name = sessionName.trimmingCharacters(in: .whitespaces)
        guard ready, !busy, !name.isEmpty else { return }
        send(["cmd": "session_rename", "name": name])
    }

    func newSession() {
        guard ready, !busy else { return }
        clearWork()
        sessionName = ""
        status = "New session. Describe a melody and press Generate melodies."
        send(["cmd": "session_new"])
    }

    func openSession(_ info: SessionInfo) {
        guard ready, !busy, info.path != sessionURL else { return }
        clearWork()
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
        status = "Composing \(Int(optionCount)) melody options…"
        send([
            "cmd": "melodies", "prompt": prompt, "bars": Int(bars), "program": instrument.program,
            "sig": sig, "bpm": bpm, "resolve": resolveEnding, "phrasing": phrasing, "shape_amount": shapeAmount,
            "key": keyChoice.map { [$0.tonic, $0.mode] as [Any] } ?? [], "complexity": Int(complexity), "llm": llm, "melody_engine": engine,
            "count": Int(optionCount), "size": size, "session_name": sessionName,
            "knobs": knobValues,
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
            selectedID = c.id
            orchestration = nil  // a different melody means the old accompaniment no longer applies
            partOptions = []
            selectedPartID = nil
            partHistory = []
            partsAdded = []
            ensureSheet()
        }
    }

    // MARK: Stage 2

    func addAccompaniment() {
        guard ready, !busy, let c = selected else { return }
        stop()
        busy = true
        orchestration = nil
        status = "Adding accompaniment…"
        send([
            "cmd": "orchestrate", "source_midi": c.url.path, "ensemble": Int(ensemble), "size": size,
            "top_p": topP, "attempts": Int(attempts), "min_density": minDensity, "enforce_meter": enforceMeter,
            "playable": playable, "in_key": inKey,
        ])
    }

    /// Engrave whatever the main view is showing, once, when the sheet music view is on.
    func ensureSheet() {
        guard viewMode == .sheet, ready, let url = displayURL,
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
            "count": Int(partOptionCount), "size": size, "top_p": topP, "attempts": Int(attempts),
            "enforce_meter": enforceMeter, "playable": playable, "in_key": inKey,
            "texture": partInstrument.texture, "style": pianoStyle, "direction": partDirection,
            "llm_directions": llmDirections,
        ])
    }

    func selectPart(_ c: PartCandidate) {
        guard !busy else { return }
        selectedPartID = c.id
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

    // MARK: Playback

    func play(_ url: URL) {
        stop()
        do {
            let p = try AVMIDIPlayer(contentsOf: url, soundBankURL: Self.soundBank)
            p.prepareToPlay()
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
