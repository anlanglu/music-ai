import AppKit
import PDFKit
import WebKit

struct PDFExportError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Turns a paged score (an HTML file with one 794x1123 px `.page` per A4 sheet) into a vector PDF by loading it
/// in an off-screen web view and capturing each page.
@MainActor
final class PDFExporter: NSObject, WKNavigationDelegate {
    static let pageW: CGFloat = 794
    static let pageH: CGFloat = 1123

    private let webView: WKWebView
    private let window: NSWindow  // WKWebView needs a host window to lay out reliably, even off-screen
    private var loaded: CheckedContinuation<Void, Error>?

    override init() {
        let frame = NSRect(x: 0, y: 0, width: Self.pageW, height: Self.pageH)
        webView = WKWebView(frame: frame)
        window = NSWindow(contentRect: frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = webView
        super.init()
        webView.navigationDelegate = self
    }

    func export(html: URL, to destination: URL) async throws {
        try await load(html)
        let count = (try await webView.evaluateJavaScript("document.querySelectorAll('.page').length") as? Int) ?? 0
        guard count > 0 else { throw PDFExportError(message: "The score has no pages to export.") }

        // Make the view tall enough to show every page, so each page's rect is just (0, i * pageH).
        let total = CGFloat(count) * Self.pageH
        window.setContentSize(NSSize(width: Self.pageW, height: total))
        webView.frame = NSRect(x: 0, y: 0, width: Self.pageW, height: total)
        try await Task.sleep(nanoseconds: 400_000_000)  // let layout settle

        let pdf = PDFDocument()
        for i in 0..<count {
            let config = WKPDFConfiguration()
            config.rect = CGRect(x: 0, y: CGFloat(i) * Self.pageH, width: Self.pageW, height: Self.pageH)
            let data = try await webView.pdf(configuration: config)
            guard let page = PDFDocument(data: data)?.page(at: 0) else {
                throw PDFExportError(message: "Could not render page \(i + 1).")
            }
            pdf.insert(page, at: pdf.pageCount)
        }
        guard pdf.write(to: destination) else { throw PDFExportError(message: "Could not write the PDF file.") }
    }

    private func load(_ url: URL) async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            loaded = cont
            webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }
    }

    nonisolated func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        Task { @MainActor in self.loaded?.resume(); self.loaded = nil }
    }

    nonisolated func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        Task { @MainActor in self.loaded?.resume(throwing: error); self.loaded = nil }
    }

    nonisolated func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        Task { @MainActor in self.loaded?.resume(throwing: error); self.loaded = nil }
    }
}
