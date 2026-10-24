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
