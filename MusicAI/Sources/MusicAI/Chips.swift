import SwiftUI

/// Lays its children out left to right, wrapping onto new lines, for rows of chips.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        arrange(proposal.width ?? 300, subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for (i, p) in arrange(bounds.width, subviews).points.enumerated() {
            subviews[i].place(at: CGPoint(x: bounds.minX + p.x, y: bounds.minY + p.y), proposal: .unspecified)
        }
    }

    private func arrange(_ width: CGFloat, _ subviews: Subviews) -> (points: [CGPoint], size: CGSize) {
        var points: [CGPoint] = [], x: CGFloat = 0, y: CGFloat = 0, rowH: CGFloat = 0, maxX: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x > 0, x + s.width > width { x = 0; y += rowH + spacing; rowH = 0 }
            points.append(CGPoint(x: x, y: y))
            x += s.width + spacing; rowH = max(rowH, s.height); maxX = max(maxX, x - spacing)
        }
        return (points, CGSize(width: maxX, height: y + rowH))
    }
}

/// A wrapping row of toggle chips. `limit` caps how many can be on at once (nil: no cap).
struct ChipPicker: View {
    let items: [String]
    @Binding var selection: Set<String>
    var limit: Int? = nil
    var help: [String: String] = [:]

    var body: some View {
        FlowLayout {
            ForEach(items, id: \.self) { item in
                let on = selection.contains(item)
                Button {
                    if on { selection.remove(item) }
                    else if limit == nil || selection.count < limit! { selection.insert(item) }
                } label: {
                    Text(item).font(.callout).padding(.horizontal, 10).padding(.vertical, 4)
                        .background(Capsule().fill(on ? Color.accentColor : Color.gray.opacity(0.2)))
                        .foregroundStyle(on ? Color.white : Color.primary)
                }
                .buttonStyle(.plain)
                .help(help[item] ?? "")
            }
        }
    }
}
