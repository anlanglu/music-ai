import AudioToolbox
import AVFoundation
import CoreAudioKit
import Network
import SwiftUI

/// One note to play, in beats from the start of the piece.
struct PlayNote {
    var start: Double, length: Double
    var pitch: UInt8, velocity: UInt8, channel: UInt8
}

/// The notes the plugin plays. Replaced as a whole, so the render thread only ever reads a finished list.
final class Score {
    let notes: [PlayNote]
    init(_ notes: [PlayNote]) { self.notes = notes.sorted { $0.start < $1.start } }
    static let empty = Score([])
}

/// A MIDI effect: it sends the chosen option's notes to the instrument on its track, in step with Logic's transport.
final class ComposerAudioUnit: AUAudioUnit {
    private var lock = os_unfair_lock()
    private var shared = Score.empty
    private var score = Score.empty           // the render thread's own reference
    var startBar = 1                          // the bar of the project where the piece starts
    private var barBeats = 4.0                // quarter notes per bar, as last reported by the host

    /// Called from any thread; the render thread picks the new score up at its next block.
    func load(_ s: Score) {
        os_unfair_lock_lock(&lock); shared = s; os_unfair_lock_unlock(&lock)
    }
    private var held: [(pitch: UInt8, channel: UInt8)] = []
    private var lastEnd = -1.0
    private var wasPlaying = false
    private var perFrame = 0.0, lastBeat = 0.0, lastSample = 0.0

    // The audio busses do nothing (a MIDI effect makes no sound); hosts and the validator expect a pair.
    private lazy var format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 2)!
    private lazy var inBusses = AUAudioUnitBusArray(audioUnit: self, busType: .input, busses: [try! AUAudioUnitBus(format: format)])
    private lazy var outBusses = AUAudioUnitBusArray(audioUnit: self, busType: .output, busses: [try! AUAudioUnitBus(format: format)])
    override var inputBusses: AUAudioUnitBusArray { inBusses }
    override var outputBusses: AUAudioUnitBusArray { outBusses }
    override var midiOutputNames: [String] { ["Composer"] }
    override var providesUserInterface: Bool { true }

    override var internalRenderBlock: AUInternalRenderBlock {
        { [unowned self] _, timestamp, frames, _, _, _, _ in
            self.render(timestamp: timestamp.pointee, frames: Int(frames))
            return noErr
        }
    }

    private func send(_ out: AUMIDIOutputEventBlock, _ time: AUEventSampleTime, _ bytes: [UInt8]) {
        bytes.withUnsafeBufferPointer { _ = out(time, 0, bytes.count, $0.baseAddress!) }
    }

    private func release(_ out: AUMIDIOutputEventBlock, _ time: AUEventSampleTime) {
        for h in held { send(out, time, [0x80 | h.channel, h.pitch, 0]) }
        held.removeAll()
    }

    private func render(timestamp: AudioTimeStamp, frames: Int) {
        guard let out = midiOutputEventBlock else { return }
        let now = AUEventSampleTime(timestamp.mSampleTime)
        var flags = AUHostTransportStateFlags()
        var tempo = 120.0, beat = 0.0
        var num = 4.0, den = 4
        musicalContextBlock?(&tempo, &num, &den, &beat, nil, nil)
        barBeats = num * 4 / Double(max(den, 1))
        let originBeat = Double(startBar - 1) * barBeats
        if os_unfair_lock_trylock(&lock) { score = shared; os_unfair_lock_unlock(&lock) }
        transportStateBlock?(&flags, nil, nil, nil)
        let playing = flags.contains(.moving)
        guard playing else {
            if wasPlaying { release(out, now); wasPlaying = false }
            lastEnd = -1
            return
        }
        // beats per frame: measured from the playhead between blocks (an effect with no audio has no sample rate to ask)
        if lastEnd >= 0, beat > lastBeat, timestamp.mSampleTime > lastSample {
            perFrame = (beat - lastBeat) / (timestamp.mSampleTime - lastSample)
        } else if perFrame == 0 { perFrame = tempo / 60 / 48000 }
        lastBeat = beat; lastSample = timestamp.mSampleTime
        let beat0 = beat - originBeat, beat1 = beat0 + Double(frames) * perFrame
        if !wasPlaying || abs(beat0 - lastEnd) > 0.05 { release(out, now) }          // started, or the playhead jumped
        wasPlaying = true
        lastEnd = beat1
        let rate = perFrame
        let at = { (b: Double) in now + AUEventSampleTime(max(0, (b - beat0) / rate)) }
        for n in score.notes {
            if n.start >= beat1 { break }
            if n.start >= beat0 {
                send(out, at(n.start), [0x90 | n.channel, n.pitch, n.velocity])
                held.append((n.pitch, n.channel))
            }
        }
        for n in score.notes where n.start + n.length >= beat0 && n.start + n.length < beat1 && n.start < beat1 {
            send(out, at(n.start + n.length), [0x80 | n.channel, n.pitch, 0])
            if let i = held.firstIndex(where: { $0.pitch == n.pitch && $0.channel == n.channel }) { held.remove(at: i) }
        }
    }
}

/// The connection to Composer.app (127.0.0.1 only): it receives the option the app is showing.
final class ComposerLink: ObservableObject {
    @Published var status = "Looking for Composer…"
    @Published var piece = ""
    @Published var noteCount = 0
    @Published var startBar = 1
    @Published var file: URL?
    var onScore: ((Score) -> Void)?
    private var connection: NWConnection?
    private var buffer = Data()
    private var timer: Timer?

    func start() {
        connect()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            if self?.connection == nil { self?.connect() }
        }
    }

    func stop() { timer?.invalidate(); connection?.cancel(); connection = nil }

    private func connect() {
        let c = NWConnection(host: "127.0.0.1", port: 47321, using: .tcp)
        c.stateUpdateHandler = { [weak self, weak c] state in
            DispatchQueue.main.async {
                guard let self, self.connection === c else { return }
                switch state {
                case .ready: self.status = self.piece.isEmpty ? "Connected. Pick an option in Composer." : "Connected"
                case .failed, .cancelled:
                    self.connection = nil
                    self.status = "Composer is not running. Open Composer.app."
                case .waiting:
                    self.connection?.cancel()
                default: break
                }
            }
        }
        connection = c
        c.start(queue: .main)
        read(c)
    }

    private func read(_ c: NWConnection) {
        c.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { [weak self] data, _, done, error in
            guard let self, self.connection === c else { return }
            if let data { self.buffer.append(data); self.drain() }
            if done || error != nil { c.cancel(); self.connection = nil; self.status = "Composer is not running. Open Composer.app." }
            else { self.read(c) }
        }
    }

    private func drain() {
        while let nl = buffer.firstIndex(of: 10) {
            let line = buffer[buffer.startIndex..<nl]
            buffer.removeSubrange(buffer.startIndex...nl)
            guard let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                  let rows = obj["notes"] as? [[Double]] else { continue }
            let notes = rows.filter { $0.count == 4 }.map {
                PlayNote(start: $0[2], length: $0[3], pitch: UInt8(max(0, min(127, $0[1]))), velocity: 90,
                         channel: UInt8(max(0, min(15, $0[0]))))
            }
            onScore?(Score(notes))
            piece = obj["name"] as? String ?? ""
            noteCount = notes.count
            file = (obj["file"] as? String).map { URL(fileURLWithPath: $0) }
            status = "Connected"
        }
    }
}

struct PluginView: View {
    @ObservedObject var link: ComposerLink
    let au: () -> ComposerAudioUnit?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Composer").font(.title2.bold())
            VStack(alignment: .leading, spacing: 4) {
                Text(link.piece.isEmpty ? "No option yet" : link.piece).font(.headline).lineLimit(2)
                Text(link.noteCount > 0 ? "\(link.noteCount) notes · plays when Logic plays" : link.status)
                    .font(.caption).foregroundStyle(.secondary)
            }
            Stepper("Starts at bar \(link.startBar)",
                    value: Binding(get: { link.startBar }, set: { link.startBar = $0; au()?.startBar = $0 }), in: 1...999)
            if let file = link.file {
                Label("Drag onto a track to edit the notes", systemImage: "arrow.down.doc")
                    .font(.callout).padding(.horizontal, 10).padding(.vertical, 6)
                    .background(.quaternary, in: Capsule())
                    .onDrag { NSItemProvider(object: file as NSURL) }
                    .help("Drop it on a track in Logic: it becomes a MIDI region you can edit. Then bypass this plugin so it isn't played twice.")
            }
            Text(link.status).font(.caption2).foregroundStyle(.tertiary)
            Spacer()
        }
        .padding(16)
        .frame(width: 380, height: 260)
    }
}

@objc(AudioUnitViewController)
public final class AudioUnitViewController: AUViewController, AUAudioUnitFactory {
    var audioUnit: ComposerAudioUnit?
    let link = ComposerLink()

    public func createAudioUnit(with description: AudioComponentDescription) throws -> AUAudioUnit {
        let au = try ComposerAudioUnit(componentDescription: description, options: [])
        au.load(Score((0..<8).map { PlayNote(start: Double($0), length: 0.9, pitch: UInt8(60 + [0, 2, 4, 5, 7, 9, 11, 12][$0]), velocity: 90, channel: 0) }))
        link.onScore = { [weak au] in au?.load($0) }
        audioUnit = au
        return au
    }

    public override func loadView() {
        link.start()
        let host = NSHostingView(rootView: PluginView(link: link, au: { [weak self] in self?.audioUnit }))
        host.frame = NSRect(x: 0, y: 0, width: 380, height: 260)
        view = host
        preferredContentSize = host.frame.size
    }
}
