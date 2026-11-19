// posters.swift: write one JPEG poster frame per b-roll clip, at the time the film starts using it,
// so a <video> never flashes black while it loads. AVFoundation only.
//   swift film/tools/posters.swift film/assets/broll  name=seconds [name=seconds ...]
import AVFoundation
import AppKit

let args = CommandLine.arguments
guard args.count >= 3 else { print("usage: swift posters.swift <broll dir> name=seconds ..."); exit(1) }
let dir = URL(fileURLWithPath: args[1])
let out = dir.appendingPathComponent("posters")
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
for spec in args.dropFirst(2) {
    let parts = spec.split(separator: "=")
    let name = String(parts[0]), sec = Double(parts[1]) ?? 0
    let asset = AVURLAsset(url: dir.appendingPathComponent(name + ".mp4"))
    let gen = AVAssetImageGenerator(asset: asset)
    gen.appliesPreferredTrackTransform = true
    gen.maximumSize = CGSize(width: 1280, height: 1280)
    gen.requestedTimeToleranceBefore = .zero
    gen.requestedTimeToleranceAfter = .zero
    do {
        let img = try gen.copyCGImage(at: CMTime(seconds: sec, preferredTimescale: 600), actualTime: nil)
        let rep = NSBitmapImageRep(cgImage: img)
        let data = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.82])!
        try data.write(to: out.appendingPathComponent(name + ".jpg"))
        print("poster \(name) @\(sec)s")
    } catch { print("skip \(name): \(error.localizedDescription)") }
}
