import Foundation

/// Which instrument plays on each MIDI channel of a file: the first program change of each channel
/// (drums, channel 10, are program 128). The plugin in Logic uses it to name and pick instruments.
enum MidiPrograms {
    static func of(_ url: URL) -> [Int: Int] {
        guard let d = try? Data(contentsOf: url), d.count > 14, String(decoding: d[0..<4], as: UTF8.self) == "MThd" else { return [:] }
        var found: [Int: Int] = [:], i = 8 + Int(d[7])      // after the header chunk
        func vlq() -> Int { var v = 0; while i < d.count { let b = Int(d[i]); i += 1; v = v << 7 | b & 0x7F; if b < 0x80 { break } }; return v }
        while i + 8 <= d.count {
            let length = Int(d[i + 4]) << 24 | Int(d[i + 5]) << 16 | Int(d[i + 6]) << 8 | Int(d[i + 7])
            let isTrack = String(decoding: d[i..<i + 4], as: UTF8.self) == "MTrk"
            i += 8
            let end = min(i + length, d.count)
            guard isTrack else { i = end; continue }
            var status = 0
            while i < end {
                _ = vlq()
                guard i < end else { break }
                if d[i] >= 0x80 { status = Int(d[i]); i += 1 }
                if status == 0xFF { i += 1; i += vlq() }                           // meta: type, length, data
                else if status == 0xF0 || status == 0xF7 { i += vlq() }            // sysex
                else if status >> 4 == 0xC || status >> 4 == 0xD {
                    if status >> 4 == 0xC, i < end, found[status & 15] == nil { found[status & 15] = Int(d[i]) }
                    i += 1
                } else { i += 2 }
            }
            i = end
        }
        found[9] = 128
        return found
    }
}
