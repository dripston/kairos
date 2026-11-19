// qa_frames.swift: automated frame checks on a finished video.
//   ./qa_frames video.mp4 [every_seconds=1]
// For every sampled time:
//   uniform%  share of pixels within ±8 levels of the dominant colour → FAIL if > 97% (an empty frame)
//   still     mean absolute difference to the frame 1 s later → WARN if < 0.15 levels (a dead, identical second)
// Exit code 1 if any frame is empty.
import AVFoundation
import Foundation

let a = CommandLine.arguments
let asset = AVURLAsset(url: URL(fileURLWithPath: a[1]))
let every = a.count > 2 ? Double(a[2])! : 1.0
let gen = AVAssetImageGenerator(asset: asset)
gen.requestedTimeToleranceBefore = .zero; gen.requestedTimeToleranceAfter = .zero
gen.maximumSize = CGSize(width: 480, height: 270)
let dur = CMTimeGetSeconds(asset.duration)

func pixels(_ t: Double) -> [UInt8]? {
    guard let cg = try? gen.copyCGImage(at: CMTime(seconds: min(t, dur - 0.05), preferredTimescale: 600), actualTime: nil) else { return nil }
    let w = 480, h = 270
    var buf = [UInt8](repeating: 0, count: w * h * 4)
    let ctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    return buf
}
func uniform(_ p: [UInt8]) -> Double {
    var hist: [Int: Int] = [:]
    let n = p.count / 4
    for i in 0..<n { let k = (Int(p[i * 4]) >> 3) << 10 | (Int(p[i * 4 + 1]) >> 3) << 5 | (Int(p[i * 4 + 2]) >> 3); hist[k, default: 0] += 1 }
    let mode = hist.max { $0.value < $1.value }!.key
    let mr = (mode >> 10) << 3 + 4, mg = ((mode >> 5) & 31) << 3 + 4, mb = (mode & 31) << 3 + 4
    var c = 0
    for i in 0..<n where abs(Int(p[i * 4]) - mr) <= 8 && abs(Int(p[i * 4 + 1]) - mg) <= 8 && abs(Int(p[i * 4 + 2]) - mb) <= 8 { c += 1 }
    return Double(c) / Double(n)
}
func mad(_ x: [UInt8], _ y: [UInt8]) -> Double { var s = 0; for i in 0..<x.count where i % 4 != 3 { s += abs(Int(x[i]) - Int(y[i])) }; return Double(s) / Double(x.count * 3 / 4) }

var empty = 0, still = 0, n = 0
var t = 0.0
var prev: [UInt8]? = nil
var maxU = 0.0, maxUt = 0.0
while t < dur {
    guard let p = pixels(t) else { t += every; continue }
    let u = uniform(p); n += 1
    if u > maxU { maxU = u; maxUt = t }
    if u > 0.97 { empty += 1; print(String(format: "FAIL empty frame at %6.2f s  (%.1f%% uniform)", t, u * 100)) }
    if let q = pixels(t + 1.0), t + 1.0 < dur {
        let d = mad(p, q)
        if d < 0.15 { still += 1; print(String(format: "WARN identical second at %6.2f s  (mean diff %.3f)", t, d)) }
    }
    prev = p; t += every
}
_ = prev
print(String(format: "frames sampled %d · empty %d · identical seconds %d · most uniform %.1f%% at %.2f s", n, empty, still, maxU * 100, maxUt))
exit(empty > 0 ? 1 : 0)
