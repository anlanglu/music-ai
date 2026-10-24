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

final class KeyField: ObservableObject { @Published var text = "" }

/// Demo: lyrics in, a melody with one note per syllable out, sung by a local singing-voice model (SoulX-Singer).
struct LyricsSheet: View {
    @ObservedObject var vm: ViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Lyrics to a sung melody (demo)").font(.title3).bold()
            Text("One line of text per musical phrase. The melody LLM writes one note per syllable, using the key, tempo, time signature and mood chosen in step 1; the melody appears as the motif, so you can go on to extend and edit it. A local singing model (SoulX-Singer, with a stock reference voice) then sings it. Four lines take about a minute.")
                .font(.callout).foregroundStyle(.secondary)
            TextEditor(text: $vm.lyrics)
                .font(.body)
                .frame(minHeight: 140)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
            Text(vm.status).font(.callout).foregroundStyle(.secondary).lineLimit(3)
            HStack {
                Button(vm.vocalPlaying ? "Stop" : "Play the voice") { vm.vocalPlaying ? vm.stopVocal() : vm.playVocal() }
                    .disabled(vm.vocalURL == nil)
                if let url = vm.vocalURL {
                    Button("Show file") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                }
                Spacer()
                Button("Close") { vm.stopVocal(); vm.showLyrics = false }
                Button("Write melody and sing") { vm.makeSong() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(vm.busy || !vm.ready || vm.lyrics.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 520)
    }
}
