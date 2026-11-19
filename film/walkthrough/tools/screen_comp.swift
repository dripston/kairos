// screen_comp.swift: put the REAL product UI onto a screen in live-action footage.
//
//   swiftc -O screen_comp.swift -o screen_comp && ./screen_comp shot.json
//
// shot.json:
// { "clip": "…mp4", "in": 0.0, "dur": 4.0, "out": "comp/H8_phone.mov", "fps": 30,
//   "mode": "green" | "quad",                       green: chroma-key + per-frame corner tracking
//   "quads": [{"t": 0, "q": [[x,y],[x,y],[x,y],[x,y]]}],  quad: screen corners TL,TR,BR,BL in output px (interpolated)
//   "ui": [{"t": 0, "img": "…jpg", "crop": [x, y, w, h]}],   UI frames over time (t from the clip's in-point; crop in image px)
//   "expand": 0.025, "uiGain": 0.94,                 grow the tracked quad into the rounded corners; dim the UI to sit in the scene
//   "grade": {"saturation": 0.96, "contrast": 1.06, "warmth": 5600}, "flip": false,
//   "debug": false }                                  debug draws the tracked corners
// Output: 1920×1080 H.264 .mov. Footage is aspect-filled to 16:9.
import AVFoundation
import CoreImage
import CoreImage.CIFilterBuiltins
import Foundation

func fail(_ m: String) -> Never { FileHandle.standardError.write((m + "\n").data(using: .utf8)!); exit(1) }
let spec = try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))) as! [String: Any]
let OW = 1920, OH = 1080
let fps = (spec["fps"] as? Double) ?? 30
let tIn = (spec["in"] as? Double) ?? 0, dur = spec["dur"] as! Double
let mode = (spec["mode"] as? String) ?? "green"
let expand = (spec["expand"] as? Double) ?? 0.025
let uiGain = (spec["uiGain"] as? Double) ?? 0.94
let debug = (spec["debug"] as? Bool) ?? false
let ctx = CIContext(options: [.workingColorSpace: CGColorSpace(name: CGColorSpace.sRGB)!, .cacheIntermediates: false])

// ---------- chroma key cube (premultiplied RGBA), with green despill
func keyCube() -> CIFilter {
    let n = 64; var data = [Float](repeating: 0, count: n * n * n * 4)
    for b in 0..<n { for g in 0..<n { for r in 0..<n {
        let R = Float(r) / Float(n - 1), G = Float(g) / Float(n - 1), B = Float(b) / Float(n - 1)
        let green = G - max(R, B)
        let lo: Float = 0.10, hi: Float = 0.26
        var a: Float = 1
        if green > lo { a = green >= hi ? 0 : 1 - (green - lo) / (hi - lo) }
        let G2 = green > 0 ? max(R, B) + green * 0.15 : G   // despill
        let i = ((b * n + g) * n + r) * 4
        data[i] = R * a; data[i + 1] = G2 * a; data[i + 2] = B * a; data[i + 3] = a
    } } }
    let f = CIFilter.colorCube(); f.cubeDimension = Float(n)
    f.cubeData = data.withUnsafeBufferPointer { Data(buffer: $0) }
    return f
}
let cube = keyCube()

// ---------- UI timeline
struct UIKey { let t: Double; let img: CIImage }
var uiCache: [String: CIImage] = [:]
let uiKeys: [UIKey] = ((spec["ui"] as? [[String: Any]]) ?? []).map { k in
    let p = k["img"] as! String
    var im = uiCache[p] ?? CIImage(contentsOf: URL(fileURLWithPath: p))!
    uiCache[p] = im
    if let c = k["crop"] as? [Double] { // crop given in image px with top-left origin
        let h = im.extent.height
        im = im.cropped(to: CGRect(x: c[0], y: h - c[1] - c[3], width: c[2], height: c[3]))
        im = im.transformed(by: CGAffineTransform(translationX: -c[0], y: -(h - c[1] - c[3])))
    }
    return UIKey(t: (k["t"] as? Double) ?? 0, img: im)
}
func uiAt(_ t: Double) -> CIImage? { var r: CIImage? = nil; for k in uiKeys where k.t <= t + 1e-6 { r = k.img }; return r ?? uiKeys.first?.img }

// ---------- quads
typealias Quad = [CGPoint] // TL, TR, BR, BL in output px, top-left origin
func lerpQ(_ a: Quad, _ b: Quad, _ s: Double) -> Quad { (0..<4).map { CGPoint(x: a[$0].x + (b[$0].x - a[$0].x) * s, y: a[$0].y + (b[$0].y - a[$0].y) * s) } }
let quadKeys: [(Double, Quad)] = ((spec["quads"] as? [[String: Any]]) ?? []).map { k in
    ((k["t"] as? Double) ?? 0, (k["q"] as! [[Double]]).map { CGPoint(x: $0[0], y: $0[1]) })
}
func quadAt(_ t: Double) -> Quad? {
    guard !quadKeys.isEmpty else { return nil }
    if t <= quadKeys[0].0 { return quadKeys[0].1 }
    for i in 1..<quadKeys.count where t < quadKeys[i].0 {
        let a = quadKeys[i - 1], b = quadKeys[i]; let s = (t - a.0) / (b.0 - a.0)
        let e = s < 0.5 ? 2 * s * s : 1 - pow(-2 * s + 2, 2) / 2
        return lerpQ(a.1, b.1, e)
    }
    return quadKeys.last!.1
}
let whiteMin = (spec["whiteMin"] as? Double) ?? 170
let roiSmall: [Double]? = (spec["roi"] as? [Double]).map { r in r.map { $0 * Double(480) / Double(1920) } }
// green tracking on a small CPU copy: extreme points of x+y and x−y over the keyed region
let SW = 480, SH = 270
var small = [UInt8](repeating: 0, count: SW * SH * 4)
let upHint: CGPoint = { if let u = spec["up"] as? [Double] { return CGPoint(x: u[0], y: u[1]) }; return CGPoint(x: 0, y: -1) }()
func cross(_ o: CGPoint, _ a: CGPoint, _ b: CGPoint) -> CGFloat { (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x) }
func hull(_ pts: [CGPoint]) -> [CGPoint] { // Andrew's monotone chain
    let p = pts.sorted { $0.x == $1.x ? $0.y < $1.y : $0.x < $1.x }
    if p.count < 3 { return p }
    var lo: [CGPoint] = [], up: [CGPoint] = []
    for q in p { while lo.count >= 2 && cross(lo[lo.count - 2], lo[lo.count - 1], q) <= 0 { lo.removeLast() }; lo.append(q) }
    for q in p.reversed() { while up.count >= 2 && cross(up[up.count - 2], up[up.count - 1], q) <= 0 { up.removeLast() }; up.append(q) }
    return Array(lo.dropLast() + up.dropLast())
}
func tri(_ a: CGPoint, _ b: CGPoint, _ c: CGPoint) -> CGFloat { abs(cross(a, b, c)) / 2 }
// the screen's corners: the largest-area quadrilateral inscribed in the convex hull of the keyed region,
// ordered so that its top edge is the short edge facing the "up" hint (phones can be held at any angle)
func trackGreen(_ frame: CIImage) -> Quad? {
    let s = frame.transformed(by: CGAffineTransform(scaleX: CGFloat(SW) / CGFloat(OW), y: CGFloat(SH) / CGFloat(OH)))
    ctx.render(s, toBitmap: &small, rowBytes: SW * 4, bounds: CGRect(x: 0, y: 0, width: SW, height: SH), format: .RGBA8, colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
    var pts: [CGPoint] = []
    for y in 0..<SH {
        var first = -1, last = -1
        for x in 0..<SW {
            if let roi = roiSmall, !(Double(x) >= roi[0] && Double(x) < roi[0] + roi[2] && Double(y) >= roi[1] && Double(y) < roi[1] + roi[3]) { continue }
            let i = (y * SW + x) * 4; let r = Double(small[i]), g = Double(small[i + 1]), b = Double(small[i + 2])
            let hit = mode == "white" ? (min(r, g, b) > whiteMin && max(r, g, b) - min(r, g, b) < 40) : (g > 90 && g - max(r, b) > 45)
            if hit { if first < 0 { first = x }; last = x }
        }
        if first >= 0 { pts.append(CGPoint(x: first, y: y)); pts.append(CGPoint(x: last, y: y)) } // row extremes are enough for the hull
    }
    if pts.count < 40 { return nil }
    let h = hull(pts); let n = h.count
    if n < 4 { return nil }
    var best: (CGFloat, [Int]) = (0, [0, 1, 2, 3])
    for i in 0..<n { for j in (i + 2)..<(i + n - 1) where j < i + n {
        let jj = j % n; var bk = -1, bl = -1, ak: CGFloat = 0, al: CGFloat = 0
        var k = (i + 1) % n
        while k != jj { let a = tri(h[i], h[jj], h[k]); if a > ak { ak = a; bk = k }; k = (k + 1) % n }
        var l = (jj + 1) % n
        while l != i { let a = tri(h[i], h[jj], h[l]); if a > al { al = a; bl = l }; l = (l + 1) % n }
        if bk >= 0 && bl >= 0 && ak + al > best.0 { best = (ak + al, [i, bk, jj, bl]) }
    } }
    var q = best.1.map { h[$0] } // hull order (counter-clockwise in a y-down image = clockwise on screen); normalise to clockwise on screen
    let area = (0..<4).reduce(CGFloat(0)) { $0 + q[$1].x * q[($1 + 1) % 4].y - q[($1 + 1) % 4].x * q[$1].y }
    if area < 0 { q.reverse() }   // y-down: positive shoelace area = clockwise on screen
    // top edge = the shorter of the two opposite edge pairs, the one whose midpoint lies furthest along "up"
    func len(_ a: CGPoint, _ b: CGPoint) -> CGFloat { hypot(a.x - b.x, a.y - b.y) }
    let e0 = len(q[0], q[1]) + len(q[2], q[3]), e1 = len(q[1], q[2]) + len(q[3], q[0])
    let landscape = (spec["landscape"] as? Bool) ?? false   // laptops/monitors: the top edge is a LONG edge
    let cands = (e0 < e1) != landscape ? [0, 2] : [1, 3]
    func proj(_ k: Int) -> CGFloat { let m = CGPoint(x: (q[k].x + q[(k + 1) % 4].x) / 2, y: (q[k].y + q[(k + 1) % 4].y) / 2); return m.x * upHint.x + m.y * upHint.y }
    let top = proj(cands[0]) > proj(cands[1]) ? cands[0] : cands[1]
    q = (0..<4).map { q[(top + $0) % 4] }   // TL, TR, BR, BL
    let k = CGFloat(OW) / CGFloat(SW)
    q = q.map { CGPoint(x: ($0.x + 0.5) * k, y: ($0.y + 0.5) * k) }
    let cx = q.map { $0.x }.reduce(0, +) / 4, cy = q.map { $0.y }.reduce(0, +) / 4
    return q.map { CGPoint(x: cx + ($0.x - cx) * (1 + expand), y: cy + ($0.y - cy) * (1 + expand)) }
}

// ---------- reader
let asset = AVURLAsset(url: URL(fileURLWithPath: spec["clip"] as! String))
guard let vt = asset.tracks(withMediaType: .video).first else { fail("no video") }
let reader = try! AVAssetReader(asset: asset)
reader.timeRange = CMTimeRange(start: CMTime(seconds: tIn, preferredTimescale: 600), duration: CMTime(seconds: dur + 0.5, preferredTimescale: 600))
let ro = AVAssetReaderTrackOutput(track: vt, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(ro); reader.startReading()
let natural = vt.naturalSize.applying(vt.preferredTransform)
let srcW = abs(natural.width), srcH = abs(natural.height)
let fill = max(CGFloat(OW) / srcW, CGFloat(OH) / srcH)
let fitT = CGAffineTransform(scaleX: fill, y: fill).concatenating(CGAffineTransform(translationX: (CGFloat(OW) - srcW * fill) / 2, y: (CGFloat(OH) - srcH * fill) / 2))
var pending: (Double, CIImage)? = nil, current: CIImage? = nil
func nextSource() -> (Double, CIImage)? {
    guard let sb = ro.copyNextSampleBuffer(), let pb = CMSampleBufferGetImageBuffer(sb) else { return nil }
    let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sb)) - tIn
    var im = CIImage(cvPixelBuffer: pb).transformed(by: vt.preferredTransform)
    im = im.transformed(by: CGAffineTransform(translationX: -im.extent.minX, y: -im.extent.minY)).transformed(by: fitT)
    return (t, im.cropped(to: CGRect(x: 0, y: 0, width: OW, height: OH)))
}
func frameAt(_ t: Double) -> CIImage? {
    if pending == nil { pending = nextSource() }
    while let p = pending, p.0 <= t + 0.5 / fps { current = p.1; pending = nextSource() }
    return current
}

// ---------- writer
let outURL = URL(fileURLWithPath: spec["out"] as! String)
try? FileManager.default.removeItem(at: outURL)
let writer = try! AVAssetWriter(outputURL: outURL, fileType: .mov)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: OW, AVVideoHeightKey: OH,
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 24_000_000, AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel, AVVideoMaxKeyFrameIntervalKey: 6]])
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA, kCVPixelBufferWidthKey as String: OW, kCVPixelBufferHeightKey as String: OH])
writer.add(input); writer.startWriting(); writer.startSession(atSourceTime: .zero)

let grade = (spec["grade"] as? [String: Double]) ?? [:]
var smoothQ: Quad? = nil
let nFrames = Int(dur * fps)
var lost = 0
for i in 0..<nFrames {
    autoreleasepool {
        let t = Double(i) / fps
        guard let src = frameAt(t) else { return }
        var q: Quad? = nil
        if mode == "green" || mode == "white" {
            if let g = trackGreen(src) {
                if let s = smoothQ { q = (0..<4).map { CGPoint(x: s[$0].x * 0.35 + g[$0].x * 0.65, y: s[$0].y * 0.35 + g[$0].y * 0.65) } } else { q = g }
                smoothQ = q
            } else { q = smoothQ; lost += 1 }
        } else { q = quadAt(t) }
        var out = src
        if let q = q, let ui = uiAt(t) {
            // UI → quad (CI space is bottom-left origin)
            let f = CIFilter.perspectiveTransform()
            f.inputImage = ui.applyingFilter("CIColorMatrix", parameters: ["inputRVector": CIVector(x: uiGain, y: 0, z: 0, w: 0), "inputGVector": CIVector(x: 0, y: uiGain, z: 0, w: 0), "inputBVector": CIVector(x: 0, y: 0, z: uiGain, w: 0)])
            func ci(_ p: CGPoint) -> CGPoint { CGPoint(x: p.x, y: CGFloat(OH) - p.y) }
            f.topLeft = ci(q[0]); f.topRight = ci(q[1]); f.bottomRight = ci(q[2]); f.bottomLeft = ci(q[3])
            let warped = f.outputImage!
            if mode == "green" {
                cube.setValue(src, forKey: kCIInputImageKey)
                let keyed = cube.outputImage!
                // residual green the inscribed quad does not cover (e.g. under a thumb) reads as dark bezel, not green
                let bezel = CIImage(color: CIColor(red: 0.07, green: 0.07, blue: 0.08)).cropped(to: warped.extent.insetBy(dx: -60, dy: -60))
                out = keyed.composited(over: warped.composited(over: bezel.composited(over: src)))
            } else {
                out = warped.composited(over: src)
            }
            if debug { for p in q { out = CIImage(color: .red).cropped(to: CGRect(x: p.x - 6, y: CGFloat(OH) - p.y - 6, width: 12, height: 12)).composited(over: out) } }
        }
        // grade: warm, slightly contrasty, slightly less saturated (matched across all clips)
        let cc = CIFilter.colorControls(); cc.inputImage = out
        cc.saturation = Float(grade["saturation"] ?? 0.96); cc.contrast = Float(grade["contrast"] ?? 1.06); cc.brightness = Float(grade["brightness"] ?? 0)
        out = cc.outputImage!
        let tt = CIFilter.temperatureAndTint(); tt.inputImage = out
        tt.neutral = CIVector(x: 6500, y: 0); tt.targetNeutral = CIVector(x: CGFloat(grade["warmth"] ?? 5900), y: 0)
        out = tt.outputImage!.cropped(to: CGRect(x: 0, y: 0, width: OW, height: OH))
        while !input.isReadyForMoreMediaData { usleep(1000) }
        var pb: CVPixelBuffer?; CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &pb)
        ctx.render(out, to: pb!, bounds: CGRect(x: 0, y: 0, width: OW, height: OH), colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
        adaptor.append(pb!, withPresentationTime: CMTime(value: CMTimeValue(i), timescale: CMTimeScale(fps)))
    }
}
input.markAsFinished()
var done = false; writer.finishWriting { done = true }
while !done { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
print("wrote \(outURL.path)  \(nFrames) frames  tracking lost on \(lost) frames")
