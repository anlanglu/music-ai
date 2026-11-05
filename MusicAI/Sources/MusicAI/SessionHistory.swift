import Foundation

/// A session's history, kept with the session (history.json, written by the backend) so that opening it brings back every
/// step, the options each one offered and the settings behind it. Options the user discards are hidden for good (the files stay).
extension ViewModel {
    // MARK: Writing

    private func candidateJSON(_ c: Candidate) -> [String: Any] {
        ["path": c.url.path, "index": c.index, "score": c.score as Any? ?? NSNull()]
    }

    private func partJSON(_ p: PartCandidate) -> [String: Any] {
        ["path": p.url.path, "index": p.index, "program": p.program, "channel": p.channel, "added": p.added, "label": p.label]
    }

    private func snapshotJSON(_ s: Snapshot) -> [String: Any] {
        [
            "candidates": s.candidates.map(candidateJSON),
            "selected": s.candidates.first { $0.id == s.selectedID }?.url.path as Any? ?? NSNull(),
            "parts": s.partOptions.map(partJSON),
            "selectedPart": s.partOptions.first { $0.id == s.selectedPartID }?.url.path as Any? ?? NSNull(),
            "partHistory": s.partHistory.map { ["before": candidateJSON($0.before), "label": $0.label] as [String: Any] },
            "expansion": s.expansionHistory.map { step -> [String: Any] in
                ["candidates": step.candidates.map(candidateJSON),
                 "selected": step.candidates.first { $0.id == step.selectedID }?.url.path as Any? ?? NSNull()]
            },
        ]
    }

    /// Writes the history to the session folder. Cheap, and done after every step, choice and discard.
    func saveHistory() {
        guard ready, sessionURL != nil, !history.isEmpty else { return }
        let doc: [String: Any] = [
            "current": currentEntryID?.uuidString ?? "",
            "discarded": Array(discarded),
            "entries": history.map { e -> [String: Any] in
                ["id": e.id.uuidString, "title": e.title, "detail": e.detail, "time": e.time.timeIntervalSince1970,
                 "panel": e.panel, "snapshot": snapshotJSON(e.snapshot)]
            },
        ]
        send(["cmd": "history_save", "data": doc])
    }

    // MARK: Reading

    private func candidate(_ j: [String: Any], _ files: [String: [String: Any]]) -> Candidate? {
        guard let path = j["path"] as? String, !discarded.contains(path), let f = files[path] else { return nil }
        return Candidate(index: j["index"] as? Int ?? 1, url: URL(fileURLWithPath: path), tempo: f["tempo"] as? Int ?? 120,
                         sig: f["sig"] as? String ?? "4/4", score: j["score"] as? Double, notes: Backend.notes(f["notes"]),
                         programs: f["programs"] as? [Int] ?? [], layers: Backend.layers(f["layers"]))
    }

    private func part(_ j: [String: Any], _ files: [String: [String: Any]]) -> PartCandidate? {
        guard let path = j["path"] as? String, !discarded.contains(path), let f = files[path] else { return nil }
        return PartCandidate(index: j["index"] as? Int ?? 1, url: URL(fileURLWithPath: path), program: j["program"] as? Int ?? 0,
                             channel: j["channel"] as? Int ?? -1, added: j["added"] as? Int ?? 0, label: j["label"] as? String ?? "",
                             notes: Backend.notes(f["notes"]), programs: f["programs"] as? [Int] ?? [])
    }

    /// Puts a saved session's history back (the backend sends it when a session is opened) and shows its latest step.
    func restoreHistory(_ data: [String: Any], _ files: [String: [String: Any]]) {
        restoringHistory = true
        discarded = Set(data["discarded"] as? [String] ?? [])
        var ids: [String: UUID] = [:]
        var entries: [HistoryEntry] = []
        for raw in data["entries"] as? [[String: Any]] ?? [] {
            let key = raw["id"] as? String ?? UUID().uuidString
            let id = UUID(uuidString: key) ?? UUID()
            ids[key] = id
            let s = raw["snapshot"] as? [String: Any] ?? [:]
            let cands = (s["candidates"] as? [[String: Any]] ?? []).compactMap { candidate($0, files) }
            let parts = (s["parts"] as? [[String: Any]] ?? []).compactMap { part($0, files) }
            let partSteps = (s["partHistory"] as? [[String: Any]] ?? []).compactMap { h -> PartStep? in
                guard let b = h["before"] as? [String: Any], let c = candidate(b, files) else { return nil }
                return PartStep(before: c, label: h["label"] as? String ?? "")
            }
            let expansion = (s["expansion"] as? [[String: Any]] ?? []).map { h -> (candidates: [Candidate], selectedID: UUID?) in
                let cs = (h["candidates"] as? [[String: Any]] ?? []).compactMap { candidate($0, files) }
                let sel = h["selected"] as? String
                return (cs, cs.first { $0.url.path == sel }?.id)
            }
            let snap = Snapshot(candidates: cands, selectedID: cands.first { $0.url.path == s["selected"] as? String }?.id,
                                partOptions: parts, selectedPartID: parts.first { $0.url.path == s["selectedPart"] as? String }?.id,
                                partHistory: partSteps, expansionHistory: expansion)
            var e = HistoryEntry(title: raw["title"] as? String ?? "Step", detail: raw["detail"] as? String ?? "",
                                 time: Date(timeIntervalSince1970: raw["time"] as? Double ?? 0), snapshot: snap,
                                 panel: raw["panel"] as? [String: Any] ?? [:])
            e.id = id
            if !(cands.isEmpty && parts.isEmpty) { entries.append(e) }       // a step whose files are all gone is dropped
        }
        history = entries
        guard let last = entries.first(where: { $0.id == ids[data["current"] as? String ?? ""] }) ?? entries.last else {
            status = "Opened. This session has no steps to show."
            return
        }
        showEntry(last)
        let rebuilt = data["rebuilt"] as? Bool ?? false
        status = "Opened \(sessionName): \(entries.count) step\(entries.count == 1 ? "" : "s") in History."
            + (rebuilt ? " This session was saved before settings were kept, so the settings of its steps are not available." : " Click a step to go back to it.")
    }

    /// Shows the options of a history entry (without the busy checks of `goBack`).
    private func showEntry(_ entry: HistoryEntry) {
        let s = entry.snapshot
        candidates = s.candidates
        selectedID = s.selectedID ?? s.candidates.first?.id
        partOptions = s.partOptions
        selectedPartID = s.selectedPartID
        partHistory = s.partHistory
        expansionHistory = s.expansionHistory
        expandJustRan = false
        currentEntryID = entry.id
        applyPanel(entry.panel)
        ensureSheet()
        loadChords()
    }

    // MARK: Discarding

    /// Throws an option away: it disappears from the list and from every step of the history. The file stays on disk.
    func discard(_ c: Candidate) {
        guard !busy else { return }
        if playingURL == c.url { stop() }
        discarded.insert(c.url.path)
        func keep(_ x: Candidate) -> Bool { x.url != c.url }
        candidates.removeAll { !keep($0) }
        if selectedID == c.id { selectedID = nil }
        expansionHistory = expansionHistory.map { (candidates: $0.candidates.filter(keep), selectedID: $0.selectedID) }
        history = history.map { e in
            var e = e
            e.snapshot.candidates.removeAll { !keep($0) }
            e.snapshot.expansionHistory = e.snapshot.expansionHistory.map { (candidates: $0.candidates.filter(keep), selectedID: $0.selectedID) }
            if e.snapshot.selectedID == c.id { e.snapshot.selectedID = nil }
            return e
        }
        history.removeAll { $0.snapshot.candidates.isEmpty && $0.snapshot.partOptions.isEmpty }
        if let id = currentEntryID, !history.contains(where: { $0.id == id }) { currentEntryID = history.last?.id }
        status = "Option \(c.index) discarded (the file stays in the session folder)."
        saveHistory()
    }

    func discardPart(_ p: PartCandidate) {
        guard !busy else { return }
        if playingURL == p.url { stop() }
        discarded.insert(p.url.path)
        partOptions.removeAll { $0.url == p.url }
        if selectedPartID == p.id { selectedPartID = nil }
        history = history.map { e in
            var e = e
            e.snapshot.partOptions.removeAll { $0.url == p.url }
            return e
        }
        status = "Option \(p.index) discarded (the file stays in the session folder)."
        saveHistory()
    }

    // MARK: Settings

    /// The settings in the panels, as plain values, kept with each step.
    func panelState() -> [String: Any] {
        func singers(_ s: Singers) -> [String: Any] { ["lead": s.lead, "extras": Array(s.extras), "harmonize": s.harmonize] }
        return [
            "step": step, "prompt": prompt, "moods": Array(moods), "bars": bars, "motifProgression": motifProgression,
            "motifModes": motifModes, "instrument": instrument.id, "sig": sig, "bpmText": bpmText,
            "key": keyChoice?.tonic ?? -1, "keyMode": keyMode, "resolveEnding": resolveEnding, "phrasing": phrasing,
            "shape": shapeSlider, "pulse": pulseSlider, "strict": strictSlider, "dynamics": dynamicsSlider, "flavor": flavorSlider,
            "complexity": complexity, "optionCount": optionCount, "llm": llm,
            "expandBars": expandBars, "expandComplexity": expandComplexity, "expandCount": expandCount,
            "expandDirection": expandDirection, "expandMoves": Array(expandMoves), "expandInstrument": expandInstrument.id,
            "expandSingers": singers(expandSingers), "expandProgression": expandProgression, "expandModes": expandModes,
            "expandShape": expandShape, "expandTheme": expandTheme, "expandDynamics": expandDynamics,
            "expandResolve": expandResolve, "expandPhrasing": expandPhrasing,
            "editInstruction": editInstruction, "editBarsText": editBarsText, "editCount": editCount, "editPart": editPart,
            "singSingers": singers(singSingers), "singStart": singStart, "singBars": singBars, "singCount": singCount,
            "ensemble": ensemble, "partDirection": partDirection, "partInstrument": partInstrument.id,
            "partOptionCount": partOptionCount, "accompDynamics": accompDynamics, "pianoStyle": pianoStyle,
            "lyrics": lyrics, "lineBars": lineBars, "singers": singers(self.singers), "playFromBar": playFromBar,
        ]
    }

    /// Puts settings from `panelState` back. Anything missing keeps its value.
    func applyPanel(_ p: [String: Any]) {
        func num(_ k: String) -> Double? { (p[k] as? NSNumber)?.doubleValue }
        func text(_ k: String) -> String? { p[k] as? String }
        func flag(_ k: String) -> Bool? { p[k] as? Bool }
        func singers(_ k: String) -> Singers? {
            guard let d = p[k] as? [String: Any] else { return nil }
            return Singers(lead: d["lead"] as? String ?? "en", extras: Set(d["extras"] as? [String] ?? []), harmonize: d["harmonize"] as? Bool ?? false)
        }
        if let v = num("step") { step = Int(v) }
        if let v = text("prompt") { prompt = v }
        if let v = p["moods"] as? [String] { moods = Set(v) }
        if let v = num("bars") { bars = v }
        if let v = text("motifProgression") { motifProgression = v }
        if let v = text("motifModes") { motifModes = v }
        if let v = num("instrument"), let i = (Instrument.all + Instrument.extend).first(where: { $0.id == Int(v) }) { instrument = i }
        if let v = text("sig") { sig = v }
        if let v = text("bpmText") { bpmText = v }
        if let v = num("key") { keyChoice = KeyChoice.all.first { $0.tonic == Int(v) } }
        if let v = text("keyMode") { keyMode = v }
        if let v = flag("resolveEnding") { resolveEnding = v }
        if let v = num("phrasing") { phrasing = Int(v) }
        if let v = num("shape") { shapeSlider = v }
        if let v = num("pulse") { pulseSlider = v }
        if let v = num("strict") { strictSlider = v }
        if let v = num("dynamics") { dynamicsSlider = v }
        if let v = num("flavor") { flavorSlider = v }
        if let v = num("complexity") { complexity = v }
        if let v = num("optionCount") { optionCount = v }
        if let v = text("llm") { llm = v }
        if let v = num("expandBars") { expandBars = v }
        if let v = num("expandComplexity") { expandComplexity = v }
        if let v = num("expandCount") { expandCount = v }
        if let v = text("expandDirection") { expandDirection = v }
        if let v = p["expandMoves"] as? [String] { expandMoves = Set(v) }
        if let v = num("expandInstrument"), let i = (Instrument.all + Instrument.extend).first(where: { $0.id == Int(v) }) { expandInstrument = i }
        if let v = singers("expandSingers") { expandSingers = v }
        if let v = text("expandProgression") { expandProgression = v }
        if let v = text("expandModes") { expandModes = v }
        if let v = num("expandShape") { expandShape = v }
        if let v = num("expandTheme") { expandTheme = v }
        if let v = num("expandDynamics") { expandDynamics = v }
        if let v = flag("expandResolve") { expandResolve = v }
        if let v = num("expandPhrasing") { expandPhrasing = Int(v) }
        if let v = text("editInstruction") { editInstruction = v }
        if let v = text("editBarsText") { editBarsText = v }
        if let v = num("editCount") { editCount = v }
        if let v = text("editPart") { editPart = v }
        if let v = singers("singSingers") { singSingers = v }
        if let v = num("singStart") { singStart = v }
        if let v = num("singBars") { singBars = v }
        if let v = num("singCount") { singCount = v }
        if let v = num("ensemble") { ensemble = v }
        if let v = text("partDirection") { partDirection = v }
        if let v = num("partInstrument"), let i = Instrument.parts.first(where: { $0.id == Int(v) }) { partInstrument = i }
        if let v = num("partOptionCount") { partOptionCount = v }
        if let v = num("accompDynamics") { accompDynamics = v }
        if let v = text("pianoStyle") { pianoStyle = v }
        if let v = text("lyrics") { lyrics = v }
        if let v = num("lineBars") { lineBars = Int(v) }
        if let v = singers("singers") { self.singers = v }
        if let v = num("playFromBar") { playFromBar = Int(v) }
    }
}
