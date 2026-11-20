// render.swift: render a compositor film frame by frame to H.264, or to PNG stills for QA.
//
//   swiftc -O render.swift -o render
//   ./render --film product --out out/product_video.mov [--fps 30] [--from 0] [--to 999]
//   ./render --film product --stills out/qa_product --every 5        (one PNG every 5 s + at the end)
//   ./render --film product --stills out/x --times 3.2,10,44.5       (PNGs at given times)
//
// The page is http://127.0.0.1:8772/walkthrough/comp/index.html?film=<name> (serve film/ with tools/serve.py).
// For each frame: `await seek(t)` in the page, then WKWebView.takeSnapshot → CVPixelBuffer → AVAssetWriter.
import AVFoundation
import AppKit
import Foundation
import WebKit
setvbuf(stdout, nil, _IOLBF, 0)

func fail(_ m: String) -> Never { FileHandle.standardError.write((m + "\n").data(using: .utf8)!); exit(1) }
func waitUntil(_ timeout: Double = 60, _ cond: () -> Bool) -> Bool {
    let end = Date().addingTimeInterval(timeout)
    while !cond() { if Date() > end { return false }; RunLoop.main.run(until: Date().addingTimeInterval(0.002)) }
    return true
}
var opts: [String: String] = [:]
var k = 1; let A = CommandLine.arguments
while k < A.count { if A[k].hasPrefix("--"), k + 1 < A.count { opts[String(A[k].dropFirst(2))] = A[k + 1]; k += 2 } else { k += 1 } }
let film = opts["film"] ?? "product"
let fps = Double(opts["fps"] ?? "30")!
let Wd = 1920.0, Ht = 1080.0                       // the CSS stage
let outSize = (opts["size"] ?? "1920x1080").split(separator: "x").map { Double($0)! }
let Ow = outSize[0], Oh = outSize[1]               // the encoded frame: the stage is re-painted at this scale (not upscaled)
let bitrate = Int(opts["mbps"] ?? (Ow > 3000 ? "48" : Ow > 2000 ? "28" : "16"))! * 1_000_000
let port = opts["port"] ?? "8772"

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let window = NSWindow(contentRect: NSRect(x: -20000, y: -20000, width: Wd, height: Ht), styleMask: [.borderless], backing: .buffered, defer: false)
let cfg = WKWebViewConfiguration()
cfg.websiteDataStore = .nonPersistent()
cfg.mediaTypesRequiringUserActionForPlayback = []
let web = WKWebView(frame: NSRect(x: 0, y: 0, width: Wd, height: Ht), configuration: cfg)
window.contentView = web
window.orderBack(nil)
// the window is off-screen: tell WebKit not to treat it as occluded, so every seek is painted and committed before the snapshot
do {
    let sel = NSSelectorFromString("_setWindowOcclusionDetectionEnabled:")
    if web.responds(to: sel) {
        typealias SetBool = @convention(c) (AnyObject, Selector, Bool) -> Void
        unsafeBitCast(web.method(for: sel), to: SetBool.self)(web, sel, false)
        print("occlusion detection off")
    }
}

final class Nav: NSObject, WKNavigationDelegate {
    var done = false
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) { done = true }
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { fail("web content process terminated") }
}
let nav = Nav(); web.navigationDelegate = nav

func js(_ code: String) -> Any? {
    var result: Any?; var finished = false
    web.callAsyncJavaScript(code, arguments: [:], in: nil, in: .page) { r in
        switch r { case .success(let v): result = v; case .failure(let e): FileHandle.standardError.write("js: \(e)\n".data(using: .utf8)!) }
        finished = true
    }
    if !waitUntil(60, { finished }) { fail("js timeout: \(code.prefix(80))") }
    return result
}

let url = URL(string: "http://127.0.0.1:\(port)/walkthrough/comp/index.html?film=\(film)&r=\(Int(Date().timeIntervalSince1970))")!
web.load(URLRequest(url: url))
guard waitUntil(30, { nav.done }) else { fail("page did not load") }
let readyOK = waitUntil(120) {
    if let e = js("return window.loadError || null") as? String { fail("load error: \(e)") }
    return (js("return !!window.ready") as? Bool) == true
}
if !readyOK { fail("film never became ready") }
let duration = (js("return KAIROS.duration()") as? Double) ?? 0
print("film \(film) duration \(duration) s")

func snapshotOnce() -> CGImage? {
    var out: CGImage?
    autoreleasepool {
        let c = WKSnapshotConfiguration(); c.snapshotWidth = NSNumber(value: Ow / window.backingScaleFactor); c.afterScreenUpdates = true
        var img: NSImage?; var finished = false
        web.takeSnapshot(with: c) { i, _ in img = i; finished = true }
        _ = waitUntil(20) { finished }
        out = img?.cgImage(forProposedRect: nil, context: nil, hints: nil)
    }
    return out
}
func snapshot() -> CGImage {
    var out: CGImage?
    for attempt in 0..<6 {
        autoreleasepool {
            let c = WKSnapshotConfiguration(); c.snapshotWidth = NSNumber(value: Ow / window.backingScaleFactor); c.afterScreenUpdates = true
            var img: NSImage?; var finished = false
            web.takeSnapshot(with: c) { i, _ in img = i; finished = true }
            _ = waitUntil(20) { finished }
            out = img?.cgImage(forProposedRect: nil, context: nil, hints: nil)
        }
        if out != nil { break }
        FileHandle.standardError.write("snapshot retry \(attempt)\n".data(using: .utf8)!)
        RunLoop.main.run(until: Date().addingTimeInterval(0.2))
    }
    guard let o = out else { fail("snapshot failed") }
    return o
}
func seek(_ t: Double) {
    let fresh = (js("return await seek(\(t))") as? Int) ?? 0
    if fresh > 0 { // a new capture frame was just swapped in: give WebKit a beat to raster it, then flush once
        RunLoop.main.run(until: Date().addingTimeInterval(0.25)); _ = snapshotOnce()   // a new image: let WebKit rasterise it fully before the real snapshot
    }
}

func writePNG(_ cg: CGImage, _ path: String) {
    let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL, "public.png" as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, cg, nil); CGImageDestinationFinalize(dest)
}

if let cp = opts["clicks"] {
    let arr = (js("return KAIROS.clicks()") as? [Double]) ?? []
    try! JSONSerialization.data(withJSONObject: arr).write(to: URL(fileURLWithPath: cp)); print("clicks \(arr.count) → \(cp)"); exit(0)
}
if let dp = opts["describe"] { // JSON lines: what is on screen every N seconds
    let ev = Double(opts["every"] ?? "5")!; var t = 0.0; var lines: [String] = []
    while t < duration { let j = (js("return JSON.stringify({t: \(t), layers: KAIROS.describe(\(t))})") as? String) ?? "{}"; lines.append(j); t += ev }
    try! lines.joined(separator: "\n").write(toFile: dp, atomically: true, encoding: .utf8); print("describe \(lines.count) → \(dp)"); exit(0)
}
if opts["qa"] != nil { // walk the film at 10 fps without snapshots and report overlay collisions
    var t = 0.0; while t < duration { seek(t); t += 0.1 }
    let q = (js("return JSON.stringify(KAIROS.qa())") as? String) ?? "[]"
    let arr = (try? JSONSerialization.jsonObject(with: q.data(using: .utf8)!)) as? [[String: Any]] ?? []
    var groups: [String: (Double, Double, Int)] = [:]
    for w in arr { let k = "\(w["kind"]!) · \(w["text"]!)"; let tt = w["t"] as! Double; let g = groups[k] ?? (tt, tt, 0); groups[k] = (min(g.0, tt), max(g.1, tt), g.2 + 1) }
    for (k, g) in groups.sorted(by: { $0.value.0 < $1.value.0 }) { print(String(format: "QA %.1f–%.1f s  %@  (%d samples)", g.0, g.1, k, g.2)) }
    print("QA collisions: \(groups.count)"); exit(groups.isEmpty ? 0 : 2)
}
if let dir = opts["stills"] {
    try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
    var times: [Double] = []
    if let ts = opts["times"] { times = ts.split(separator: ",").map { Double($0)! } }
    else { let ev = Double(opts["every"] ?? "5")!; var t = 0.0; while t < duration { times.append(t); t += ev }; times.append(max(0, duration - 0.05)) }
    for t in times {
        seek(t); let cg = snapshot()
        let p = "\(dir)/\(film)_\(String(format: "%06.2f", t)).png"; writePNG(cg, p); print("still \(p)")
    }
    exit(0)
}

guard let outPath = opts["out"] else { fail("--out or --stills required") }
let from = Double(opts["from"] ?? "0")!, to = min(Double(opts["to"] ?? "99999")!, duration)
let outURL = URL(fileURLWithPath: outPath)
try? FileManager.default.removeItem(at: outURL)
let writer = try! AVAssetWriter(outputURL: outURL, fileType: .mov)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: Int(Ow), AVVideoHeightKey: Int(Oh),
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: bitrate, AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
                                      AVVideoMaxKeyFrameIntervalKey: Int(fps * 2), AVVideoExpectedSourceFrameRateKey: Int(fps)],
    AVVideoColorPropertiesKey: [AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2, AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2, AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2]])
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA, kCVPixelBufferWidthKey as String: Int(Ow), kCVPixelBufferHeightKey as String: Int(Oh)])
writer.add(input)
writer.startWriting(); writer.startSession(atSourceTime: .zero)
let n0 = Int((from * fps).rounded()), n1 = Int((to * fps).rounded())
let started = Date()
let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
// --preroll S: play the S seconds before --from without writing them, so a range starts with every layer already painted
let preroll = Double(opts["preroll"] ?? "0")!
for n in max(0, n0 - Int((preroll * fps).rounded()))..<n0 { autoreleasepool { seek(Double(n) / fps); _ = snapshot() } }
for n in n0..<n1 {
    autoreleasepool {
        let t = Double(n) / fps
        seek(t)
        let cg = snapshot()
        _ = waitUntil(30) { input.isReadyForMoreMediaData }
        var pb: CVPixelBuffer?
        CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &pb)
        guard let buf = pb else { fail("no pixel buffer") }
        CVPixelBufferLockBaseAddress(buf, [])
        let ctx = CGContext(data: CVPixelBufferGetBaseAddress(buf), width: Int(Ow), height: Int(Oh), bitsPerComponent: 8,
                            bytesPerRow: CVPixelBufferGetBytesPerRow(buf), space: srgb,
                            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: Ow, height: Oh))
        CVPixelBufferUnlockBaseAddress(buf, [])
        adaptor.append(buf, withPresentationTime: CMTime(value: CMTimeValue(n - n0), timescale: CMTimeScale(fps)))
        if (n - n0) % 150 == 0 {
            let el = Date().timeIntervalSince(started), done = Double(n - n0 + 1), left = Double(n1 - n) * el / done
            print(String(format: "frame %d/%d  t=%.1f  %.0f ms/frame  eta %.0f s", n, n1, t, el / done * 1000, left))
        }
    }
}
input.markAsFinished()
var finished = false
writer.finishWriting { finished = true }
_ = waitUntil(120) { finished }
if writer.status != .completed { fail("writer: \(String(describing: writer.error))") }
print(String(format: "wrote %@  %d frames in %.0f s", outPath, n1 - n0, Date().timeIntervalSince(started)))
