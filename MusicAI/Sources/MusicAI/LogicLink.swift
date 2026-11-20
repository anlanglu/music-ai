import Foundation
import Network

/// Hands the option being looked at to the Composer plugin in Logic Pro. The plugin runs in Logic's process and
/// cannot read this app's files, so it connects here (127.0.0.1 only) and is sent one JSON line per change:
/// {"name": …, "tempo": …, "notes": [[channel, pitch, startBeat, lengthBeats], …],
/// "programs": {"channel": General MIDI program}}
final class LogicLink {
    static let port = NWEndpoint.Port(rawValue: 47321)!
    private var listener: NWListener?
    private var clients: [NWConnection] = []
    private var last = Data()

    init() {
        let params = NWParameters.tcp
        params.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: Self.port)     // this Mac only
        guard let l = try? NWListener(using: params) else { return }
        l.newConnectionHandler = { [weak self] c in self?.accept(c) }
        l.start(queue: .main)
        listener = l
    }

    private func accept(_ c: NWConnection) {
        c.stateUpdateHandler = { [weak self, weak c] state in
            guard let self, let c else { return }
            switch state {
            case .ready: if !self.last.isEmpty { c.send(content: self.last, completion: .idempotent) }
            case .failed, .cancelled: self.clients.removeAll { $0 === c }
            default: break
            }
        }
        clients.append(c)
        c.start(queue: .main)
    }

    func publish(name: String, tempo: Int, notes: [Note], programs: [Int: Int]) {
        let perSecond = Double(tempo) / 60
        let rows = notes.map { [Double($0.channel), Double($0.pitch), $0.start * perSecond, ($0.end - $0.start) * perSecond] }
        let body: [String: Any] = ["name": name, "tempo": tempo, "notes": rows,
                                   "programs": Dictionary(uniqueKeysWithValues: programs.map { (String($0.key), $0.value) })]
        guard var data = try? JSONSerialization.data(withJSONObject: body) else { return }
        data.append(10)
        last = data
        for c in clients { c.send(content: data, completion: .idempotent) }
    }
}
