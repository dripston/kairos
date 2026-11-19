// mix.swift: the films' soundtrack. Music edited at bar lines (never stretched), narration placed on the
// timeline, music ducked under the voice, BS.1770-4 integrated loudness normalised, true-peak limited.
//
//   swiftc -O mix.swift -o mix && ./mix spec.json
//
// spec.json:
// { "out": "audio/product_mix.wav", "duration": 115.0, "lufs": -14, "ceiling": -1.0,
//   "music": {"file": "...mp3", "segments": [{"src": 0.0, "len": 109.93, "at": 0.0, "fadeIn": 0, "fadeOut": 0.025}], "gain": 0},
//   "duck": {"db": 18, "attack": 0.12, "release": 0.45, "pre": 0.15},
//   "vo": [{"file": "audio/vo/p01.wav", "at": 0.4}, …], "voLevel": -20,
//   "sfx": [{"kind": "click", "at": 12.3}] }
// Prints a report: integrated loudness, true peak, voice coverage, music level under the voice vs in the gaps.
import AVFoundation
import Foundation

func fail(_ m: String) -> Never { FileHandle.standardError.write((m + "\n").data(using: .utf8)!); exit(1) }
let spec = try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))) as! [String: Any]
let SR = 44100.0
let dur = spec["duration"] as! Double
let N = Int(dur * SR)
var L = [Float](repeating: 0, count: N), R = [Float](repeating: 0, count: N)

func readAudio(_ path: String) -> ([Float], [Float]) {
    let f = try! AVAudioFile(forReading: URL(fileURLWithPath: path))
    let inFmt = f.processingFormat
    let n = AVAudioFrameCount(f.length)
    let buf = AVAudioPCMBuffer(pcmFormat: inFmt, frameCapacity: n)!
    try! f.read(into: buf)
    var l = Array(UnsafeBufferPointer(start: buf.floatChannelData![0], count: Int(buf.frameLength)))
    var r = inFmt.channelCount > 1 ? Array(UnsafeBufferPointer(start: buf.floatChannelData![1], count: Int(buf.frameLength))) : l
    if abs(inFmt.sampleRate - SR) > 1 { // resample (linear is fine for 48k/44.1k voice; music is 44.1k already)
        let ratio = inFmt.sampleRate / SR, m = Int(Double(l.count) / ratio)
        func rs(_ x: [Float]) -> [Float] { (0..<m).map { i in let p = Double(i) * ratio, a = Int(p), fr = Float(p - Double(a)); return a + 1 < x.count ? x[a] * (1 - fr) + x[a + 1] * fr : x[min(a, x.count - 1)] } }
        l = rs(l); r = rs(r)
    }
    return (l, r)
}
func db2g(_ d: Double) -> Float { Float(pow(10, d / 20)) }
func g2db(_ g: Double) -> Double { 20 * log10(max(g, 1e-12)) }

// ---------- biquads
struct Biquad { var b0, b1, b2, a1, a2: Double; var x1 = 0.0, x2 = 0.0, y1 = 0.0, y2 = 0.0
    mutating func run(_ x: Double) -> Double { let y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2; x2 = x1; x1 = x; y2 = y1; y1 = y; return y } }
func highpass(_ fc: Double, _ q: Double = 0.707) -> Biquad {
    let w = 2 * Double.pi * fc / SR, al = sin(w) / (2 * q), c = cos(w), a0 = 1 + al
    return Biquad(b0: (1 + c) / 2 / a0, b1: -(1 + c) / a0, b2: (1 + c) / 2 / a0, a1: -2 * c / a0, a2: (1 - al) / a0)
}
// BS.1770 K-weighting at any sample rate (pre-filter shelf + RLB high-pass), standard analog prototypes
func kWeight() -> (Biquad, Biquad) {
    let f0 = 1681.974450955533, G = 3.999843853973347, Q = 0.7071752369554196
    let K = tan(Double.pi * f0 / SR), Vh = pow(10, G / 20), Vb = pow(Vh, 0.4996667741545416)
    let a0 = 1 + K / Q + K * K
    let s = Biquad(b0: (Vh + Vb * K / Q + K * K) / a0, b1: 2 * (K * K - Vh) / a0, b2: (Vh - Vb * K / Q + K * K) / a0, a1: 2 * (K * K - 1) / a0, a2: (1 - K / Q + K * K) / a0)
    let f1 = 38.13547087602444, Q1 = 0.5003270373238773, K1 = tan(Double.pi * f1 / SR), d = 1 + K1 / Q1 + K1 * K1
    let h = Biquad(b0: 1, b1: -2, b2: 1, a1: 2 * (K1 * K1 - 1) / d, a2: (1 - K1 / Q1 + K1 * K1) / d)
    return (s, h)
}
func integratedLUFS(_ l: [Float], _ r: [Float]) -> Double {
    var (s1, h1) = kWeight(); var (s2, h2) = kWeight()
    let n = l.count; var zl = [Double](repeating: 0, count: n), zr = zl
    for i in 0..<n { zl[i] = h1.run(s1.run(Double(l[i]))); zr[i] = h2.run(s2.run(Double(r[i]))) }
    let blk = Int(0.4 * SR), hop = Int(0.1 * SR)
    var pre = [Double](repeating: 0, count: n + 1)
    for i in 0..<n { pre[i + 1] = pre[i] + zl[i] * zl[i] + zr[i] * zr[i] }
    var ms: [Double] = []
    var i = 0
    while i + blk <= n { ms.append((pre[i + blk] - pre[i]) / Double(blk)); i += hop }
    func lk(_ m: Double) -> Double { -0.691 + 10 * log10(max(m, 1e-15)) }
    let abs1 = ms.filter { lk($0) > -70 }
    if abs1.isEmpty { return -99 }
    let rel = lk(abs1.reduce(0, +) / Double(abs1.count)) - 10
    let g = abs1.filter { lk($0) > rel }
    return lk(g.reduce(0, +) / Double(g.count))
}
// 4× oversampled true-peak estimate per sample (windowed-sinc interpolation, 4 phases × 16 taps)
let tpTaps: [[Double]] = (1..<4).map { ph in
    let frac = Double(ph) / 4
    return (-8..<8).map { k in let x = Double(k) - frac; let s = x == 0 ? 1 : sin(Double.pi * x) / (Double.pi * x)
        let w = 0.5 * (1 + cos(Double.pi * x / 8.5)); return s * w }
}
func truePeak(_ x: [Float], _ i: Int) -> Double {
    var m = abs(Double(x[i]))
    for taps in tpTaps { var acc = 0.0; for (j, k) in (-8..<8).enumerated() { let idx = i + k + 1; if idx >= 0 && idx < x.count { acc += taps[j] * Double(x[idx]) } }; m = max(m, abs(acc)) }
    return m
}

// ---------- music
var musicL = [Float](repeating: 0, count: N), musicR = musicL
if let mu = spec["music"] as? [String: Any] {
    let (ml, mr) = readAudio(mu["file"] as! String)
    let mg = db2g((mu["gain"] as? Double) ?? 0)
    for seg in mu["segments"] as! [[String: Any]] {
        let src = seg["src"] as! Double, len = seg["len"] as! Double, at = seg["at"] as! Double
        let fi = (seg["fadeIn"] as? Double) ?? 0, fo = (seg["fadeOut"] as? Double) ?? 0.025
        let s0 = Int(src * SR), d0 = Int(at * SR), n = Int(len * SR)
        for k in 0..<n {
            let si = s0 + k, di = d0 + k; if si >= ml.count || di >= N || di < 0 { continue }
            let tt = Double(k) / SR
            var g = mg
            if fi > 0 && tt < fi { g *= Float(tt / fi) }
            if fo > 0 && tt > len - fo { g *= Float(max(0, (len - tt) / fo)) }
            musicL[di] += ml[si] * g; musicR[di] += mr[si] * g
        }
    }
}

// ---------- narration: trim, level, light high-pass, place
var voL = [Float](repeating: 0, count: N), voR = voL
var voIntervals: [(Double, Double)] = []
let voLevel = (spec["voLevel"] as? Double) ?? -20
var report: [String] = []
for v in (spec["vo"] as? [[String: Any]]) ?? [] {
    var (l, _) = readAudio(v["file"] as! String)
    // trim silence (−45 dBFS over 10 ms windows), keep 40 ms pads
    let w = Int(0.01 * SR), thr = db2g(-45)
    func loud(_ i: Int) -> Bool { var m: Float = 0; for k in i..<min(i + w, l.count) { m = max(m, abs(l[k])) }; return m > thr }
    var a = 0; while a < l.count && !loud(a) { a += w }
    var b = l.count - w; while b > a && !loud(b) { b -= w }
    a = max(0, a - Int(0.04 * SR)); b = min(l.count, b + w + Int(0.04 * SR))
    l = Array(l[a..<b])
    var hp = highpass(90); l = l.map { Float(hp.run(Double($0))) }
    // level: RMS of voiced 50 ms blocks → voLevel dBFS
    let bw = Int(0.05 * SR); var acc = 0.0, cnt = 0
    var i = 0; while i + bw <= l.count { var s = 0.0; for k in i..<i + bw { s += Double(l[k] * l[k]) }; let r = sqrt(s / Double(bw)); if r > 0.01 { acc += r * r; cnt += 1 }; i += bw }
    let rms = cnt > 0 ? sqrt(acc / Double(cnt)) : 0.1
    let g = Float(pow(10, voLevel / 20) / rms) * db2g((v["gain"] as? Double) ?? 0)
    let at = v["at"] as! Double, d0 = Int(at * SR)
    for k in 0..<l.count where d0 + k < N { voL[d0 + k] += l[k] * g; voR[d0 + k] += l[k] * g }
    let len = Double(l.count) / SR
    voIntervals.append((at, at + len))
    report.append(String(format: "  vo %@ at %.2f–%.2f (%.2f s)", (v["file"] as! NSString).lastPathComponent, at, at + len, len))
}

// ---------- sfx: a soft, short click (filtered noise + a damped 2.4 kHz tick), −30 dBFS
var sfxList = (spec["sfx"] as? [[String: Any]]) ?? []
if let cf = spec["clicks"] as? String, let d = FileManager.default.contents(atPath: cf), let arr = try? JSONSerialization.jsonObject(with: d) as? [Double] {
    sfxList += arr.map { ["kind": "click", "at": $0, "db": (spec["clickDb"] as? Double) ?? -30] }
}
for s in sfxList {
    let at = s["at"] as! Double, d0 = Int(at * SR), lv = db2g((s["db"] as? Double) ?? -30)
    var seed: UInt32 = UInt32(at * 1000)
    for k in 0..<Int(0.03 * SR) where d0 + k < N {
        seed = seed &* 1664525 &+ 1013904223
        let noise = Float(Int32(bitPattern: seed)) / Float(Int32.max)
        let tt = Float(k) / Float(SR)
        let env = exp(-tt * 260)
        let v = (0.55 * sin(2 * Float.pi * 2400 * tt) + 0.45 * noise) * env * lv
        L[d0 + k] += v; R[d0 + k] += v
    }
}

// ---------- ducking envelope from the narration intervals
let duck = (spec["duck"] as? [String: Any]) ?? [:]
let duckDb = (duck["db"] as? Double) ?? 18, att = (duck["attack"] as? Double) ?? 0.12, rel = (duck["release"] as? Double) ?? 0.45, pre = (duck["pre"] as? Double) ?? 0.15
var target = [Float](repeating: 1, count: N)
let low = db2g(-duckDb)
for (a, b) in voIntervals { let i0 = max(0, Int((a - pre) * SR)), i1 = min(N, Int((b + 0.05) * SR)); for i in i0..<i1 { target[i] = low } }
// smooth: attack/release one-pole in dB domain, run forwards
var env = [Float](repeating: 1, count: N)
var cur: Double = 0
let aC = exp(-1 / (att * SR)), rC = exp(-1 / (rel * SR))
for i in 0..<N { let tdb = g2db(Double(target[i])); let c = tdb < cur ? aC : rC; cur = tdb + c * (cur - tdb); env[i] = Float(pow(10, cur / 20)) }

for i in 0..<N { L[i] += musicL[i] * env[i] + voL[i]; R[i] += musicR[i] * env[i] + voR[i] }

// ---------- loudness normalise + true-peak limit
let targetLUFS = (spec["lufs"] as? Double) ?? -14, ceilDb = (spec["ceiling"] as? Double) ?? -1
var lufs = integratedLUFS(L, R)
var gain = db2g(targetLUFS - lufs)
for pass in 0..<3 {
    var l2 = L.map { $0 * gain }, r2 = R.map { $0 * gain }
    // lookahead limiter on the true peak
    let ceil = pow(10, ceilDb / 20) * 0.995
    var need = [Double](repeating: 1, count: N)
    for i in 0..<N { let p = max(truePeak(l2, i), truePeak(r2, i)); if p > ceil { need[i] = ceil / p } }
    let la = Int(0.005 * SR)
    var gmin = [Double](repeating: 1, count: N)
    // min over lookahead window
    var dq: [Int] = []
    for i in 0..<(N + la) {
        if i < N { while let last = dq.last, need[last] >= need[i] { dq.removeLast() }; dq.append(i) }
        let o = i - la; if o >= 0 { while let f = dq.first, f < o { dq.removeFirst() }; if let f = dq.first { gmin[o] = need[f] } }
    }
    var gcur = 1.0; let relC = exp(-1 / (0.08 * SR))
    for i in 0..<N { let t = gmin[i]; gcur = t < gcur ? t : t + relC * (gcur - t); l2[i] *= Float(gcur); r2[i] *= Float(gcur) }
    let got = integratedLUFS(l2, r2)
    if abs(got - targetLUFS) < 0.15 || pass == 2 {
        L = l2; R = r2; lufs = got; break
    }
    gain *= db2g(targetLUFS - got)
}
var tp = 0.0; for i in stride(from: 0, to: N, by: 1) { tp = max(tp, truePeak(L, i), truePeak(R, i)) }

// ---------- report
let voOn = voIntervals.reduce(0) { $0 + ($1.1 - $1.0) }
func rmsDb(_ x: [Float], _ ranges: [(Int, Int)]) -> Double { var s = 0.0, n = 0; for (a, b) in ranges { for i in a..<b { s += Double(x[i] * x[i]); n += 1 } }; return n > 0 ? 10 * log10(s / Double(n) + 1e-15) : -99 }
var under: [(Int, Int)] = [], gaps: [(Int, Int)] = []
var last = 0
for (a, b) in voIntervals.sorted(by: { $0.0 < $1.0 }) { let i0 = Int(a * SR), i1 = min(N, Int(b * SR)); if i0 > last + Int(0.6 * SR) { gaps.append((last + Int(0.5 * SR), i0 - Int(0.1 * SR))) }; under.append((i0 + Int(0.2 * SR), i1)); last = i1 }
let mixedMusic = (0..<N).map { musicL[$0] * env[$0] * gain }
print(String(format: "integrated %.2f LUFS (target %.1f)  true peak %.2f dBTP  duration %.2f s", lufs, targetLUFS, g2db(tp), dur))
print(String(format: "voice on %.1f s = %.0f%% of runtime, %d lines", voOn, 100 * voOn / dur, voIntervals.count))
print(String(format: "music under voice %.1f dB vs in gaps %.1f dB (duck %.0f dB)", rmsDb(mixedMusic, under), rmsDb(mixedMusic, gaps), duckDb))
report.forEach { print($0) }

// ---------- write 24-bit WAV
let outPath = spec["out"] as! String
let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: SR, channels: 2, interleaved: false)!
let ob = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(N))!
ob.frameLength = AVAudioFrameCount(N)
for i in 0..<N { ob.floatChannelData![0][i] = L[i]; ob.floatChannelData![1][i] = R[i] }
try? FileManager.default.removeItem(atPath: outPath)
do { // scoped so the file is closed (header written) before the process exits
    let of = try AVAudioFile(forWriting: URL(fileURLWithPath: outPath), settings: [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: SR, AVNumberOfChannelsKey: 2, AVLinearPCMBitDepthKey: 24, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false], commonFormat: .pcmFormatFloat32, interleaved: false)
    try of.write(from: ob)
} catch { fail("write: \(error)") }
print("wrote \(outPath)")
