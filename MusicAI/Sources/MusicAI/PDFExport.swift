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

        var pages: [Data] = []
        for i in 0..<count {
            let config = WKPDFConfiguration()
            config.rect = CGRect(x: 0, y: CGFloat(i) * Self.pageH, width: Self.pageW, height: Self.pageH)
            pages.append(try await webView.pdf(configuration: config))
        }
        try Self.writeA4(pages: pages, to: destination)
    }

    /// WebKit makes each captured page as many points wide as the web page has CSS pixels (794 x 1123 pt, about
    /// 28 x 40 cm). Redraw every page, still as vectors, onto a real A4 sheet (595.28 x 841.89 pt).
    static func writeA4(pages: [Data], to destination: URL) throws {
        var box = CGRect(x: 0, y: 0, width: 595.2756, height: 841.8898)
        guard let consumer = CGDataConsumer(url: destination as CFURL),
              let ctx = CGContext(consumer: consumer, mediaBox: &box, nil) else {
            throw PDFExportError(message: "Could not write the PDF file.")
        }
        for (i, data) in pages.enumerated() {
            guard let provider = CGDataProvider(data: data as CFData), let doc = CGPDFDocument(provider),
                  let page = doc.page(at: 1) else {
                throw PDFExportError(message: "Could not render page \(i + 1).")
            }
            let src = page.getBoxRect(.mediaBox)
            let scale = min(box.width / src.width, box.height / src.height)
            ctx.beginPDFPage(nil)
            ctx.scaleBy(x: scale, y: scale)
            ctx.translateBy(x: -src.minX, y: -src.minY)
            ctx.drawPDFPage(page)
            ctx.endPDFPage()
        }
        ctx.closePDF()
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
