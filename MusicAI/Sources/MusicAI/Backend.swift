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

/// A saved session folder.
struct SessionInfo: Identifiable {
    let name: String
    let path: URL
    let created: String
    let files: Int
    var id: URL { path }
}

enum BackendEvent {
    case ready
    case log(String)
    case candidate(Candidate)
    case melodiesDone
    case done(melody: URL, full: URL?, notes: [Note], programs: [Int])
    case partCandidate(PartCandidate)
    case partsDone
    case partApproved(url: URL, notes: [Note], programs: [Int])
    case notation(midi: URL, html: URL, paged: Bool)
    case notationError(midi: URL, text: String)
    case session(name: String, path: URL?, oldPath: URL?)
    case sessions([SessionInfo])
    case error(String)
}

/// Owns the long-lived Python process (so the models stay loaded between generations)
/// and speaks JSON lines with it.
final class Backend {
    static let projectRoot = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MUSIC_AI_ROOT"] ?? "/Users/Shared/music-ai")

    private let process = Process()
    private let stdin = Pipe()
    private let stdout = Pipe()
    private var buffer = Data()
    var onEvent: ((BackendEvent) -> Void)?

    func start() throws {
        let root = Backend.projectRoot
        process.executableURL = root.appendingPathComponent(".venv/bin/python")
        process.arguments = ["-u", "backend.py"]
        process.currentDirectoryURL = root
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = FileHandle.nullDevice
        stdout.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard !data.isEmpty else { return }
            self?.consume(data)
        }
        process.terminationHandler = { [weak self] p in
            self?.emit(.error("Python backend exited (status \(p.terminationStatus))."))
        }
        try process.run()
    }

    func stop() {
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
        case "ready": return .ready
        case "log": return .log(obj["text"] as? String ?? "")
        case "error": return .error(obj["text"] as? String ?? "Unknown error")
        case "melodies_done": return .melodiesDone
        case "parts_done": return .partsDone
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
