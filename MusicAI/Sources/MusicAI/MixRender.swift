import AVFoundation
import AudioToolbox

/// Renders a MIDI file with the same Apple sound bank the app plays it with, and mixes it with a sung voice into one
/// audio file. Everything is offline (faster than real time).
enum MixRender {
    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static let rate = 44100.0
    static let block: AVAudioFrameCount = 4096

    /// One stem as left/right samples: the MIDI through the sound bank, or the voice file, alone.
    private static func stem(midi: URL?, voice: URL?, bank: URL) throws -> [[Float]] {
        let engine = AVAudioEngine()
        let fmt = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 2)!
        try engine.enableManualRenderingMode(.offline, format: fmt, maximumFrameCount: block)
        var seconds = 0.0
        var sequencer: AVAudioSequencer?
        var player: AVAudioPlayerNode?
        var file: AVAudioFile?

        if let midi {
            let desc = AudioComponentDescription(componentType: kAudioUnitType_MusicDevice,
                                                 componentSubType: kAudioUnitSubType_MIDISynth,
                                                 componentManufacturer: kAudioUnitManufacturer_Apple,
                                                 componentFlags: 0, componentFlagsMask: 0)
            let synth = AVAudioUnitMIDIInstrument(audioComponentDescription: desc)
            engine.attach(synth)
            engine.connect(synth, to: engine.mainMixerNode, format: nil)
            var bankURL = bank as CFURL
            var status = AudioUnitSetProperty(synth.audioUnit, AudioUnitPropertyID(kMusicDeviceProperty_SoundBankURL),
                                              kAudioUnitScope_Global, 0, &bankURL, UInt32(MemoryLayout<CFURL>.size))
            guard status == noErr else { throw Failure(message: "The sound bank would not load (\(status)).") }
            // load every instrument the file uses before it plays (offline, nothing waits for a late load)
            var on: UInt32 = 1
            AudioUnitSetProperty(synth.audioUnit, AudioUnitPropertyID(kAUMIDISynthProperty_EnablePreload),
                                 kAudioUnitScope_Global, 0, &on, UInt32(MemoryLayout<UInt32>.size))
            for (channel, program) in try programs(in: midi) where channel != 9 {
                synth.sendProgramChange(program, onChannel: channel)
            }
            var off: UInt32 = 0
            status = AudioUnitSetProperty(synth.audioUnit, AudioUnitPropertyID(kAUMIDISynthProperty_EnablePreload),
                                          kAudioUnitScope_Global, 0, &off, UInt32(MemoryLayout<UInt32>.size))
            let seq = AVAudioSequencer(audioEngine: engine)
            try seq.load(from: midi, options: [])
            for track in seq.tracks { track.destinationAudioUnit = synth; seconds = max(seconds, track.lengthInSeconds) }
            sequencer = seq
            seconds += 2.5      // the release of the last notes
        }
        if let voice {
            let f = try AVAudioFile(forReading: voice)
            let p = AVAudioPlayerNode()
            engine.attach(p)
            engine.connect(p, to: engine.mainMixerNode, format: f.processingFormat)
            file = f
            player = p
            seconds = max(seconds, Double(f.length) / f.processingFormat.sampleRate + 0.5)
        }

        engine.prepare()
        try engine.start()
        if let player, let file {
            player.scheduleFile(file, at: nil)
            player.play()
        }
        if let sequencer {
            sequencer.prepareToPlay()
            try sequencer.start()
        }
        let total = AVAudioFramePosition(seconds * rate)
        let buffer = AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: block)!
        var left = [Float](), right = [Float]()
        left.reserveCapacity(Int(total)); right.reserveCapacity(Int(total))
        while engine.manualRenderingSampleTime < total {
            let n = AVAudioFrameCount(min(AVAudioFramePosition(block), total - engine.manualRenderingSampleTime))
            let result = try engine.renderOffline(n, to: buffer)
            guard result == .success || result == .insufficientDataFromInputNode else {
                throw Failure(message: "Audio rendering stopped early.")
            }
            let count = Int(buffer.frameLength)
            let ch = buffer.floatChannelData!
            left.append(contentsOf: UnsafeBufferPointer(start: ch[0], count: count))
            right.append(contentsOf: UnsafeBufferPointer(start: ch[1], count: count))
        }
        sequencer?.stop()
        player?.stop()
        engine.stop()
        return [left, right]
    }

    /// (channel, program) of every program change in a standard MIDI file.
    private static func programs(in url: URL) throws -> [(UInt8, UInt8)] {
        let d = [UInt8](try Data(contentsOf: url))
        guard d.count > 14, d[0] == 0x4D, d[1] == 0x54, d[2] == 0x68, d[3] == 0x64 else { throw Failure(message: "Not a MIDI file.") }
        var out: [(UInt8, UInt8)] = []
        var pos = 8 + (Int(d[4]) << 24 | Int(d[5]) << 16 | Int(d[6]) << 8 | Int(d[7]))
        while pos + 8 <= d.count {
            let len = Int(d[pos + 4]) << 24 | Int(d[pos + 5]) << 16 | Int(d[pos + 6]) << 8 | Int(d[pos + 7])
            var i = pos + 8
            let end = min(pos + 8 + len, d.count)
            var running: UInt8 = 0
            while i < end {
                while i < end, d[i] & 0x80 != 0 { i += 1 }      // delta time
                i += 1
                guard i < end else { break }
                var status = d[i]
                if status & 0x80 != 0 { i += 1; if status < 0xF0 { running = status } } else { status = running }
                switch status {
                case 0xFF:
                    guard i < end else { break }
                    i += 1
                    var l = 0
                    while i < end { let b = d[i]; i += 1; l = l << 7 | Int(b & 0x7F); if b & 0x80 == 0 { break } }
                    i += l
                case 0xF0, 0xF7:
                    var l = 0
                    while i < end { let b = d[i]; i += 1; l = l << 7 | Int(b & 0x7F); if b & 0x80 == 0 { break } }
                    i += l
                case 0xC0...0xCF:
                    if i < end { out.append((status & 0x0F, d[i])) }
                    i += 1
                case 0xD0...0xDF: i += 1
                default: i += 2
                }
            }
            pos = end
        }
        return out
    }

    private static func rms(_ s: [[Float]]) -> Float {
        var sum = 0.0, n = 0.0
        for ch in s { for x in ch where abs(x) > 0.001 { sum += Double(x * x); n += 1 } }   // the sounding part only
        return n > 0 ? Float((sum / n).squareRoot()) : 0
    }

    /// The voice with the MIDI behind it (a quarter as loud, in RMS terms, as the voice's own sounding parts), written as
    /// a 16-bit stereo WAV, the loudest point at 90 %.
    static func mix(midi: URL, voice: URL, bank: URL, to out: URL) throws {
        let back = try stem(midi: midi, voice: nil, bank: bank)
        let vox = try stem(midi: nil, voice: voice, bank: bank)
        let gain = rms(back) > 0 ? 0.5 * rms(vox) / rms(back) : 0
        let n = max(back[0].count, vox[0].count)
        var mixed = [[Float]](repeating: [Float](repeating: 0, count: n), count: 2)
        var peak: Float = 0
        for c in 0..<2 {
            for i in 0..<n {
                let v = (i < vox[c].count ? vox[c][i] : 0) + gain * (i < back[c].count ? back[c][i] : 0)
                mixed[c][i] = v
                peak = max(peak, abs(v))
            }
        }
        guard peak > 0.0005 else { throw Failure(message: "The rendered mix is silent.") }
        let scale = 0.9 / peak
        let fmt = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 2)!
        let file = try AVAudioFile(forWriting: out, settings: [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: rate,
                                                               AVNumberOfChannelsKey: 2, AVLinearPCMBitDepthKey: 16,
                                                               AVLinearPCMIsFloatKey: false],
                                   commonFormat: .pcmFormatFloat32, interleaved: false)
        var at = 0
        while at < n {
            let m = min(Int(block), n - at)
            let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(m))!
            buf.frameLength = AVAudioFrameCount(m)
            for c in 0..<2 { for i in 0..<m { buf.floatChannelData![c][i] = mixed[c][at + i] * scale } }
            try file.write(from: buf)
            at += m
        }
    }
}
