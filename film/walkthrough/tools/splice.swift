// splice.swift: replace a time range of a rendered (silent) film with a re-rendered segment, frame-exact.
//   splice base.mov seg.mov <from seconds> out.mov
// seg.mov must come from `render --from <from> --to <to>` at the same fps. Frames [from, from + seg duration) of base
// are replaced by seg's frames; everything else is copied frame by frame. Re-encodes once (H.264 16 Mb/s, BT.709),
// like the renderer. Then mux with tools/finish as usual (audio and captions are unchanged).
import AVFoundation
import Foundation

let a = CommandLine.arguments
guard a.count == 5, let from = Double(a[3]) else { print("usage: splice base.mov seg.mov from out.mov"); exit(1) }
func reader(_ p: String) -> (AVAssetReader, AVAssetReaderTrackOutput, Double, CGSize) {
    let asset = AVURLAsset(url: URL(fileURLWithPath: p))
    let sem = DispatchSemaphore(value: 0); var track: AVAssetTrack?; var fps: Float = 30; var size = CGSize(width: 1920, height: 1080)
    Task { track = try? await asset.loadTracks(withMediaType: .video).first
           if let t = track { fps = (try? await t.load(.nominalFrameRate)) ?? 30; size = (try? await t.load(.naturalSize)) ?? size }; sem.signal() }
    sem.wait()
    let r = try! AVAssetReader(asset: asset)
    let o = AVAssetReaderTrackOutput(track: track!, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
    o.alwaysCopiesSampleData = false; r.add(o); r.startReading()
    return (r, o, Double(fps), size)
}
let (rb, ob, fps, size) = reader(a[1])
let (rs, os, sfps, _) = reader(a[2])
if abs(fps - sfps) > 0.01 { print("fps mismatch \(fps) vs \(sfps)"); exit(1) }
let out = URL(fileURLWithPath: a[4]); try? FileManager.default.removeItem(at: out)
let w = try! AVAssetWriter(outputURL: out, fileType: .mov)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: Int(size.width), AVVideoHeightKey: Int(size.height),
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: size.width > 3000 ? 48_000_000 : size.width > 2000 ? 28_000_000 : 16_000_000, AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
                                      AVVideoMaxKeyFrameIntervalKey: Int(fps * 2), AVVideoExpectedSourceFrameRateKey: Int(fps)],
    AVVideoColorPropertiesKey: [AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2, AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2, AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2]])
input.expectsMediaDataInRealTime = false
let ad = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: nil)
w.add(input); w.startWriting(); w.startSession(atSourceTime: .zero)
let f0 = Int((from * fps).rounded())
var n = 0, replaced = 0
var segDone = false
while let sb = ob.copyNextSampleBuffer() {
    guard let pb = CMSampleBufferGetImageBuffer(sb) else { continue }
    var use = pb
    if n >= f0 && !segDone {
        if let ss = os.copyNextSampleBuffer(), let sp = CMSampleBufferGetImageBuffer(ss) { use = sp; replaced += 1 } else { segDone = true }
    }
    while !input.isReadyForMoreMediaData { Thread.sleep(forTimeInterval: 0.002) }
    ad.append(use, withPresentationTime: CMTime(value: CMTimeValue(n), timescale: CMTimeScale(fps)))
    n += 1
}
input.markAsFinished()
let sem = DispatchSemaphore(value: 0); w.finishWriting { sem.signal() }; sem.wait()
print(String(format: "spliced %d frames from %.3f s (frame %d) into %d frames → %@", replaced, from, f0, n, a[4]))
_ = rb; _ = rs
