// Draws the Composer app icon (a pair of beamed eighth notes on a rounded square) and writes an .iconset folder.
// Run:  swift make_icon.swift <output.iconset>      then:  iconutil -c icns <output.iconset>
import AppKit

func render(size s: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: s, pixelsHigh: s, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let ctx = NSGraphicsContext.current!.cgContext
    let S = CGFloat(s)
    let margin = S * 0.098                       // macOS icons leave a transparent margin around the tile
    let tile = CGRect(x: margin, y: margin, width: S - 2 * margin, height: S - 2 * margin)
    let c = tile.width

    // soft drop shadow under the tile
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -S * 0.012), blur: S * 0.03, color: NSColor.black.withAlphaComponent(0.35).cgColor)
    let path = CGPath(roundedRect: tile, cornerWidth: c * 0.225, cornerHeight: c * 0.225, transform: nil)
    ctx.addPath(path); ctx.setFillColor(NSColor(red: 0.25, green: 0.2, blue: 0.7, alpha: 1).cgColor); ctx.fillPath()
    ctx.restoreGState()

    // background gradient clipped to the tile
    ctx.saveGState()
    ctx.addPath(path); ctx.clip()
    let grad = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: [
        NSColor(red: 0.40, green: 0.45, blue: 1.00, alpha: 1).cgColor,   // top: bright periwinkle
        NSColor(red: 0.42, green: 0.22, blue: 0.80, alpha: 1).cgColor,   // middle: violet
        NSColor(red: 0.20, green: 0.12, blue: 0.50, alpha: 1).cgColor,   // bottom: deep indigo
    ] as CFArray, locations: [0, 0.55, 1])!
    ctx.drawLinearGradient(grad, start: CGPoint(x: tile.midX, y: tile.maxY), end: CGPoint(x: tile.midX, y: tile.minY), options: [])
    // a faint highlight across the top
    let gloss = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: [
        NSColor.white.withAlphaComponent(0.18).cgColor, NSColor.white.withAlphaComponent(0).cgColor] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(gloss, start: CGPoint(x: tile.midX, y: tile.maxY), end: CGPoint(x: tile.midX, y: tile.midY), options: [])
    ctx.restoreGState()

    // the notes, in tile-relative coordinates (0...1)
    // The artwork is drawn off to one side on purpose and shifted here: centring the bounding box looks wrong because
    // the heavy note heads sit low, so the offset was tuned until both the bounding box and the centre of mass
    // (measured from the rendered pixels) sat on the tile's centre.
    let nudgeX: CGFloat = -0.038, nudgeY: CGFloat = 0.048
    func P(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: tile.minX + (x + nudgeX) * c, y: tile.minY + (y + nudgeY) * c) }
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -S * 0.008), blur: S * 0.02, color: NSColor.black.withAlphaComponent(0.30).cgColor)
    ctx.setFillColor(NSColor.white.cgColor)
    // note heads: tilted ellipses
    for (cx, cy) in [(0.335, 0.285), (0.700, 0.215)] as [(CGFloat, CGFloat)] {
        ctx.saveGState()
        ctx.translateBy(x: P(cx, cy).x, y: P(cx, cy).y)
        ctx.rotate(by: 20 * .pi / 180)
        ctx.fillEllipse(in: CGRect(x: -0.118 * c, y: -0.082 * c, width: 0.236 * c, height: 0.164 * c))
        ctx.restoreGState()
    }
    // stems
    let stemW = 0.036 * c
    ctx.fill(CGRect(x: P(0.425, 0).x - stemW / 2, y: P(0, 0.30).y, width: stemW, height: 0.50 * c))
    ctx.fill(CGRect(x: P(0.790, 0).x - stemW / 2, y: P(0, 0.23).y, width: stemW, height: 0.50 * c))
    // beam joining the stem tops, sloping down to the right
    let beam = CGMutablePath()
    beam.move(to: P(0.425 - 0.018, 0.805)); beam.addLine(to: P(0.790 + 0.018, 0.735))
    beam.addLine(to: P(0.790 + 0.018, 0.655)); beam.addLine(to: P(0.425 - 0.018, 0.725)); beam.closeSubpath()
    ctx.addPath(beam); ctx.fillPath()
    ctx.restoreGState()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AppIcon.iconset"
try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
// (file name, pixel size) as iconutil expects
for (name, px) in [("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32), ("icon_32x32@2x", 64), ("icon_128x128", 128),
                   ("icon_128x128@2x", 256), ("icon_256x256", 256), ("icon_256x256@2x", 512), ("icon_512x512", 512), ("icon_512x512@2x", 1024)] {
    try! render(size: px).write(to: URL(fileURLWithPath: "\(out)/\(name).png"))
}
print("wrote", out)
