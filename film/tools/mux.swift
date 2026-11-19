// mux.swift: put the Kairos soundtrack under a screen recording and export a 1080p MP4.
// Uses only Apple's AVFoundation (no ffmpeg needed).
//
//   swift film/tools/mux.swift <recording.mov> <soundtrack.wav|.m4a> <out.mp4> [offset_seconds]
//   swift film/tools/mux.swift --make-test-clip <out.mov> [seconds]   (3 s dummy video, for testing)
//
// offset_seconds trims that much from the start of the recording, so the film's 0:00
// lines up with the soundtrack's 0:00 (record mode holds the first frame for 1.5 s).
import AVFoundation
import CoreVideo
import Foundation

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(1)
}

func waitFor(_ done: () -> Bool) {
    while !done() { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
}

func makeTestClip(_ path: String, seconds: Double) {
    let url = URL(fileURLWithPath: path)
    try? FileManager.default.removeItem(at: url)
    guard let writer = try? AVAssetWriter(outputURL: url, fileType: .mov) else { fail("cannot create writer") }
    let w = 1920, h = 1080, fps: Int32 = 30
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
        AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: w, AVVideoHeightKey: h])
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
        kCVPixelBufferWidthKey as String: w, kCVPixelBufferHeightKey as String: h])
    writer.add(input)
    writer.startWriting()
    writer.startSession(atSourceTime: .zero)
    let frames = Int(seconds * Double(fps))
    for i in 0..<frames {
        while !input.isReadyForMoreMediaData { usleep(1000) }
        var pb: CVPixelBuffer?
        CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &pb)
        guard let buf = pb else { fail("no pixel buffer") }
        CVPixelBufferLockBaseAddress(buf, [])
        let base = CVPixelBufferGetBaseAddress(buf)!.assumingMemoryBound(to: UInt8.self)
        let stride = CVPixelBufferGetBytesPerRow(buf)
        let shade = UInt8(30 + (i * 3) % 120)
        for y in 0..<h { for x in 0..<w {
            let p = base + y * stride + x * 4
            p[0] = shade; p[1] = UInt8(60 + x % 60); p[2] = 20; p[3] = 255
        } }
        CVPixelBufferUnlockBaseAddress(buf, [])
        adaptor.append(buf, withPresentationTime: CMTime(value: CMTimeValue(i), timescale: fps))
    }
    input.markAsFinished()
    var finished = false
    writer.finishWriting { finished = true }
    waitFor { finished }
    if writer.status != .completed { fail("test clip failed: \(String(describing: writer.error))") }
    print("wrote \(path) (\(seconds) s, 1920x1080)")
}

let args = CommandLine.arguments
if args.count >= 3 && args[1] == "--make-test-clip" {
    makeTestClip(args[2], seconds: args.count > 3 ? Double(args[3]) ?? 3 : 3)
    exit(0)
}
guard args.count >= 4 else {
    fail("usage: swift mux.swift <recording.mov> <soundtrack.wav|.m4a> <out.mp4> [offset_seconds]")
}
let video = AVURLAsset(url: URL(fileURLWithPath: args[1]))
let audio = AVURLAsset(url: URL(fileURLWithPath: args[2]))
let outURL = URL(fileURLWithPath: args[3])
let offset = args.count > 4 ? Double(args[4]) ?? 0 : 0
try? FileManager.default.removeItem(at: outURL)

guard let vTrack = video.tracks(withMediaType: .video).first else { fail("no video track in \(args[1])") }
guard let aTrack = audio.tracks(withMediaType: .audio).first else { fail("no audio track in \(args[2])") }

let comp = AVMutableComposition()
let start = CMTime(seconds: offset, preferredTimescale: 600)
let vLen = CMTimeSubtract(video.duration, start)
let length = CMTimeMinimum(vLen, audio.duration)
guard let cv = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid),
      let ca = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) else { fail("composition failed") }
do {
    try cv.insertTimeRange(CMTimeRange(start: start, duration: length), of: vTrack, at: .zero)
    try ca.insertTimeRange(CMTimeRange(start: .zero, duration: length), of: aTrack, at: .zero)
} catch { fail("insert failed: \(error)") }
cv.preferredTransform = vTrack.preferredTransform

guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPreset1920x1080) else { fail("no export session") }
export.outputURL = outURL
export.outputFileType = .mp4
export.shouldOptimizeForNetworkUse = true
var done = false
export.exportAsynchronously { done = true }
waitFor { done }
if export.status != .completed { fail("export failed: \(String(describing: export.error))") }
print(String(format: "wrote %@ (%.1f s, 1080p, H.264 + AAC)", args[3], CMTimeGetSeconds(length)))
