// sheet.swift: contact sheet of PNG/JPG stills with their file names (for QA).
//   swiftc -O sheet.swift -o sheet && ./sheet out.jpg cols thumbWidth img1 img2 …
import AppKit
import Foundation
let a = CommandLine.arguments
let out = a[1], cols = Int(a[2])!, tw = CGFloat(Double(a[3])!)
let files = Array(a[4...])
let th = tw * 9 / 16, pad: CGFloat = 10, lab: CGFloat = 26
let rows = (files.count + cols - 1) / cols
let W = CGFloat(cols) * (tw + pad) + pad, H = CGFloat(rows) * (th + lab + pad) + pad
let ctx = CGContext(data: nil, width: Int(W), height: Int(H), bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.setFillColor(CGColor(gray: 0.15, alpha: 1)); ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
let ns = NSGraphicsContext(cgContext: ctx, flipped: false); NSGraphicsContext.current = ns
for (i, f) in files.enumerated() {
    guard let img = NSImage(contentsOfFile: f), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let c = i % cols, r = i / cols
    let x = pad + CGFloat(c) * (tw + pad), y = H - (pad + CGFloat(r + 1) * (th + lab + pad)) + pad
    ctx.interpolationQuality = .high
    ctx.draw(cg, in: CGRect(x: x, y: y, width: tw, height: th))
    let name = (f as NSString).lastPathComponent
    (name as NSString).draw(at: NSPoint(x: x, y: y + th + 4), withAttributes: [.font: NSFont.systemFont(ofSize: 16, weight: .semibold), .foregroundColor: NSColor.white])
}
let im = ctx.makeImage()!
let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: out) as CFURL, "public.jpeg" as CFString, 1, nil)!
CGImageDestinationAddImage(dest, im, [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary); CGImageDestinationFinalize(dest)
print("sheet \(out) \(Int(W))x\(Int(H))")
