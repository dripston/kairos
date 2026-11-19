// finish.swift: mux the rendered video with the AAC soundtrack into the final MP4, export a 720p copy,
// and verify both files load and play end to end (AVAsset tracks, durations, a frame read at the end).
//
//   swiftc -O finish.swift -o finish
//   ./finish <video.mov> <soundtrack.m4a> <out.mp4> <out_720.mp4>
import AVFoundation
import Foundation

func fail(_ m: String) -> Never { FileHandle.standardError.write((m + "\n").data(using: .utf8)!); exit(1) }
func wait(_ done: () -> Bool) { while !done() { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) } }
let a = CommandLine.arguments
guard a.count >= 5 else { fail("usage: finish video.mov audio.m4a out.mp4 out_720.mp4") }
let video = AVURLAsset(url: URL(fileURLWithPath: a[1])), audio = AVURLAsset(url: URL(fileURLWithPath: a[2]))
let out = URL(fileURLWithPath: a[3]), out720 = URL(fileURLWithPath: a[4])

let comp = AVMutableComposition()
guard let vt = video.tracks(withMediaType: .video).first else { fail("no video track") }
guard let at = audio.tracks(withMediaType: .audio).first else { fail("no audio track") }
let dur = video.duration
let cv = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
do { try cv.insertTimeRange(vt.timeRange, of: vt, at: .zero) } catch { fail("video insert: \(error)") }
cv.preferredTransform = vt.preferredTransform
let ca = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
let ar = at.timeRange
let ad = CMTimeMinimum(dur, ar.duration)
do { try ca.insertTimeRange(CMTimeRange(start: ar.start, duration: ad), of: at, at: .zero) } catch { fail("audio insert: \(error)") }

func export(_ asset: AVAsset, _ preset: String, _ url: URL) {
    try? FileManager.default.removeItem(at: url)
    guard let ex = AVAssetExportSession(asset: asset, presetName: preset) else { fail("no export session \(preset)") }
    ex.outputURL = url; ex.outputFileType = .mp4; ex.shouldOptimizeForNetworkUse = true
    var done = false
    ex.exportAsynchronously { done = true }
    wait { done }
    if ex.status != .completed { fail("export \(preset) failed: \(String(describing: ex.error))") }
}
export(comp, AVAssetExportPresetPassthrough, out)
export(AVURLAsset(url: out), AVAssetExportPreset1280x720, out720)

for u in [out, out720] {
    let as1 = AVURLAsset(url: u)
    let v = as1.tracks(withMediaType: .video), au = as1.tracks(withMediaType: .audio)
    guard let v0 = v.first else { fail("\(u.lastPathComponent): no video") }
    let gen = AVAssetImageGenerator(asset: as1); gen.requestedTimeToleranceBefore = .zero; gen.requestedTimeToleranceAfter = .zero
    let lastT = CMTimeSubtract(as1.duration, CMTime(value: 1, timescale: 10))
    let img = try? gen.copyCGImage(at: lastT, actualTime: nil)
    // decode every sample of the video track once, to prove it plays start to finish
    let reader = try! AVAssetReader(asset: as1)
    let ro = AVAssetReaderTrackOutput(track: v0, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange])
    reader.add(ro); reader.startReading()
    var frames = 0; while ro.copyNextSampleBuffer() != nil { frames += 1 }
    let size = v0.naturalSize
    let attrs = try! FileManager.default.attributesOfItem(atPath: u.path)
    print(String(format: "%@: %.3f s, %dx%d @ %.2f fps, %d frames decoded (%@), audio tracks %d (%@), last frame %@, %.1f MB",
                 u.lastPathComponent, CMTimeGetSeconds(as1.duration), Int(size.width), Int(size.height), v0.nominalFrameRate, frames,
                 reader.status == .completed ? "reader completed" : "reader status \(reader.status.rawValue)", au.count,
                 au.first.map { String(format: "%.3f s", CMTimeGetSeconds($0.timeRange.duration)) } ?? "-", img != nil ? "ok" : "MISSING",
                 Double((attrs[.size] as! NSNumber).intValue) / 1e6))
}
