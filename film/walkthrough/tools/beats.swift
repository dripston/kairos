// beats.swift: tempo, beat phase and per-bar energy for a music file (for phrase-accurate edits).
//   swift beats.swift file.wav
import AVFoundation
import Foundation
let f = try! AVAudioFile(forReading: URL(fileURLWithPath: CommandLine.arguments[1]))
let sr = f.processingFormat.sampleRate; let N = Int(f.length)
let buf = AVAudioPCMBuffer(pcmFormat: f.processingFormat, frameCapacity: AVAudioFrameCount(N))!
try! f.read(into: buf)
let L = buf.floatChannelData![0]; let R = f.processingFormat.channelCount > 1 ? buf.floatChannelData![1] : L
let hop = 441 // 10 ms
let nH = N / hop
var env = [Double](repeating: 0, count: nH)
// onset: positive diff of log energy in high-passed signal
var prev = 0.0, x1 = 0.0, y1 = 0.0
let a = exp(-2 * Double.pi * 150 / sr)
var lastE = 0.0
for h in 0..<nH {
  var e = 0.0
  for i in h*hop..<(h+1)*hop { let x = Double(L[i] + R[i]) / 2; let y = a * (y1 + x - x1); x1 = x; y1 = y; e += y*y }
  let le = log(e + 1e-9); env[h] = max(0, le - lastE); lastE = le; _ = prev; prev = le
}
// tempo via autocorrelation 60..180 bpm
var best = 0.0, bestLag = 0
for lag in 33...100 { var s = 0.0; for i in lag..<nH { s += env[i]*env[i-lag] }; s /= Double(nH-lag); if s > best { best = s; bestLag = lag } }
// refine fractional period with comb over whole track
var bestP = 0.0, bestScore = 0.0, bestPh = 0.0
var p = Double(bestLag) - 1.5
while p <= Double(bestLag) + 1.5 {
  for phI in 0..<Int(p) { let ph = Double(phI); var s = 0.0; var t = ph; while Int(t) < nH { s += env[Int(t)]; t += p }; if s > bestScore { bestScore = s; bestP = p; bestPh = ph } }
  p += 0.02
}
let beat = bestP * Double(hop) / sr
print(String(format: "beat %.4f s  bpm %.2f  first beat %.3f s  duration %.2f", beat, 60/beat, bestPh*Double(hop)/sr, Double(N)/sr))
// per-bar (4 beats) RMS dB and onset strength
var t = bestPh*Double(hop)/sr; var bar = 0
var line = ""
while t + 4*beat <= Double(N)/sr {
  let i0 = Int(t*sr), i1 = Int((t+4*beat)*sr); var s = 0.0
  for i in stride(from: i0, to: i1, by: 4) { let v = Double(L[i]+R[i])/2; s += v*v }
  let db = 10*log10(s/Double((i1-i0)/4) + 1e-12)
  line += String(format: "bar%3d %6.2fs %5.1fdB | ", bar, t, db)
  if bar % 4 == 3 { print(line); line = "" }
  t += 4*beat; bar += 1
}
print(line)
