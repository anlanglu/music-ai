import Foundation

struct Note: Identifiable {
    let id = UUID()
    let channel: Int, pitch: Int
    let start: Double, end: Double
}

/// One melody option produced by stage 1 (or an imported file).
struct Candidate: Identifiable {
    var id = UUID()
    let index: Int
    let url: URL
    let tempo: Int
    let sig: String
    let score: Double?
    let notes: [Note]
    var programs: [Int] = []   // General MIDI program of each instrument in the file (128 = drums)
    var duration: Double { notes.map(\.end).max() ?? 0 }
}

/// One option for the instrument being added in "one at a time" mode: the whole piece with that part included.
struct PartCandidate: Identifiable {
    var id = UUID()
    let index: Int
    let url: URL
    let program: Int
    let channel: Int          // the new part's MIDI channel, so it can be highlighted
    let added: Int            // how many notes the new part has
    var label = ""            // e.g. "Arpeggios" for the piano chords entry
    let notes: [Note]
    let programs: [Int]
}

/// One instrument's volume (MIDI controller 7, 0...127) in a MIDI file.
struct MixChannel: Identifiable {
    let channel: Int
    let name: String
    var volume: Int
    let notes: Int
    var id: Int { channel }
}

/// A saved session folder.
struct SessionInfo: Identifiable {
    let name: String
    let path: URL
    let created: String
    let files: Int
    var id: URL { path }
}

enum BackendEvent {
    case ready([String: Double])
    case log(String)
    case candidate(Candidate)
    case melodiesDone
    case done(melody: URL, full: URL?, notes: [Note], programs: [Int])
    case partCandidate(PartCandidate)
    case partsDone
    /// The editing model says these other bars would have to change too; nothing was changed in them.
    case editSuggestion(bars: [Int], reason: String, targets: [Int])
    case partApproved(url: URL, notes: [Note], programs: [Int])
    case mix(path: URL, channels: [MixChannel])
    case notation(midi: URL, html: URL, paged: Bool)
    case notationError(midi: URL, text: String)
    case session(name: String, path: URL?, oldPath: URL?)
    case sessions([SessionInfo])
    case claudeStatus(ok: Bool, text: String)
    case error(String)
}

/// Owns the long-lived Python process (so the models stay loaded between generations)
/// and speaks JSON lines with it.
final class Backend {
    static let projectRoot = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MUSIC_AI_ROOT"] ?? "/Users/Shared/music-ai")

    private var process = Process()
    private var stdin = Pipe()
    private var stdout = Pipe()
    private var stderr = Pipe()
    private var buffer = Data()
    /// Bumped on every (re)start so output and exit notices from an old process are ignored.
    private var generation = 0
    private let lock = NSLock()
    private var stderrTail = ""
    var onEvent: ((BackendEvent) -> Void)?

    /// Where the Python process's stderr (library warnings, tracebacks) is kept: ~/Library/Logs/Composer/backend.log
    static let logURL = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Logs/Composer/backend.log")

    /// Start the Python backend (or start a fresh one after `stop()`).
    func start() throws {
        stop()
        generation += 1
        let gen = generation
        let root = Backend.projectRoot
        let process = Process(), stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        self.process = process; self.stdin = stdin; self.stdout = stdout; self.stderr = stderr
        buffer = Data()
        lock.lock(); stderrTail = ""; lock.unlock()
        process.executableURL = root.appendingPathComponent(".venv/bin/python")
        process.arguments = ["-u", "backend.py"]
        process.currentDirectoryURL = root
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr
        stdout.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard !data.isEmpty, let self, self.generation == gen else { return }
            self.consume(data)
        }
        let logFile = Backend.openLog()
        stderr.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard !data.isEmpty, let self else { return }
            try? logFile?.write(contentsOf: data)
            let text = String(decoding: data, as: UTF8.self)
            self.lock.lock()
            self.stderrTail = String((self.stderrTail + text).suffix(2000))
            self.lock.unlock()
        }
        process.terminationHandler = { [weak self] p in
            guard let self, self.generation == gen else { return }
            // the process is gone, so whatever it wrote last may still be sitting in the pipe: read it out now
            stderr.fileHandleForReading.readabilityHandler = nil
            if let rest = try? stderr.fileHandleForReading.readToEnd(), !rest.isEmpty {
                try? logFile?.write(contentsOf: rest)
                self.lock.lock()
                self.stderrTail = String((self.stderrTail + String(decoding: rest, as: UTF8.self)).suffix(2000))
                self.lock.unlock()
            }
            self.lock.lock(); let tail = self.stderrTail; self.lock.unlock()
            let lastLines = tail.split(separator: "\n").suffix(3).joined(separator: " | ")
            self.emit(.error("Python backend exited (status \(p.terminationStatus))."
                             + (lastLines.isEmpty ? "" : " Last output: \(lastLines)")
                             + " Full log: \(Backend.logURL.path)"))
        }
        try process.run()
    }

    private static func openLog() -> FileHandle? {
        let dir = logURL.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        guard let h = try? FileHandle(forWritingTo: logURL) else { return nil }
        // keep the log from growing without bound: start over once it passes 1 MB
        if let size = try? h.seekToEnd(), size > 1_000_000 { try? h.truncate(atOffset: 0) }
        return h
    }

    /// Stop the process. Its exit is not reported as an error.
    func stop() {
        generation += 1
        stdout.fileHandleForReading.readabilityHandler = nil
        stderr.fileHandleForReading.readabilityHandler = nil
        if process.isRunning { process.terminate() }
    }

    /// Send one request object (see backend.py for the commands).
    func send(_ request: [String: Any]) throws {
        var data = try JSONSerialization.data(withJSONObject: request)
        data.append(0x0A)
        try stdin.fileHandleForWriting.write(contentsOf: data)
    }

    private func consume(_ data: Data) {
        buffer.append(data)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<nl]
            buffer = buffer[buffer.index(after: nl)...]
            if let ev = parse(Data(line)) { emit(ev) }
        }
    }

    private func emit(_ ev: BackendEvent) {
        DispatchQueue.main.async { self.onEvent?(ev) }
    }

    private static func notes(_ raw: Any?) -> [Note] {
        (raw as? [[Double]] ?? []).filter { $0.count == 4 }.map {
            Note(channel: Int($0[0]), pitch: Int($0[1]), start: $0[2], end: $0[3])
        }
    }

    private func parse(_ line: Data) -> BackendEvent? {
        guard let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
              let type = obj["type"] as? String else { return nil }
        switch type {
        case "ready": return .ready((obj["sliders"] as? [String: Double]) ?? [:])
        case "claude_status": return .claudeStatus(ok: obj["ok"] as? Bool ?? false, text: obj["text"] as? String ?? "")
        case "log": return .log(obj["text"] as? String ?? "")
        case "error": return .error(obj["text"] as? String ?? "Unknown error")
        case "melodies_done": return .melodiesDone
        case "parts_done": return .partsDone
        case "edit_suggestion":
            return .editSuggestion(bars: obj["bars"] as? [Int] ?? [], reason: obj["reason"] as? String ?? "",
                                   targets: obj["targets"] as? [Int] ?? [])
        case "part_candidate":
            return .partCandidate(PartCandidate(
                index: obj["index"] as? Int ?? 0,
                url: URL(fileURLWithPath: obj["path"] as? String ?? ""),
                program: obj["program"] as? Int ?? 0,
                channel: obj["channel"] as? Int ?? -1,
                added: obj["added"] as? Int ?? 0,
                label: obj["label"] as? String ?? "",
                notes: Backend.notes(obj["notes"]),
                programs: obj["programs"] as? [Int] ?? []))
        case "part_approved":
            return .partApproved(url: URL(fileURLWithPath: obj["path"] as? String ?? ""),
                                 notes: Backend.notes(obj["notes"]), programs: obj["programs"] as? [Int] ?? [])
        case "session":
            let path = obj["path"] as? String ?? "", old = obj["old_path"] as? String ?? ""
            return .session(name: obj["name"] as? String ?? "",
                            path: path.isEmpty ? nil : URL(fileURLWithPath: path),
                            oldPath: old.isEmpty ? nil : URL(fileURLWithPath: old))
        case "sessions":
            let items = (obj["items"] as? [[String: Any]] ?? []).map {
                SessionInfo(name: $0["name"] as? String ?? "", path: URL(fileURLWithPath: $0["path"] as? String ?? ""),
                            created: $0["created"] as? String ?? "", files: $0["files"] as? Int ?? 0)
            }
            return .sessions(items)
        case "mix":
            let chans = (obj["channels"] as? [[String: Any]] ?? []).map {
                MixChannel(channel: $0["channel"] as? Int ?? 0, name: $0["name"] as? String ?? "",
                           volume: $0["volume"] as? Int ?? 100, notes: $0["notes"] as? Int ?? 0)
            }
            return .mix(path: URL(fileURLWithPath: obj["path"] as? String ?? ""), channels: chans)
        case "notation":
            return .notation(midi: URL(fileURLWithPath: obj["midi"] as? String ?? ""),
                             html: URL(fileURLWithPath: obj["html"] as? String ?? ""),
                             paged: obj["paged"] as? Bool ?? false)
        case "notation_error":
            return .notationError(midi: URL(fileURLWithPath: obj["midi"] as? String ?? ""),
                                  text: obj["text"] as? String ?? "Could not engrave this piece")
        case "candidate":
            return .candidate(Candidate(
                index: obj["index"] as? Int ?? 0,
                url: URL(fileURLWithPath: obj["path"] as? String ?? ""),
                tempo: obj["tempo"] as? Int ?? 120,
                sig: obj["sig"] as? String ?? "4/4",
                score: obj["score"] as? Double,
                notes: Backend.notes(obj["notes"]),
                programs: obj["programs"] as? [Int] ?? []))
        case "done":
            let melody = URL(fileURLWithPath: obj["melody"] as? String ?? "")
            let full = (obj["full"] as? String).map { URL(fileURLWithPath: $0) }
            return .done(melody: melody, full: full, notes: Backend.notes(obj["notes"]),
                         programs: obj["programs"] as? [Int] ?? [])
        default: return nil
        }
    }
}
