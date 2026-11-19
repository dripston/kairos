// score_render.swift: render a score JSON (from score_compose.py) with macOS's bundled
// General MIDI DLS sampler, offline, then master it (glue compression, -16 LUFS, -1 dBFS limiter)
// and write a 44.1 kHz stereo 16-bit WAV.
//
//   swiftc -O film/tools/score_render.swift -o /tmp/score_render
//   /tmp/score_render score.json out.wav
//
// Graph: per track  sampler -> EQ (high-pass, 2.8 kHz vocal-pocket cut, low-pass) -> [delay] -> track mixer
//        track mixer -> dry bus  and  -> reverb bus (send level)   ;  reverb bus -> hall reverb (100% wet)
//        dry + reverb -> master mixer -> master EQ -> output.  Notes are fired between 64-frame renders.
import AVFoundation
import Foundation

struct DelayCfg: Decodable { let time: Double; let feedback: Float; let wet: Float; let lp: Float? }
struct TrackCfg: Decodable {
    let name: String
    let program: Int
    let perc: Bool?
    let volume: Float
    let pan: Float?
    let hp: Float?
    let lp: Float?
    let cut: Float?        // dB at 2.8 kHz (negative), default -3
    let reverb: Float?     // send level 0..1
    let delay: DelayCfg?
    let auto: [[Double]]?  // [[t, gain], ...] linear-interpolated gain multiplier
    let fx: String?        // if set, this track plays the synthesised fx bus of that name (no sampler)
    let dist: [Float]?     // [wetMix%, preGain dB]: AVAudioUnitDistortion saturation (braams)
}
struct FxEv: Decodable { let type: String; let t: Double; let t1: Double?; let gain: Double?; let bus: String; let f: Double? }
struct ScoreCfg: Decodable {
    let duration: Double
    let reverbPreset: String?
    let reverbLevel: Float?
    let lufs: Double?
    let tracks: [TrackCfg]
    let notes: [[Double]]   // [t, track, midi, vel, dur]
    let cc: [[Double]]?     // [t, track, cc, value]
    let fx: [FxEv]?
}

let args = CommandLine.arguments
guard args.count >= 3 else { print("usage: score_render score.json out.wav"); exit(2) }
let score = try JSONDecoder().decode(ScoreCfg.self, from: Data(contentsOf: URL(fileURLWithPath: args[1])))
let outURL = URL(fileURLWithPath: args[2])
let dls = URL(fileURLWithPath: "/System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls")
let sr = 44100.0
let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
let totalFrames = Int((score.duration * sr).rounded())

let engine = AVAudioEngine()
try engine.enableManualRenderingMode(.offline, format: fmt, maximumFrameCount: 4096)

let dryBus = AVAudioMixerNode(), revBus = AVAudioMixerNode(), master = AVAudioMixerNode()
let reverb = AVAudioUnitReverb()
let presets: [String: AVAudioUnitReverbPreset] = ["largeHall": .largeHall, "largeHall2": .largeHall2, "mediumHall": .mediumHall,
    "mediumHall2": .mediumHall2, "cathedral": .cathedral, "plate": .plate, "mediumRoom": .mediumRoom, "largeRoom": .largeRoom]
reverb.loadFactoryPreset(presets[score.reverbPreset ?? "largeHall"] ?? .largeHall)
reverb.wetDryMix = 100
let masterEQ = AVAudioUnitEQ(numberOfBands: 3)
for n in [dryBus, revBus, master, reverb, masterEQ] as [AVAudioNode] { engine.attach(n) }
// master EQ: rumble filter, gentle vocal-pocket dip, a touch of air
masterEQ.bands[0].filterType = .highPass; masterEQ.bands[0].frequency = 32; masterEQ.bands[0].bypass = false
masterEQ.bands[1].filterType = .parametric; masterEQ.bands[1].frequency = 2600; masterEQ.bands[1].bandwidth = 1.6
masterEQ.bands[1].gain = -2.5; masterEQ.bands[1].bypass = false
masterEQ.bands[2].filterType = .highShelf; masterEQ.bands[2].frequency = 9000; masterEQ.bands[2].gain = 1.5; masterEQ.bands[2].bypass = false

engine.connect(revBus, to: reverb, format: fmt)
engine.connect(reverb, to: master, fromBus: 0, toBus: master.nextAvailableInputBus, format: fmt)
engine.connect(dryBus, to: master, fromBus: 0, toBus: master.nextAvailableInputBus, format: fmt)
engine.connect(master, to: masterEQ, format: fmt)
engine.connect(masterEQ, to: engine.mainMixerNode, format: fmt)
revBus.outputVolume = score.reverbLevel ?? 1.0

// ---- synthesised trailer fx (booms, sub drops, thumps, risers, reversed crashes, ticks) ----
var fxBufs: [String: ([Float], [Float])] = [:]
var seed: UInt64 = 0x9E3779B97F4A7C15
func noise() -> Double { seed = seed &* 6364136223846793005 &+ 1442695040888963407; return Double(Int64(bitPattern: seed >> 11) & 0xFFFFFFFFFFFF) / Double(0xFFFFFFFFFFFF) * 2 - 1 }
func synth(_ e: FxEv) {
    var (bl, br) = fxBufs[e.bus] ?? ([Float](repeating: 0, count: totalFrames), [Float](repeating: 0, count: totalFrames))
    let g = e.gain ?? 1
    func add(_ i: Int, _ l: Double, _ r: Double) { if i >= 0 && i < totalFrames { bl[i] += Float(l); br[i] += Float(r) } }
    let s0 = Int(e.t * sr)
    switch e.type {
    case "boom", "subdrop", "thump":
        // pitch-dropping sine with saturation + a short low-passed noise transient
        let (fHi, fLo, glide, decay, drive, click): (Double, Double, Double, Double, Double, Double) =
            e.type == "boom" ? (95, e.f ?? 44, 0.3, 1.3, 2.2, 0.55) :
            e.type == "subdrop" ? (72, 27, 1.8, 1.8, 1.4, 0.0) : (120, e.f ?? 52, 0.09, 0.32, 2.8, 0.7)
        let n = Int((decay * 3.2) * sr)
        var ph = 0.0, lp = 0.0
        for k in 0..<n {
            let t = Double(k) / sr
            let f = fLo + (fHi - fLo) * exp(-t / (glide / 3))
            ph += 2 * Double.pi * f / sr
            let env = (1 - exp(-t / 0.002)) * exp(-t / decay)
            var y = tanh(drive * (sin(ph) + 0.25 * sin(2 * ph))) / tanh(drive) * env
            lp += 0.18 * (noise() - lp)
            y += click * lp * exp(-t / 0.012)
            add(s0 + k, g * y, g * y)
        }
    case "riser":
        // band-passed stereo noise sweeping up (SVF), amplitude rising, plus a faint rising tone; hard stop at t1
        let n = Int(((e.t1 ?? e.t + 2) - e.t) * sr)
        var lowL = 0.0, bandL = 0.0, lowR = 0.0, bandR = 0.0, ph = 0.0
        for k in 0..<n {
            let x = Double(k) / Double(n)
            let fc = 250 * pow(9000 / 250, x)
            let F = 2 * sin(Double.pi * min(fc, 12000) / sr), q = 0.35
            let inL = noise(), inR = noise()
            lowL += F * bandL; let hiL = inL - lowL - q * bandL; bandL += F * hiL
            lowR += F * bandR; let hiR = inR - lowR - q * bandR; bandR += F * hiR
            ph += 2 * Double.pi * (110 * pow(8, x)) / sr
            let env = pow(x, 2.2) * min(1, Double(n - k) / (0.008 * sr))
            add(s0 + k, g * env * (0.9 * bandL + 0.12 * sin(ph)), g * env * (0.9 * bandR + 0.12 * sin(ph * 1.003)))
        }
    case "revcrash":
        // reversed cymbal: high-passed noise with an exponentially rising envelope that ends at t1
        let n = Int(((e.t1 ?? e.t + 2) - e.t) * sr)
        var hpL = 0.0, hpR = 0.0, pL = 0.0, pR = 0.0, lpL = 0.0, lpR = 0.0
        for k in 0..<n {
            let rem = Double(n - k) / sr
            let env = exp(-rem / 0.55) * min(1, Double(n - k) / (0.004 * sr))
            let a = noise(), b = noise()
            hpL = 0.96 * (hpL + a - pL); pL = a; hpR = 0.96 * (hpR + b - pR); pR = b
            lpL += 0.6 * (hpL - lpL); lpR += 0.6 * (hpR - lpR)
            add(s0 + k, g * env * lpL, g * env * lpR)
        }
    case "tick":
        // clock tick kept above the voice band: high-passed click, 4 ms
        var hp = 0.0, p = 0.0
        for k in 0..<Int(0.03 * sr) {
            let t = Double(k) / sr; let a = noise()
            hp = 0.7 * (hp + a - p); p = a
            let y = g * hp * exp(-t / 0.004)
            add(s0 + k, y * 0.8, y)
        }
    case "braam":
        // detuned saw stack, low-passed with an opening envelope, saturated (layered under DLS brass)
        let n = Int(((e.t1 ?? e.t + 3) - e.t) * sr)
        let f0 = e.f ?? 73.42
        var phs = [Double](repeating: 0, count: 6), lp1 = 0.0, lp2 = 0.0
        let det = [1.0, 1.006, 0.994, 1.5, 1.497, 2.0]
        for k in 0..<n {
            let t = Double(k) / sr
            var x = 0.0
            for v in 0..<6 { phs[v] += f0 * det[v] / sr; if phs[v] >= 1 { phs[v] -= 1 }; x += (2 * phs[v] - 1) * (v < 3 ? 1 : 0.55) }
            let fc = 180 + 900 * exp(-t / 0.5) * (1 - exp(-t / 0.05))
            let a = 1 - exp(-2 * Double.pi * fc / sr)
            lp1 += a * (x - lp1); lp2 += a * (lp1 - lp2)
            let env = (1 - exp(-t / 0.02)) * exp(-t / 1.4) * min(1, Double(n - k) / (0.05 * sr))
            let y = g * tanh(1.8 * lp2 / 3) * env
            add(s0 + k, y, y)
        }
    default: print("unknown fx \(e.type)")
    }
    fxBufs[e.bus] = (bl, br)
}
for e in score.fx ?? [] { synth(e) }

var samplers: [AVAudioUnitSampler?] = []
var players: [AVAudioPlayerNode] = []
var trackMixers: [AVAudioMixerNode] = []
for t in score.tracks {
    let src: AVAudioNode
    var s: AVAudioUnitSampler? = nil
    if t.fx != nil { let p = AVAudioPlayerNode(); src = p; players.append(p) } else { s = AVAudioUnitSampler(); src = s! }
    let eq = AVAudioUnitEQ(numberOfBands: 3)
    let tm = AVAudioMixerNode()
    engine.attach(src); engine.attach(eq); engine.attach(tm)
    eq.bands[0].filterType = .highPass; eq.bands[0].frequency = t.hp ?? 30; eq.bands[0].bypass = false
    eq.bands[1].filterType = .parametric; eq.bands[1].frequency = 2800; eq.bands[1].bandwidth = 1.4
    eq.bands[1].gain = t.cut ?? -3; eq.bands[1].bypass = false
    eq.bands[2].filterType = .lowPass; eq.bands[2].frequency = t.lp ?? 16000; eq.bands[2].bypass = false
    if let ds = t.dist {
        let du = AVAudioUnitDistortion(); engine.attach(du)
        du.loadFactoryPreset(.multiDistortedCubed); du.wetDryMix = ds[0]; du.preGain = ds.count > 1 ? ds[1] : -6
        engine.connect(src, to: du, format: fmt); engine.connect(du, to: eq, format: fmt)
    } else {
        engine.connect(src, to: eq, format: fmt)
    }
    if let d = t.delay {
        let dl = AVAudioUnitDelay()
        engine.attach(dl)
        dl.delayTime = d.time; dl.feedback = d.feedback; dl.wetDryMix = d.wet; dl.lowPassCutoff = d.lp ?? 6000
        engine.connect(eq, to: dl, format: fmt)
        engine.connect(dl, to: tm, format: fmt)
    } else {
        engine.connect(eq, to: tm, format: fmt)
    }
    let dBus = dryBus.nextAvailableInputBus, rBus = revBus.nextAvailableInputBus
    engine.connect(tm, to: [AVAudioConnectionPoint(node: dryBus, bus: dBus), AVAudioConnectionPoint(node: revBus, bus: rBus)],
                   fromBus: 0, format: fmt)
    if let dd = tm.destination(forMixer: dryBus, bus: dBus) { dd.volume = 1; dd.pan = t.pan ?? 0 }
    if let rd = tm.destination(forMixer: revBus, bus: rBus) { rd.volume = t.reverb ?? 0.25; rd.pan = t.pan ?? 0 }
    tm.outputVolume = t.volume
    if let s = s {
        try s.loadSoundBankInstrument(at: dls, program: UInt8(t.program),
            bankMSB: UInt8((t.perc ?? false) ? kAUSampler_DefaultPercussionBankMSB : kAUSampler_DefaultMelodicBankMSB),
            bankLSB: UInt8(kAUSampler_DefaultBankLSB))
    }
    samplers.append(s); trackMixers.append(tm)
}

// ---- event list ----
enum Kind { case on, off, cc }
struct Ev { let frame: Int; let kind: Kind; let track: Int; let a: UInt8; let b: UInt8; let gen: Int }
var evs: [Ev] = []
var gen = 0
for n in score.notes {
    let tr = Int(n[1]); let note = UInt8(max(0, min(127, Int(n[2])))); let vel = UInt8(max(1, min(127, Int(n[3].rounded()))))
    let f0 = max(0, Int((n[0] * sr).rounded())); let f1 = max(f0 + 32, Int(((n[0] + n[4]) * sr).rounded()))
    gen += 1
    evs.append(Ev(frame: f0, kind: .on, track: tr, a: note, b: vel, gen: gen))
    evs.append(Ev(frame: f1, kind: .off, track: tr, a: note, b: 0, gen: gen))
}
for c in score.cc ?? [] {
    evs.append(Ev(frame: max(0, Int((c[0] * sr).rounded())), kind: .cc, track: Int(c[1]), a: UInt8(c[2]), b: UInt8(max(0, min(127, Int(c[3])))), gen: 0))
}
evs.sort { $0.frame != $1.frame ? $0.frame < $1.frame : ($0.kind == .off && $1.kind != .off) }

func autoGain(_ pts: [[Double]], _ t: Double) -> Float {
    if pts.isEmpty { return 1 }
    if t <= pts[0][0] { return Float(pts[0][1]) }
    if t >= pts.last![0] { return Float(pts.last![1]) }
    var lo = 0, hi = pts.count - 1          // binary search: pts[lo][0] < t <= pts[hi][0]
    while hi - lo > 1 { let m = (lo + hi) / 2; if pts[m][0] < t { lo = m } else { hi = m } }
    let (t0, g0, t1, g1) = (pts[lo][0], pts[lo][1], pts[hi][0], pts[hi][1])
    return Float(g0 + (g1 - g0) * (t - t0) / max(1e-9, t1 - t0))
}

try engine.start()
var pi = 0
for t in score.tracks where t.fx != nil {
    let p = players[pi]; pi += 1
    let (bl, br) = fxBufs[t.fx!] ?? ([Float](repeating: 0, count: totalFrames), [Float](repeating: 0, count: totalFrames))
    let pb = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(totalFrames))!
    pb.frameLength = AVAudioFrameCount(totalFrames)
    for i in 0..<totalFrames { pb.floatChannelData![0][i] = bl[i]; pb.floatChannelData![1][i] = br[i] }
    p.scheduleBuffer(pb, at: nil, options: [], completionHandler: nil)
    p.play()
}
let chunk = 64
let buf = AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: 4096)!
var L = [Float](repeating: 0, count: totalFrames), R = [Float](repeating: 0, count: totalFrames)
var active: [Int: Int] = [:]   // key track*128+note -> gen of the sounding note
var ei = 0, frame = 0
let t0 = Date()
while frame < totalFrames {
    while ei < evs.count && evs[ei].frame <= frame {
        let e = evs[ei]; ei += 1
        guard let s = samplers[e.track] else { continue }; let key = e.track * 128 + Int(e.a)
        switch e.kind {
        case .on:
            if active[key] != nil { s.stopNote(e.a, onChannel: 0) }
            s.startNote(e.a, withVelocity: e.b, onChannel: 0); active[key] = e.gen
        case .off:
            if active[key] == e.gen { s.stopNote(e.a, onChannel: 0); active[key] = nil }
        case .cc:
            s.sendController(e.a, withValue: e.b, onChannel: 0)
        }
    }
    let t = Double(frame) / sr
    for (i, tc) in score.tracks.enumerated() where tc.auto != nil {
        trackMixers[i].outputVolume = tc.volume * autoGain(tc.auto!, t)
    }
    let n = min(chunk, totalFrames - frame)
    let st = try engine.renderOffline(AVAudioFrameCount(n), to: buf)
    guard st == .success else { print("render status \(st.rawValue)"); exit(1) }
    let l = buf.floatChannelData![0], r = buf.floatChannelData![1]
    for i in 0..<n { L[frame + i] = l[i]; R[frame + i] = r[i] }
    frame += n
}
engine.stop()
print(String(format: "rendered %.2f s in %.1f s", score.duration, Date().timeIntervalSince(t0)))

// ---- mastering ----
func biquad(_ x: inout [Float], _ b: [Double], _ a: [Double]) {
    var x1 = 0.0, x2 = 0.0, y1 = 0.0, y2 = 0.0
    for i in 0..<x.count {
        let xi = Double(x[i])
        let y = b[0]*xi + b[1]*x1 + b[2]*x2 - a[1]*y1 - a[2]*y2
        x2 = x1; x1 = xi; y2 = y1; y1 = y; x[i] = Float(y)
    }
}
func kWeight(_ x: [Float]) -> [Float] {
    var y = x
    // BS.1770 pre-filter (high shelf) and RLB high-pass, re-derived for 44.1 kHz
    var f0 = 1681.974450955533; let G = 3.999843853973347; var Q = 0.7071752369554196
    var K = tan(Double.pi * f0 / sr); let Vh = pow(10, G/20), Vb = pow(Vh, 0.4996667741545416)
    var a0 = 1 + K/Q + K*K
    biquad(&y, [(Vh + Vb*K/Q + K*K)/a0, 2*(K*K - Vh)/a0, (Vh - Vb*K/Q + K*K)/a0], [1, 2*(K*K - 1)/a0, (1 - K/Q + K*K)/a0])
    f0 = 38.13547087602444; Q = 0.5003270373238773; K = tan(Double.pi * f0 / sr); a0 = 1 + K/Q + K*K
    biquad(&y, [1, -2, 1], [1, 2*(K*K - 1)/a0, (1 - K/Q + K*K)/a0])
    return y
}
func lufs(_ l: [Float], _ r: [Float]) -> Double {
    let kl = kWeight(l), kr = kWeight(r)
    let blk = Int(0.4 * sr), hop = Int(0.1 * sr)
    var ms: [Double] = []
    var i = 0
    while i + blk <= kl.count {
        var s = 0.0
        for j in i..<(i+blk) { s += Double(kl[j]*kl[j] + kr[j]*kr[j]) }
        ms.append(s / Double(blk)); i += hop
    }
    let abs = ms.filter { -0.691 + 10*log10($0 + 1e-12) > -70 }
    let mean1 = abs.reduce(0, +) / Double(max(1, abs.count))
    let rel = -0.691 + 10*log10(mean1 + 1e-12) - 10
    let gated = abs.filter { -0.691 + 10*log10($0 + 1e-12) > rel }
    return -0.691 + 10*log10(gated.reduce(0, +) / Double(max(1, gated.count)) + 1e-12)
}
func gain(_ x: inout [Float], _ g: Float) { for i in 0..<x.count { x[i] *= g } }
func peak(_ l: [Float], _ r: [Float]) -> Float { var p: Float = 0; for i in 0..<l.count { p = max(p, abs(l[i]), abs(r[i])) }; return p }

// fades: 5 ms in, last 1.2 s out (the reverb tail is already decaying)
for i in 0..<220 { let g = Float(i)/220; L[i] *= g; R[i] *= g }
let fo = Int(1.2 * sr)
for i in 0..<fo { let g = Float(cos(Double(i)/Double(fo) * Double.pi/2)); let k = totalFrames - fo + i; L[k] *= g; R[k] *= g }

let target = score.lufs ?? -16
let raw = lufs(L, R)
print(String(format: "raw: %.2f LUFS, peak %.2f dBFS", raw, 20*log10(Double(peak(L, R)))))
var g0 = Float(pow(10, (-19 - raw)/20)); gain(&L, g0); gain(&R, g0)

// glue compressor: stereo-linked RMS detector, 2:1 above -17 dBFS, 6 dB knee, 25 ms / 250 ms
do {
    let thr = -17.0, ratio = 2.0, knee = 6.0
    let aA = exp(-1/(0.025*sr)), aR = exp(-1/(0.25*sr))
    var env = 0.0
    var maxGR = 0.0
    for i in 0..<totalFrames {
        let p = Double(L[i]*L[i] + R[i]*R[i]) * 0.5
        env = p > env ? aA*env + (1-aA)*p : aR*env + (1-aR)*p
        let lvl = 10*log10(env + 1e-12)
        var over = lvl - thr, gr = 0.0
        if over > knee/2 { gr = over * (1 - 1/ratio) } else if over > -knee/2 { over += knee/2; gr = (1 - 1/ratio) * over*over / (2*knee) }
        maxGR = max(maxGR, gr)
        let g = Float(pow(10, -gr/20)); L[i] *= g; R[i] *= g
    }
    print(String(format: "glue comp: max gain reduction %.1f dB", maxGR))
}

// look-ahead peak limiter at -1 dBFS
func limit(_ l: inout [Float], _ r: inout [Float], ceiling: Float) -> Float {
    let n = l.count, la = Int(0.004 * sr)
    var req = [Float](repeating: 1, count: n)
    for i in 0..<n { let p = max(abs(l[i]), abs(r[i])); if p > ceiling { req[i] = ceiling / p } }
    // forward-looking minimum over la samples (monotone deque)
    var m = [Float](repeating: 1, count: n)
    var dq: [Int] = []; var head = 0
    for i in stride(from: n-1, through: 0, by: -1) {
        while dq.count > head && req[dq.last!] >= req[i] { dq.removeLast() }
        dq.append(i)
        while dq[head] > i + la { head += 1 }
        m[i] = req[dq[head]]
        if head > 4096 { dq.removeFirst(head); head = 0 }
    }
    let rel = Float(exp(-1/(0.12*sr)))
    var e: Float = 1; var minG: Float = 1
    var env = [Float](repeating: 1, count: n)
    for i in 0..<n { e = min(m[i], rel*e + (1-rel)*1); env[i] = e }
    var acc: Float = 0
    var out = [Float](repeating: 1, count: n)
    for i in 0..<n { acc += env[i]; if i >= la { acc -= env[i-la] }; out[i] = acc / Float(min(i+1, la)) }
    for i in 0..<n { let g = out[i]; l[i] *= g; r[i] *= g; minG = min(minG, g) }
    return minG
}
let ceiling = Float(pow(10, -1.0/20))
var cur = lufs(L, R)
var gr: Float = 1
for _ in 0..<2 {
    let g = Float(pow(10, (target - cur)/20)); gain(&L, g); gain(&R, g)
    gr = limit(&L, &R, ceiling: ceiling * 0.995)
    cur = lufs(L, R)
}
let pk = peak(L, R)
print(String(format: "master: %.2f LUFS integrated, sample peak %.2f dBFS, limiter max GR %.1f dB", cur, 20*log10(Double(pk)), -20*log10(Double(gr))))

// ---- write 16-bit WAV with TPDF dither ----
let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: sr, AVNumberOfChannelsKey: 2,
    AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false, AVLinearPCMIsNonInterleaved: false]
try? FileManager.default.removeItem(at: outURL)
let file = try AVAudioFile(forWriting: outURL, settings: settings, commonFormat: .pcmFormatInt16, interleaved: false)
let ifmt = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: sr, channels: 2, interleaved: false)!
let ob = AVAudioPCMBuffer(pcmFormat: ifmt, frameCapacity: AVAudioFrameCount(totalFrames))!
ob.frameLength = AVAudioFrameCount(totalFrames)
var rng = SystemRandomNumberGenerator()
func q(_ x: Float) -> Int16 {
    let d = (Float.random(in: -0.5...0.5, using: &rng) + Float.random(in: -0.5...0.5, using: &rng))
    return Int16(max(-32768, min(32767, (x * 32767 + d).rounded())))
}
for i in 0..<totalFrames { ob.int16ChannelData![0][i] = q(L[i]); ob.int16ChannelData![1][i] = q(R[i]) }
try file.write(from: ob)
file.close()
print("wrote \(outURL.path) frames=\(totalFrames)")
