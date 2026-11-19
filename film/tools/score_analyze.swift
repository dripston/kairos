// score_analyze.swift: verification numbers for a score WAV.
//   score_analyze file.wav [t1 t2 ...]
// Prints duration, sample peak, integrated loudness (BS.1770, gated), clipped-sample count,
// a 1 s RMS timeline, RMS in 1 s windows before/after each given marker, and the share of
// energy in the 1-4 kHz voice band (whole track) for a "voice gap" sanity check.
import AVFoundation
import Foundation

let a = CommandLine.arguments
let f = try AVAudioFile(forReading: URL(fileURLWithPath: a[1]))
let sr = f.processingFormat.sampleRate
let N = Int(f.length)
let buf = AVAudioPCMBuffer(pcmFormat: f.processingFormat, frameCapacity: AVAudioFrameCount(N))!
try f.read(into: buf)
let L = Array(UnsafeBufferPointer(start: buf.floatChannelData![0], count: N))
let R = f.processingFormat.channelCount > 1 ? Array(UnsafeBufferPointer(start: buf.floatChannelData![1], count: N)) : L

func db(_ x: Double) -> Double { 20 * log10(max(x, 1e-9)) }
func rms(_ i0: Int, _ i1: Int, _ l: [Float], _ r: [Float]) -> Double {
    var s = 0.0; let a = max(0, i0), b = min(N, i1)
    if b <= a { return 0 }
    for i in a..<b { s += Double(l[i]*l[i] + r[i]*r[i]) / 2 }
    return sqrt(s / Double(b - a))
}
func biquad(_ x: [Float], _ b: [Double], _ a: [Double]) -> [Float] {
    var y = x; var x1 = 0.0, x2 = 0.0, y1 = 0.0, y2 = 0.0
    for i in 0..<x.count { let xi = Double(x[i]); let o = b[0]*xi + b[1]*x1 + b[2]*x2 - a[1]*y1 - a[2]*y2
        x2 = x1; x1 = xi; y2 = y1; y1 = o; y[i] = Float(o) }
    return y
}
func rbj(_ type: String, _ f0: Double, _ q: Double = 0.7071) -> ([Double], [Double]) {
    let w = 2 * Double.pi * f0 / sr, c = cos(w), al = sin(w) / (2 * q)
    let a0 = 1 + al
    if type == "lp" { return ([(1-c)/2/a0, (1-c)/a0, (1-c)/2/a0], [1, -2*c/a0, (1-al)/a0]) }
    return ([(1+c)/2/a0, -(1+c)/a0, (1+c)/2/a0], [1, -2*c/a0, (1-al)/a0])
}
func band(_ x: [Float]) -> [Float] {
    var y = x
    for _ in 0..<2 { let h = rbj("hp", 1000); y = biquad(y, h.0, h.1) }
    for _ in 0..<2 { let l = rbj("lp", 4000); y = biquad(y, l.0, l.1) }
    return y
}
// loudness (BS.1770-4 K-weighting at 44.1 kHz)
func kw(_ x: [Float]) -> [Float] {
    var K = tan(Double.pi * 1681.974450955533 / sr); var Q = 0.7071752369554196
    let Vh = pow(10, 3.999843853973347/20), Vb = pow(Vh, 0.4996667741545416)
    var a0 = 1 + K/Q + K*K
    var y = biquad(x, [(Vh + Vb*K/Q + K*K)/a0, 2*(K*K - Vh)/a0, (Vh - Vb*K/Q + K*K)/a0], [1, 2*(K*K - 1)/a0, (1 - K/Q + K*K)/a0])
    K = tan(Double.pi * 38.13547087602444 / sr); Q = 0.5003270373238773; a0 = 1 + K/Q + K*K
    y = biquad(y, [1, -2, 1], [1, 2*(K*K - 1)/a0, (1 - K/Q + K*K)/a0])
    return y
}
let kl = kw(L), kr = kw(R)
var ms: [Double] = []
var i = 0; let blk = Int(0.4*sr), hop = Int(0.1*sr)
while i + blk <= N { var s = 0.0; for j in i..<(i+blk) { s += Double(kl[j]*kl[j] + kr[j]*kr[j]) }; ms.append(s/Double(blk)); i += hop }
let g1 = ms.filter { -0.691 + 10*log10($0+1e-12) > -70 }
let rel = -0.691 + 10*log10(g1.reduce(0,+)/Double(g1.count)) - 10
let g2 = g1.filter { -0.691 + 10*log10($0+1e-12) > rel }
let lufs = -0.691 + 10*log10(g2.reduce(0,+)/Double(g2.count))
var pk: Float = 0; var clip = 0
for i in 0..<N { let p = max(abs(L[i]), abs(R[i])); pk = max(pk, p); if p >= 0.9999 { clip += 1 } }
print(String(format: "file %@\nduration %.4f s (%d frames)  peak %.2f dBFS  integrated %.2f LUFS  RMS %.2f dBFS  clipped samples %d",
             (a[1] as NSString).lastPathComponent, Double(N)/sr, N, db(Double(pk)), lufs, db(rms(0, N, L, R)), clip))
let bl = band(L), br = band(R)
let full = rms(0, N, L, R), bnd = rms(0, N, bl, br)
print(String(format: "voice band 1-4 kHz: %.1f%% of energy (%.1f dB below full band)", 100 * bnd*bnd/(full*full), db(full) - db(bnd)))
var st = "short-term loudness (3 s window, LUFS) every 2 s:"
for s in stride(from: 0, to: Int(Double(N)/sr) - 2, by: 2) {
    if s % 20 == 0 { st += "\n  \(s)s:" }
    let i0 = Int(Double(s)*sr), i1 = min(N, i0 + Int(3*sr)); var e = 0.0
    for j in i0..<i1 { e += Double(kl[j]*kl[j] + kr[j]*kr[j]) }
    st += String(format: " %.0f", -0.691 + 10*log10(e/Double(i1-i0) + 1e-12))
}
print(st)
var line = "1 s RMS dBFS:"
for s in 0..<Int(ceil(Double(N)/sr)) { if s % 10 == 0 { line += "\n  \(s)s:" }; line += String(format: " %.0f", db(rms(Int(Double(s)*sr), Int(Double(s+1)*sr), L, R))) }
print(line)
for m in a.dropFirst(2) where m.contains("-") && !m.hasPrefix("-") {
    // range "a-b": RMS and K-weighted loudness over that span (e.g. a dialogue scene)
    let ab = m.split(separator: "-").map { Double($0)! }
    let i0 = Int(ab[0]*sr), i1 = min(N, Int(ab[1]*sr)); var e = 0.0
    for j in i0..<i1 { e += Double(kl[j]*kl[j] + kr[j]*kr[j]) }
    print(String(format: "range %7.2f-%7.2f s: RMS %.1f dBFS, loudness %.1f LUFS (ungated)", ab[0], ab[1],
                 db(rms(i0, i1, L, R)), -0.691 + 10*log10(e/Double(i1-i0) + 1e-12)))
}
for m in a.dropFirst(2) where !m.contains("-") {
    let t = Double(m)!; let c = Int(t*sr), w = Int(sr)
    let before = rms(c - w, c, L, R), after = rms(c, c + w, L, R)
    let bb = rms(c - w, c, bl, br), ab = rms(c, c + w, bl, br)
    print(String(format: "marker %7.2f s: RMS 1 s before %.1f dBFS, 1 s after %.1f dBFS (%+.1f dB); 100 ms after %.1f dBFS; 1-4 kHz share after %.0f%%",
                 t, db(before), db(after), db(after) - db(before), db(rms(c, c + Int(0.1*sr), L, R)), 100*ab*ab/max(1e-12, after*after)))
    _ = bb
}
