import Foundation

/// A standard MIDI file (type 1) from notes: a tempo track, then one track per channel, so Logic makes one track
/// for each instrument of the piece. The plugin writes this itself, into its own folder, because a sandboxed
/// plugin can only hand Logic a file it can read.
enum MidiWriter {
    static func data(notes: [PlayNote], tempo: Int, names: [UInt8: String] = [:]) -> Data {
        let ppq = 480
        var out = Data("MThd".utf8)
        out += be32(6) + be16(1) + be16(UInt16(1 + Set(notes.map(\.channel)).count)) + be16(UInt16(ppq))
        let micros = 60_000_000 / max(tempo, 1)
        out += track([(0, [0xFF, 0x51, 0x03, UInt8(micros >> 16 & 255), UInt8(micros >> 8 & 255), UInt8(micros & 255)]),
                      (0, [0xFF, 0x58, 0x04, 4, 2, 24, 8])])
        for ch in Set(notes.map(\.channel)).sorted() {
            var events: [(Int, Int, [UInt8])] = []          // tick, order (offs before ons), bytes
            if let name = names[ch] { events.append((0, -1, [0xFF, 0x03, UInt8(min(name.utf8.count, 127))] + Array(name.utf8.prefix(127)))) }   // the track's name
            for n in notes where n.channel == ch {
                let on = Int((n.start * Double(ppq)).rounded())
                let off = max(on + 1, Int(((n.start + n.length) * Double(ppq)).rounded()))
                events.append((on, 1, [0x90 | ch, n.pitch, n.velocity]))
                events.append((off, 0, [0x80 | ch, n.pitch, 0]))
            }
            events.sort { ($0.0, $0.1) < ($1.0, $1.1) }
            var last = 0
            out += track(events.map { e in defer { last = e.0 }; return (e.0 - last, e.2) })
        }
        return out
    }

    private static func track(_ events: [(Int, [UInt8])]) -> Data {
        var body = Data()
        for (delta, bytes) in events { body += vlq(delta) + Data(bytes) }
        body += vlq(0) + Data([0xFF, 0x2F, 0x00])
        return Data("MTrk".utf8) + be32(UInt32(body.count)) + body
    }

    private static func vlq(_ v: Int) -> Data {
        var value = v, bytes = [UInt8(value & 0x7F)]
        value >>= 7
        while value > 0 { bytes.insert(UInt8(value & 0x7F) | 0x80, at: 0); value >>= 7 }
        return Data(bytes)
    }
    private static func be32(_ v: UInt32) -> Data { Data([UInt8(v >> 24), UInt8(v >> 16 & 255), UInt8(v >> 8 & 255), UInt8(v & 255)]) }
    private static func be16(_ v: UInt16) -> Data { Data([UInt8(v >> 8), UInt8(v & 255)]) }
}
