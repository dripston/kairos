// thumbs.swift: frames from a finished video at fixed times, for QA.
//   ./thumbs video.mp4 outdir every_seconds [width]     → outdir/<name>_<t>.png
import AVFoundation
import Foundation
let a = CommandLine.arguments
let asset = AVURLAsset(url: URL(fileURLWithPath: a[1])), dir = a[2], every = Double(a[3])!, w = a.count > 4 ? Double(a[4])! : 0
try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
let gen = AVAssetImageGenerator(asset: asset); gen.requestedTimeToleranceBefore = .zero; gen.requestedTimeToleranceAfter = .zero
if w > 0 { gen.maximumSize = CGSize(width: w, height: w * 9 / 16) }
let d = CMTimeGetSeconds(asset.duration), name = ((a[1] as NSString).lastPathComponent as NSString).deletingPathExtension
var t = 0.0
var times: [Double] = []
while t < d { times.append(t); t += every }
for t in times {
    let tt = min(t + 0.02, d - 0.05)
    guard let cg = try? gen.copyCGImage(at: CMTime(seconds: tt, preferredTimescale: 600), actualTime: nil) else { print("no frame at \(t)"); continue }
    let p = "\(dir)/\(name)_\(String(format: "%06.2f", t)).png"
    let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: p) as CFURL, "public.png" as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, cg, nil); CGImageDestinationFinalize(dest); print(p)
}
