import Security
import SwiftUI

/// The Anthropic API key lives in the macOS Keychain, never in the project folder or the logs.
enum Keychain {
    private static let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "Composer",
        kSecAttrAccount as String: "anthropic-api-key",
    ]

    static func get() -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func set(_ key: String) {
        SecItemDelete(query as CFDictionary)
        var q = query
        q[kSecValueData as String] = Data(key.utf8)
        SecItemAdd(q as CFDictionary, nil)
    }

    static func remove() { SecItemDelete(query as CFDictionary) }
}

/// Asks for the API key, checks it against Anthropic, and keeps it in the Keychain.
struct ClaudeKeySheet: View {
    @ObservedObject var vm: ViewModel
    @FocusState private var focused: Bool
    @StateObject private var field = KeyField()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Use Claude as the melody LLM").font(.title3).bold()
            Text("Claude runs at Anthropic, not on this Mac: the prompt, the melody so far and your instructions are sent to the Anthropic API.")
                .font(.callout)
            Text("A Claude.ai subscription (Pro or Max) does not include API access. You need an API key from console.anthropic.com → API keys, with credit added under Billing; usage is charged per request there.")
                .font(.callout).foregroundStyle(.secondary)
            Link("Open console.anthropic.com", destination: URL(string: "https://console.anthropic.com/settings/keys")!)
            SecureField("sk-ant-…", text: $field.text)
                .textFieldStyle(.roundedBorder)
                .focused($focused)
                .onSubmit { vm.saveClaudeKey(field.text) }
            if !vm.claudeStatus.isEmpty {
                Text(vm.claudeStatus).font(.callout)
                    .foregroundStyle(vm.claudeKeyOK ? Color.green : Color.orange)
            }
            Text("The key is stored in your macOS Keychain and held in memory by the backend; it is not written to the project folder or the log.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                if vm.claudeKeySaved { Button("Remove key", role: .destructive) { vm.removeClaudeKey(); field.text = "" } }
                Spacer()
                Button("Close") { vm.showClaudeSheet = false }
                Button("Save and test") { vm.saveClaudeKey(field.text) }
                    .keyboardShortcut(.defaultAction)
                    .disabled(field.text.trimmingCharacters(in: .whitespaces).isEmpty || vm.claudeChecking)
            }
        }
        .padding(20)
        .frame(width: 460)
        .onAppear { focused = true }
    }
}

final class VoiceForm: ObservableObject {
    @Published var file: URL?
    @Published var name = ""
    @Published var language = "English"
    @Published var permission = false
}

final class KeyField: ObservableObject { @Published var text = "" }

/// The lyrics, kept apart from the melody. Nothing is composed here: with the Voice instrument, the Motif and Extend steps
/// take as many lines as fit the bars asked for and carry on with the next line each time.
struct LyricsSheet: View {
    @ObservedObject var vm: ViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Lyrics").font(.title3).bold()
            Text("Put in as many lines as you like, one line of text per musical phrase. They are only kept here. Choose the Voice instrument in the Motif step and the melody takes as many lines as fit the bars you ask for; each extension of that melody carries on with the next line, and it never has to use them all.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextEditor(text: $vm.lyrics)
                .font(.body)
                .frame(minHeight: 180)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
                .onChange(of: vm.lyrics) { _, _ in vm.lyricsInfo = "" }
            Text(vm.lyricsInfo.isEmpty ? " " : vm.lyricsInfo).font(.callout)
                .foregroundStyle(vm.lyricsInfo.isEmpty || vm.lyricsOK ? Color.secondary : Color.orange)
                .lineLimit(3)
            Divider()
            Text("Singing (demo): a local voice model (SoulX-Singer, a stock reference voice) sings the lyric lines of the melody chosen on the right, if it was written with the Voice instrument. If you have added accompaniment (steps 4–5), the voice and the accompaniment are mixed into one audio file. About 15 seconds a line.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Picker("Voice", selection: $vm.voice) {
                    ForEach(vm.voiceChoices, id: \.key) { Text($0.name).tag($0.key) }
                }
                .fixedSize()
                .help("Singers A and B are the two reference voices that come with SoulX-Singer (both sing in a similar range). The model copies a voice from a short recording; add your own below.")
                Button("Add voice…") { vm.showAddVoice = true }
                    .disabled(vm.busy || !vm.ready)
                    .help("Make a new singer from a recording of a voice singing alone")
            }
            HStack {
                Button("Check lyrics") { vm.checkLyrics() }
                    .disabled(vm.busy || !vm.ready || !vm.hasLyrics)
                Button("Sing the chosen melody") { vm.singSelected() }
                    .disabled(vm.busy || !vm.ready || vm.selected == nil)
                Button(vm.vocalPlaying ? "Stop" : "Play") { vm.vocalPlaying ? vm.stopVocal() : vm.playVocal() }
                    .disabled(vm.vocalURL == nil)
                if let url = vm.vocalURL {
                    Button("Show file") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                }
                Spacer()
                Button("Done") { vm.stopVocal(); vm.showLyrics = false }
                    .keyboardShortcut(.defaultAction)
            }
            if vm.busy { Text(vm.status).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
        }
        .padding(20)
        .frame(width: 540)
        .sheet(isPresented: $vm.showAddVoice) { AddVoiceSheet(vm: vm) }
    }
}

/// Makes a new singer from a recording. The recording is only read here; nothing is uploaded anywhere.
struct AddVoiceSheet: View {
    @ObservedObject var vm: ViewModel
    @StateObject private var form = VoiceForm()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Add a voice").font(.title3).bold()
            Text("Pick a recording of one person singing alone (no band or backing), 10 to 30 seconds, clean, with words. The model works out the words and notes in it (about a minute) and then sings in that voice. Everything stays on this Mac; the first 30 seconds are used.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Choose recording…") {
                    let p = NSOpenPanel()
                    p.allowedContentTypes = [.audio]
                    p.allowsMultipleSelection = false
                    if p.runModal() == .OK, let u = p.url {
                        form.file = u
                        if form.name.isEmpty { form.name = u.deletingPathExtension().lastPathComponent }
                    }
                }
                Text(form.file?.lastPathComponent ?? "No file chosen").foregroundStyle(.secondary).lineLimit(1)
            }
            TextField("Name of the voice", text: $form.name)
            Picker("Language sung in the recording", selection: $form.language) {
                Text("English").tag("English")
                Text("Mandarin").tag("Mandarin")
            }
            Toggle("It is my own voice, or I have the singer’s permission to copy it", isOn: $form.permission)
            HStack {
                Spacer()
                Button("Cancel") { vm.showAddVoice = false }
                Button("Make voice") {
                    if let file = form.file { vm.addVoice(file, name: form.name.trimmingCharacters(in: .whitespaces), language: form.language) }
                    vm.showAddVoice = false
                }
                .keyboardShortcut(.defaultAction)
                .disabled(form.file == nil || form.name.trimmingCharacters(in: .whitespaces).isEmpty || !form.permission)
            }
        }
        .padding(20)
        .frame(width: 480)
    }
}
