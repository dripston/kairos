#!/usr/bin/env python3
"""Compose the Kairos film score variants as note/fx event lists (JSON) for score_render.swift.

    python3 film/tools/score_compose.py v3 out.json [hits.json]

All timing comes from audio/vo_neha/timeline.json (runtime 139.635 s). A piecewise tempo map puts
every scripted hit on a downbeat, so the groove and the hits stay locked together.
Original composition; instruments are the Apple General MIDI DLS bundled with macOS.
"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TL = json.load(open(os.path.join(HERE, "..", "audio", "vo_neha", "timeline.json")))
RUNTIME = TL["runtime_s"]

PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def n(s):
    """'C#4' / 'Bb2' -> MIDI number (C4 = 60)."""
    p = PC[s[0]]
    i = 1
    while i < len(s) and s[i] in "#b":
        p += 1 if s[i] == "#" else -1
        i += 1
    return p + 12 * (int(s[i:]) + 1)


QUAL = {"": [0, 4, 7], "m": [0, 3, 7], "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10],
        "sus4": [0, 5, 7], "sus2": [0, 2, 7], "add9": [0, 4, 7, 2], "madd9": [0, 3, 7, 2], "maj9": [0, 4, 7, 11, 2],
        "m9": [0, 3, 7, 10, 2], "7sus4": [0, 5, 7, 10], "6": [0, 4, 7, 9], "5": [0, 7], "m6": [0, 3, 7, 9]}


def parse(sym):
    """'Bbmaj7/D' -> (root pc, [chord pcs], bass pc)."""
    main, _, bass = sym.partition("/")
    r = PC[main[0]]
    i = 1
    while i < len(main) and main[i] in "#b":
        r += 1 if main[i] == "#" else -1
        i += 1
    q = QUAL[main[i:]]
    pcs = [(r + x) % 12 for x in q]
    b = r % 12
    if bass:
        b = (PC[bass[0]] + sum(1 if c == "#" else -1 for c in bass[1:])) % 12
    return r % 12, pcs, b


def low(pc, lo):
    """lowest MIDI note with pitch class pc at or above lo."""
    return lo + ((pc - lo) % 12)


def voicing(sym, lo, hi, center=None):
    """close-position voicing of the chord inside [lo, hi], inversion chosen nearest to `center`."""
    _, pcs, _ = parse(sym)
    center = center if center is not None else (lo + hi) / 2
    best = None
    for rot in range(len(pcs)):
        order = pcs[rot:] + pcs[:rot]
        for base in range(lo, lo + 12):
            v, cur = [], base - 1
            for pc in order:
                cur = cur + 1 + ((pc - (cur + 1)) % 12)
                v.append(cur)
            if v[0] < lo or v[-1] > hi:
                continue
            d = abs(sum(v) / len(v) - center)
            if best is None or d < best[0]:
                best = (d, v)
    return best[1] if best else [low(p, lo) for p in pcs]


class Score:
    def __init__(self, anchors, seed=7, runtime=None):
        self.runtime = runtime or RUNTIME
        self.anchors = anchors  # [(beat, seconds)] increasing
        self.rng = random.Random(seed)
        self.tracks, self.notes, self.cc, self.fx = [], [], [], []

    def T(self, beat):
        a = self.anchors
        if beat <= a[0][0]:
            (b0, t0), (b1, t1) = a[0], a[1]
        elif beat >= a[-1][0]:
            (b0, t0), (b1, t1) = a[-2], a[-1]
        else:
            k = next(i for i in range(1, len(a)) if beat <= a[i][0])
            (b0, t0), (b1, t1) = a[k - 1], a[k]
        return t0 + (beat - b0) * (t1 - t0) / (b1 - b0)

    def track(self, name, program, **kw):
        d = {"name": name, "program": program, "volume": kw.pop("volume", 0.8)}
        d.update(kw)
        self.tracks.append(d)
        return len(self.tracks) - 1

    def note(self, tr, beat, pitch, vel, dur, hum_ms=6, vh=8):
        """beat-timed note with humanised timing (+-hum_ms) and velocity (+-vh)."""
        t0 = self.T(beat)
        d = self.T(beat + dur) - t0
        j = max(-hum_ms, min(hum_ms, self.rng.gauss(0, hum_ms / 2.2))) / 1000 if hum_ms else 0
        v = vel + (self.rng.randint(-vh, vh) if vh else 0)
        self.add(tr, t0 + j, pitch, v, d)

    def add(self, tr, t, pitch, vel, dur):
        if t + dur <= 0 or t >= self.runtime:
            return
        if t < 0:
            dur += t
            t = 0
        self.notes.append([round(t, 5), tr, int(pitch), max(1, min(127, int(round(vel)))), round(dur, 5)])

    def chord(self, tr, beat, pitches, vel, dur, spread_ms=12, **kw):
        for i, p in enumerate(sorted(pitches)):
            self.note(tr, beat + 0, p, vel - 2 * i, dur, **kw)

    def fxev(self, typ, t, bus, gain=1.0, t1=None, f=None):
        e = {"type": typ, "t": round(t, 5), "bus": bus, "gain": gain}
        if t1 is not None:
            e["t1"] = round(t1, 5)
        if f is not None:
            e["f"] = f
        self.fx.append(e)

    def mute(self, trs, t0, t1, ramp=0.03):
        """drop notes of the given tracks starting inside [t0, t1) and dip their volume to 0 there."""
        self.notes = [x for x in self.notes if not (x[1] in trs and t0 <= x[0] < t1)]
        for tr in trs:
            pts = self.tracks[tr].setdefault("auto", [[0, 1]])
            pts += [[t0 - ramp, 1], [t0, 0.0], [t1, 0.0], [t1 + ramp, 1]]

    master = None  # optional [(t, gain)] curve multiplied into every track's automation

    @staticmethod
    def interp(pts, t):
        if t <= pts[0][0]:
            return pts[0][1]
        for (t0, g0), (t1, g1) in zip(pts, pts[1:]):
            if t <= t1:
                return g0 + (g1 - g0) * (t - t0) / max(1e-9, t1 - t0)
        return pts[-1][1]

    @staticmethod
    def interp_fast(pts, t):
        import bisect
        keys = [p[0] for p in pts] if not hasattr(pts, "_k") else pts._k
        i = bisect.bisect_left(keys, t)
        if i == 0:
            return pts[0][1]
        if i >= len(pts):
            return pts[-1][1]
        (t0, g0), (t1, g1) = pts[i - 1], pts[i]
        return g0 + (g1 - g0) * (t - t0) / max(1e-9, t1 - t0)

    def pump(self, tr, beats, depth=0.5, rec=0.2):
        """sidechain-style duck on every given beat (volume automation)."""
        pts = self.tracks[tr].setdefault("auto", [[0, 1]])
        for b in beats:
            t = self.T(b)
            pts += [[round(t - 0.004, 4), 1.0], [round(t + 0.012, 4), depth], [round(t + rec, 4), 1.0]]

    def dump(self, path, **kw):
        for t in self.tracks:
            if "auto" in t:
                t["auto"] = sorted(t["auto"], key=lambda p: p[0])
        if self.master:
            base = [round(k * 0.05, 3) for k in range(int(self.runtime / 0.05) + 1)] + [p[0] for p in self.master]
            for t in self.tracks:
                own = t.get("auto", [[0, 1]])
                grid = sorted(set(base + [p[0] for p in own]))
                t["auto"] = [[g, round(self.interp_fast(own, g) * self.interp(self.master, g), 4)] for g in grid]
        self.notes.sort(key=lambda x: x[0])
        out = {"duration": self.runtime, "tracks": self.tracks, "notes": self.notes, "cc": self.cc, "fx": self.fx}
        out.update(kw)
        json.dump(out, open(path, "w"))
        print(f"{path}: {len(self.tracks)} tracks, {len(self.notes)} notes, {len(self.fx)} fx")




# ---------------------------------------------------------------------------------------------
# V3 "Cinematic Trailer" for the full film (audio/film_timeline.json, 247.665 s):
# D minor, C minor for "Under the hood", back to D minor for the proof, ending in D major.
# ---------------------------------------------------------------------------------------------
FT = json.load(open(os.path.join(HERE, "..", "audio", "film_timeline.json")))
FILM_RUNTIME = FT["runtime_s"]
DIALOGUE = [(l["start"], l["end"]) for l in FT["lines"] if l["kind"] == "dialogue"]

V3_HITS = [  # (seconds, kind, cue)
    (0.0, "cold_open", "problem: cold-open boom + sub drop, pizzicato/tick pulse"),
    (24.6, "riser", "riser 24.60-25.98, near-silence to 26.28"),
    (26.28, "big", "04_meet: Meet Kairos"),
    (43.28, "medium", "06_decides: contact or wait"),
    (48.29, "flourish", "07_channel: channel ring flourish"),
    (56.51, "medium_brass", "08_aladdin: safety supervisor, brass stab"),
    (64.29, "light", "09_rules"),
    (71.35, "stop", "09_rules: everything stops; near-silence to 74.50"),
    (74.5, "pad", "st dialogue (Please stop messaging me): pad only"),
    (79.9, "light", "10_reply: WhatsApp"),
    (87.5, "pad", "wa dialogue: pad only"),
    (112.44, "ring", "vc dialogue: phone-ring accent, pad only"),
    (130.6, "medium", "12_verify"),
    (140.5, "pad", "fp dialogue: suspense pad only"),
    (149.63, "build", "13_learns: build begins"),
    (158.1, "riser", "riser 158.10-159.50"),
    (159.72, "hood", "t1_record: Under the hood, C minor electronic texture"),
    (167.5, "step", "t2_model"),
    (178.85, "step", "t3_reserve"),
    (188.17, "step", "t4_webhooks"),
    (199.02, "step", "t5_intent"),
    (208.77, "step", "t6_loop"),
    (218.76, "lift", "14_operator: lift, back towards D"),
    (225.1, "riser", "riser 225.10-226.40, silence to 226.72"),
    (226.72, "biggest", "15_proof: climax, choir"),
    (235.33, "big", "16_safe: zero rule breaches"),
    (239.84, "final", "17_end: final statement, sustained D major"),
    (244.7, "boom", "final low boom, tail to 247.665"),
]


FT_SHORT = json.load(open(os.path.join(HERE, "..", "audio", "film_timeline_short.json")))

V3_HITS_SHORT = [  # (seconds, kind, cue) for the 2-minute cut
    (0.0, "cold_open", "problem: cold-open boom + sub drop, pizzicato/tick pulse"),
    (17.3, "riser", "riser 17.30-18.27, near-silence to 18.57"),
    (18.57, "big", "04_meet: Meet Kairos"),
    (25.29, "medium", "06_decides"),
    (30.30, "flourish", "07_channel: channel ring flourish"),
    (37.7, "ring", "vc dialogue: phone-ring accent, pad only to 49.16"),
    (49.16, "medium_brass", "08_aladdin: brass stab"),
    (56.94, "light", "09_rules"),
    (64.0, "stop", "09_rules: everything stops; near-silence to 67.20"),
    (67.2, "pad", "st dialogue: pad only"),
    (72.56, "pad", "fp dialogue: suspense pad only"),
    (75.75, "accent", "fp_a1: the fake is caught (soft medium accent)"),
    (81.68, "build", "13_learns: build begins"),
    (90.58, "lift", "14_operator: lift (dashboard)"),
    (96.9, "riser", "riser 96.90-97.85, silence to 98.14"),
    (98.14, "biggest", "15_proof: climax, choir"),
    (106.95, "big", "16_safe"),
    (111.46, "final", "17_end: final statement, sustained D major"),
    (116.4, "boom", "final low boom, tail to 119.287"),
]


def v3_short(path, hits_path=None):
    v3(path, hits_path, cut="short")


FT_TECH = json.load(open(os.path.join(HERE, "..", "audio", "film_timeline_tech.json")))

V3_HITS_TECH = [  # (seconds, kind, cue) for the technical film
    (0.0, "riser", "soft riser into the cold open"),
    (1.1, "cold_open", "x01_intro: cold-open hit, C minor"),
    (4.31, "step", "t1_record: arpeggio, sub pulse, ticking hats"),
    (13.34, "step", "x02_churn: churn / re-sort rhythm"),
    (28.3, "step", "t2_model: off-beat e-piano arp, halo pad"),
    (38.52, "medium_brass", "t3_reserve: Aladdin, low brass stab"),
    (47.68, "step", "t4_webhooks: ping-pong delay plucks"),
    (57.97, "step", "x03_voice: strings ostinato + arps"),
    (68.39, "step", "t5_intent: layered decision ladder"),
    (78.86, "step", "x04_cm03: build"),
    (92.95, "riser", "riser 92.95-93.65"),
    (93.87, "lift", "x05_hitl: lift to E-flat major, a person takes control (piano)"),
    (109.18, "step", "t6_loop: back to C minor, building"),
    (118.4, "riser", "riser 118.40-119.05, silence to 119.34"),
    (119.34, "biggest", "x06_proof: climax, choir"),
    (128.54, "final", "x07_end: final statement, C major"),
    (133.3, "boom", "final low boom, tail to 135.64"),
]


def v3_tech(path, hits_path=None):
    v3(path, hits_path, cut="tech")


def v3(path, hits_path=None, cut="full"):
    R = {"full": FILM_RUNTIME, "short": FT_SHORT["runtime_s"], "tech": FT_TECH["runtime_s"]}[cut]
    if cut == "full":
        grid_hits = [0.0, 26.28, 43.28, 48.29, 56.51, 64.29, 71.35, 79.9, 112.44, 130.6, 149.63, 159.72,
                     167.5, 178.85, 188.17, 199.02, 208.77, 218.76, 226.72, 235.33, 239.84]
        tP, tS, tF, tB = 226.72, 235.33, 239.84, 244.7
    elif cut == "tech":
        grid_hits = [0.0, 1.1, 4.31, 13.34, 28.3, 38.52, 47.68, 57.97, 68.39, 78.86, 93.87, 109.18, 119.34, 128.54]
        tP, tS, tF, tB = 119.34, None, 128.54, 133.3
    else:
        grid_hits = [0.0, 18.57, 25.29, 30.30, 37.7, 49.16, 56.94, 64.0, 72.56, 81.68, 90.58, 98.14, 106.95, 111.46]
        tP, tS, tF, tB = 98.14, 106.95, 111.46, 116.4
    A, beat = [(0, 0.0)], 0
    for t0, t1 in zip(grid_hits, grid_hits[1:]):
        beat += max(1, round((t1 - t0) / 0.6))
        A.append((beat, t1))
    A.append((beat + 12, grid_hits[-1] + 12 * 0.64))
    HB = {t: b for b, t in A}  # hit time -> beat
    s = Score(A, seed=303, runtime=R)

    hitkit = s.track("hit kit", 0, perc=True, volume=0.85, reverb=0.32, cut=-2)
    kit = s.track("groove kit", 0, perc=True, volume=1.0, reverb=0.28, cut=-4)
    taiko = s.track("taiko", 116, volume=1.0, reverb=0.3, lp=6000, cut=-4)
    timp = s.track("timpani", 47, volume=0.9, reverb=0.38, cut=-3)
    ost = s.track("ostinato strings", 48, volume=1.0, reverb=0.3, hp=90, lp=5200, cut=-7, pan=-0.3)
    pizz = s.track("pizzicato", 45, volume=1.0, reverb=0.35, hp=80, lp=5000, cut=-6, pan=0.3)
    lows = s.track("low strings", 49, volume=1.0, reverb=0.35, lp=2600, cut=-5)
    bigs = s.track("big strings", 48, volume=0.75, reverb=0.45, hp=45, cut=-4, pan=0.1)
    tbn = s.track("trombones", 57, volume=0.7, reverb=0.35, lp=3200, cut=-4, dist=[18, -8], pan=-0.15)
    tuba = s.track("tuba", 58, volume=0.75, reverb=0.3, lp=2000, cut=-3, dist=[14, -8])
    brass = s.track("brass section", 61, volume=0.5, reverb=0.4, lp=3500, cut=-5, dist=[12, -10], pan=0.15)
    horns = s.track("french horns", 60, volume=0.7, reverb=0.5, lp=4500, cut=-4)
    choir = s.track("choir", 52, volume=0.55, reverb=0.55, hp=150, lp=6000, cut=-5)
    piano = s.track("piano", 0, volume=1.0, reverb=0.5, hp=70, cut=-5)
    pad = s.track("warm pad", 89, volume=0.9, reverb=0.45, hp=110, lp=2600, cut=-8)
    trem = s.track("tremolo strings", 44, volume=0.8, reverb=0.45, hp=70, lp=2200, cut=-8)
    cel = s.track("celesta", 8, volume=0.5, reverb=0.5, cut=-3, pan=0.25,
                  delay={"time": 0.45, "feedback": 30, "wet": 25, "lp": 6000})
    revcym = s.track("reverse cymbal", 119, volume=0.6, reverb=0.4, hp=300)
    # "under the hood" electronic layer
    arpA = s.track("synth arp", 39, volume=0.8, reverb=0.25, hp=90, lp=2400, cut=-6, pan=-0.2,
                   delay={"time": 0.45, "feedback": 30, "wet": 22, "lp": 3000})
    arpB = s.track("e-piano 2 arp", 5, volume=0.7, reverb=0.35, hp=200, lp=4500, cut=-7, pan=0.3,
                   delay={"time": 0.3, "feedback": 35, "wet": 28, "lp": 4000})
    sub = s.track("sub synth", 38, volume=0.9, reverb=0.05, lp=500, cut=0)
    poly = s.track("polysynth pad", 90, volume=0.7, reverb=0.45, hp=140, lp=2600, cut=-8)
    halo = s.track("halo pad", 94, volume=0.45, reverb=0.6, hp=400, lp=5000, cut=-9)
    fxlow = s.track("fx low", 0, fx="low", volume=0.36, reverb=0.12, hp=24)
    fxair = s.track("fx air", 0, fx="air", volume=0.2, reverb=0.35, hp=250, cut=-2)
    fxbraam = s.track("fx braam", 0, fx="braam", volume=0.25, reverb=0.3, lp=2400, cut=-4)
    if cut == "tech":  # ping-pong plucks: two harps hard left/right, each with its own echo
        pluckL = s.track("pluck L", 46, volume=0.75, reverb=0.3, hp=150, lp=3600, cut=-7, pan=-0.85,
                         delay={"time": 0.45, "feedback": 30, "wet": 30, "lp": 3500})
        pluckR = s.track("pluck R", 46, volume=0.75, reverb=0.3, hp=150, lp=3600, cut=-7, pan=0.85,
                         delay={"time": 0.45, "feedback": 30, "wet": 30, "lp": 3500})

    def hit_at(t, kind, sym="Dm", sustain=2.5):
        """hits are placed at exact times (no humanisation)."""
        root = parse(sym)[0]
        r1, r2 = low(root, 26), low(root, 38)
        if kind in ("big", "biggest", "final", "cold_open", "hood"):
            s.fxev("boom", t, "low", gain=1.0 if kind not in ("cold_open", "hood") else 0.85, f=44)
            if kind in ("biggest", "cold_open", "final", "hood"):
                s.fxev("subdrop", t, "low", gain=0.55)
        elif kind in ("medium", "medium_brass"):
            s.fxev("boom", t, "low", gain=0.6, f=48)
        elif kind in ("stop", "light", "step"):
            s.fxev("thump", t, "low", gain={"stop": 0.9, "light": 0.6, "step": 0.45}[kind])
        if kind == "cold_open":
            s.add(timp, t, 38, 100, 3)
            return
        if kind == "step":
            s.add(hitkit, t, 36, 92, 0.5)
            s.add(hitkit, t, 46, 70, 0.8)
            s.add(timp, t, r2 if r2 <= 45 else r2 - 12, 84, 2)
            s.fxev("subdrop", t, "low", gain=0.25)
            return
        loud = {"biggest": 127, "final": 122, "big": 120, "hood": 116, "medium": 108, "medium_brass": 108,
                "stop": 118, "light": 92}[kind]
        s.add(hitkit, t, 36, loud, 0.6)
        s.add(taiko, t, 41, loud, 1.5)
        s.add(taiko, t + 0.004, 36, loud - 6, 1.5)
        s.add(timp, t, r2 if r2 <= 45 else r2 - 12, loud, 2.5)
        if kind != "stop":
            s.add(hitkit, t, 49, loud - (0 if kind in ("big", "biggest", "final", "hood") else 25), 4)
        if kind in ("big", "biggest", "final", "hood"):
            s.add(hitkit, t + 0.01, 57, loud - 10, 4)
            s.add(timp, t, r2 - 12 if r2 - 12 >= 33 else r2, loud - 10, 3)
        if kind == "stop":
            s.add(hitkit, t, 38, 125, 0.4)
            s.add(hitkit, t, 40, 110, 0.4)
            for p in voicing(sym, 38, 62):
                s.add(bigs, t, p, 120, 0.22)
            for p in (r2, r2 + 12):
                s.add(tbn, t, p, 120, 0.25)
                s.add(tuba, t, p - 12, 115, 0.25)
            return
        top = 74 if kind in ("big", "biggest", "final") else 62
        dur = sustain if kind in ("big", "biggest", "final", "hood") else 0.45
        for p in [r2 - 12, r2] + voicing(sym, r2 + 7, top):
            s.add(bigs, t, p, loud - 5, dur)
        if kind in ("big", "biggest", "final", "medium_brass", "hood"):
            bd = 0.6 if kind == "medium_brass" else 1.8
            for p in (r2, r2 + 7, r2 + 12):
                s.add(tbn, t, p, loud, bd)
            s.add(tuba, t, r1 + 12 if r1 + 12 < 40 else r1, loud, bd)
            s.add(tuba, t, r2, loud - 8, bd)
            for p in voicing(sym, 50, 65):
                s.add(brass, t, p, loud - 12, bd * 0.9)
            s.fxev("braam", t, "braam", gain=0.6 if kind == "medium_brass" else 0.9, t1=t + bd + 0.6,
                   f=440 * 2 ** ((r1 + 12 - 69) / 12))

    def ostinato(b0, nbeats, sym, vel=88, octave_lo=50, layer=False, gallop=False):
        """16th spiccato ostinato with a 3-3-2 accent shape (or an 8th+two-16ths gallop)."""
        root = low(parse(sym)[0], octave_lo)
        third = low(parse(sym)[1][1], root)
        fifth = root + 7
        shape = [root, root, fifth, root, third, root, fifth, root, root, fifth, root, third, root, fifth, third, fifth]
        acc = {0, 3, 6, 8, 11, 14}
        for k in range(int(nbeats * 4)):
            if gallop and k % 4 == 1:
                continue
            p = shape[k % 16]
            v = vel + (14 if k % 16 in acc else -8)
            s.note(ost, b0 + k / 4, p, v, 0.18, hum_ms=4, vh=5)
            if layer and k % 2 == 0:
                s.note(pizz, b0 + k / 4, p + 12, v - 10, 0.3, hum_ms=4, vh=5)

    def drums(b0, nbeats, level, fill=True):
        pats = {1: [0, 10], 2: [0, 6, 8, 11, 14], 3: [0, 3, 6, 8, 10, 11, 12, 14, 15]}
        for k in range(int(nbeats * 4)):
            pos = k % 16
            if pos in pats[level]:
                v = 70 + 10 * level + (12 if pos in (0, 8) else 0)
                s.note(taiko, b0 + k / 4, 38 if pos in (0, 8) else 43, v, 1.0, hum_ms=4)
                if pos == 0:
                    s.note(kit, b0 + k / 4, 36, 70 + 8 * level, 0.5, hum_ms=3)
            if level >= 2 and pos in (4, 12):
                s.note(kit, b0 + k / 4, 45, 58 + 6 * level, 0.5, hum_ms=4)
            if level >= 3 and pos in (2, 7, 13):
                s.note(kit, b0 + k / 4, 43, 66, 0.5, hum_ms=4)
        if fill and level >= 2:
            last = b0 + nbeats - 1
            for i, (p, v) in enumerate([(50, 78), (47, 82), (45, 88), (43, 94)]):
                s.note(kit, last + i / 4, p, v, 0.5, hum_ms=3)

    def low_bed(b0, chords, vel=72):
        b = b0
        for sym, d in chords:
            r = low(parse(sym)[2], n("D2") - 2)
            s.note(lows, b, r, vel, d, hum_ms=0, vh=3)
            s.note(lows, b, r + 7, vel - 8, d, hum_ms=0, vh=3)
            b += d

    def section(b0, chords, level, ost_vel=88, layer=False, drums_on=True, gallop=False):
        b = b0
        for sym, d in chords:
            ostinato(b, d, sym, vel=ost_vel, layer=layer, gallop=gallop)
            b += d
        low_bed(b0, chords)
        if drums_on:
            drums(b0, b - b0, level)

    def padbed(b0, chords, vel=60, tremolo=False, lo=50, hi=65):
        """dialogue bed: low pad and low strings only (below the voices), no drums."""
        b = b0
        for sym, d in chords:
            for p in voicing(sym, lo, hi):
                s.note(pad, b, p, vel, d + 0.1, hum_ms=0, vh=2)
            s.note(lows, b, low(parse(sym)[2], 36), vel + 4, d + 0.1, hum_ms=0, vh=2)
            if tremolo:
                for p in voicing(sym, 45, 57):
                    s.note(trem, b, p, vel - 4, d + 0.1, hum_ms=0, vh=2)
            b += d

    def short_sections():
        # ---- problem 0 - 18.57 ----
        hit_at(0.0, "cold_open")
        for p in (n("D2"), n("A2")):
            s.note(lows, 0, p, 70, 28.5, hum_ms=0)
        intro = [("Dm", 8), ("Bb/D", 8), ("Gm/D", 8), ("Bb", 4), ("A", 3)]
        b = 0
        for sym, d in intro:
            if b >= 8:
                s.note(lows, b, voicing(sym, 50, 62)[0], 50 + b // 2, d, hum_ms=0)
            b += d
        for k in range(4, 57):
            beat = k / 2
            sym = next(sy for (sy, bb) in [("Dm", 8), ("Bb/D", 16), ("Gm/D", 24), ("Bb", 28), ("A", 99)] if beat < bb)
            root = low(parse(sym)[0], 50)
            p = root + [0, 7, 12, 7][k % 4] if k % 4 else low(2, 50)
            s.note(pizz, beat, p, 62 + int(24 * k / 57) + (8 if k % 2 == 0 else 0), 0.4)
        for k in range(1, 28):
            s.fxev("tick", s.T(k), "air", gain=0.05 + 0.07 * k / 28)
        for start, notes in [(4, ["A4", "D5", "F5", "E5"]), (12, ["A4", "D5", "F5", "G5"]), (20, ["Bb4", "D5", "G5", "F5"])]:
            for i, (nm, dd) in enumerate(zip(notes, [1, 1, 1.5, 3])):
                s.note(piano, start + [0, 1, 2, 3.5][i], n(nm), 84 if i < 3 else 76, dd)
            s.note(piano, start, n("D3"), 70, 6)
        for bar in range(4, 7):
            s.note(taiko, bar * 4, 38, 60 + bar * 5, 1.5, hum_ms=0)
            s.note(kit, bar * 4 + 2.5, 41, 48 + bar * 4, 0.6)
        for k in range(24 * 4, int(28.8 * 4)):
            s.note(timp, k / 4, n("A1"), 40 + (k - 96) * 3, 0.3, hum_ms=3)
        s.fxev("riser", 17.3, "air", gain=0.55, t1=18.27)
        s.fxev("revcrash", 17.5, "air", gain=0.35, t1=18.27)
        s.add(revcym, 16.71, 60, 100, 1.6)
        for p in voicing("A", 45, 69):
            s.add(bigs, 17.1, p, 70, 1.1)
        # ---- meet / decides / channels ----
        hit_at(18.57, "big", "Dm", sustain=2.4)
        section(HB[18.57], [("Dm", 4), ("Bb", 4), ("C", 3)], level=2)
        hit_at(25.29, "medium", "Gm")
        section(HB[25.29], [("Gm", 4), ("Bb", 2), ("A", 2)], level=2, layer=True)
        hit_at(30.30, "light", "Dm")
        for i, nm in enumerate(["D5", "F5", "A5", "D6", "F6", "A6"]):
            s.add(cel, 30.30 + i * 0.05, n(nm), 78 - 3 * i, 0.6)
        for i, nm in enumerate(["D4", "F4", "A4", "D5"]):
            s.add(pizz, 30.30 + i * 0.075, n(nm), 92, 0.3)
        b0 = HB[30.30]
        section(b0, [("Dm", 4), ("F", 4), ("C", 2), ("A", 2)], level=2, ost_vel=84, layer=True, gallop=True)
        for k in range(int(12 * 4)):
            s.note(kit, b0 + k / 4, 42, {0: 50, 1: 30, 2: 42, 3: 30}[k % 4], 0.08, hum_ms=4, vh=4)
        # ---- call dialogue 37.7 - 49.16: ring, pad only ----
        for rep in range(2):
            for k in range(8):
                s.add(cel, 37.7 + rep * 0.9 + k * 0.05, n("A6") if k % 2 == 0 else n("F#6"), 58 - 2 * k, 0.12)
        padbed(HB[37.7], [("Gm", 8), ("Eb", 8), ("A", 3)], vel=52)
        # ---- safety 49.16 - 64.0 ----
        hit_at(49.16, "medium_brass", "Bb")
        b0 = HB[49.16]
        ch = [("Bb", 4), ("Gm", 4), ("Dm", 4), ("A", 1)]
        section(b0, ch, level=2, ost_vel=84)
        bb = b0
        for sym, d in ch:
            for p in (low(parse(sym)[0], 38), low(parse(sym)[0], 38) + 7):
                s.note(tbn, bb, p, 64, d, hum_ms=0, vh=3)
            bb += d
        hit_at(56.94, "light", "Dm")
        section(HB[56.94], [("Dm", 4), ("Bb", 4), ("Gm", 2), ("A", 2)], level=1, ost_vel=76)
        hit_at(64.0, "stop", "Dm")
        # ---- st dialogue 67.2 - 72.06 and fp dialogue 72.56 - 81.18: pads only, accent on the reveal ----
        padbed(HB[64.0] + 5.3, [("Dm", 4), ("Bb", 4.7)], vel=52)
        padbed(HB[72.56], [("Dm", 5), ("Bb", 5), ("A", 5)], vel=50, tremolo=True)
        s.fxev("boom", 75.75, "low", gain=0.4, f=48)
        s.add(timp, 75.75, 38, 92, 2.5)
        s.add(hitkit, 75.75, 49, 66, 3)
        for p in (n("D2"), n("A2"), n("D3")):
            s.add(lows, 75.75, p, 84, 0.6)
        # ---- learns 81.68 - 90.58: build ----
        hit_at(81.68, "light", "Dm")
        b0 = HB[81.68]
        build = [("Dm", 4), ("Bb", 4), ("F", 4), ("C", 3)]
        bb = b0
        for i, (sym, d) in enumerate(build):
            ostinato(bb, d, sym, vel=84 + 4 * i, octave_lo=50 + (0 if i < 2 else 5), layer=i >= 1)
            drums(bb, d, 1 if i < 1 else (2 if i < 3 else 3), fill=i == 3)
            bb += d
        low_bed(b0, build)
        # ---- operator 90.58 - 98.14: lift, riser, silence ----
        hit_at(90.58, "light", "Bb")
        b0 = HB[90.58]
        ch = [("Bb", 4), ("F/A", 4), ("Gm", 2), ("A", 3)]
        bb = b0
        for sym, d in ch:
            for p in voicing(sym, 55, 72):
                s.note(bigs, bb, p, 72, d + 0.1, hum_ms=0, vh=3)
            bb += d
        low_bed(b0, ch[:3])
        ostinato(b0, 10, "Bb", vel=80, layer=True)
        for i, nm in enumerate(["F4", "Bb4", "D5", "C5"]):
            s.note(piano, b0 + [0, 1, 2, 3.5][i], n(nm), 84, [1, 1, 1.5, 3][i])
        for k in range(0, 10 * 4):
            if k % 16 in (0, 10):
                s.note(taiko, b0 + k / 4, 38, 84, 1.0, hum_ms=3)
        s.fxev("riser", 96.9, "air", gain=0.8, t1=97.85)
        s.fxev("revcrash", 97.1, "air", gain=0.5, t1=97.85)
        s.add(revcym, 96.37, 60, 115, 1.6)
        for k in range(int((b0 + 10) * 4), int((b0 + 12) * 4)):
            s.note(timp, k / 4, n("A1"), 80 + (k - (b0 + 10) * 4) * 4, 0.3, hum_ms=3, vh=3)

    def tech_sections():
        def elec(t0, t1, loop, L, ch_beats=4, arp_vel=0, end=None):
            """the "Under the hood" engine: L is the set of layers to play in [t0, t1)."""
            b0, nb = HB[t0], HB[t1] - HB[t0]
            ends = end or nb
            for k4 in range(0, ends, ch_beats):
                sym = loop[(k4 // ch_beats) % len(loop)]
                d = min(ch_beats, ends - k4)
                bb = b0 + k4
                tones = voicing(sym, 48, 62)
                if "arp" in L:
                    order = [0, 1, 2, 1, 2, 0, 1, 2, 0, 2, 1, 2, 1, 0, 2, 1]
                    if "churn" in L:  # re-sort: shuffled tones, then sorted on the last beat
                        pool = tones + [p + 12 for p in tones]
                    for k in range(int(d * 4)):
                        if "churn" in L:
                            if k % 4 == 0:
                                grp = s.rng.sample(pool, 4) if (k // 4) < d - 1 else sorted(s.rng.sample(pool, 4))
                            p = grp[k % 4]
                        else:
                            p = tones[order[k % 16] % len(tones)] + (12 if k % 8 == 6 and "up" in L else 0)
                        s.note(arpA, bb + k / 4, p, (86 if k % 16 in (0, 3, 6, 8, 11, 14) else 66) + arp_vel, 0.14, hum_ms=2, vh=4)
                if "arpB" in L:
                    up = voicing(sym, 60, 72)
                    for k in range(int(d * 2)):
                        if k % 2 == 1:
                            s.note(arpB, bb + k / 2, up[(k // 2) % len(up)], 68, 0.3, hum_ms=3, vh=4)
                if "ping" in L:
                    up = voicing(sym, 60, 74)
                    for k in range(int(d * 2)):
                        s.note(pluckL if k % 2 == 0 else pluckR, bb + k / 2, up[(k * 2) % len(up)], 74 if k % 4 == 0 else 64, 0.4, hum_ms=3, vh=4)
                if "sub" in L:
                    r = low(parse(sym)[0], 24)
                    for k in range(int(d * 2)):
                        s.note(sub, bb + k / 2, r, 96 if k % 2 == 0 else 80, 0.42, hum_ms=0, vh=3)
                if "poly" in L:
                    for p in voicing(sym, 51, 63):
                        s.note(poly, bb, p, 64, d + 0.05, hum_ms=0, vh=2)
                if "halo" in L:
                    for p in voicing(sym, 72, 84)[:2]:
                        s.note(halo, bb, p, 50, d + 0.05, hum_ms=0, vh=2)
                if "lows" in L:
                    for p in voicing(sym, 50, 64):
                        s.note(lows, bb, p, 66, d + 0.05, hum_ms=0, vh=2)
                if "tbn" in L:
                    r = low(parse(sym)[0], 36)
                    for p in (r, r + 7):
                        s.note(tbn, bb, p, 62, d, hum_ms=0, vh=3)
                if "ost" in L:
                    ostinato(bb, d, sym, vel=82, octave_lo=48, layer="pizz" in L)
                for k in range(int(d * 4)):
                    pos, tb = k % 16, bb + k / 4
                    if "hats" in L:
                        s.note(kit, tb, 42, {0: 56, 1: 30, 2: 44, 3: 30}[k % 4], 0.07, hum_ms=3, vh=4)
                    if "kick" in L and pos % 4 == 0:
                        s.note(kit, tb, 36, 88, 0.4, hum_ms=2, vh=3)
                        s.fxev("thump", s.T(tb), "low", gain=0.2)
                    if "half" in L and pos in (0, 10):
                        s.note(kit, tb, 36, 84, 0.4, hum_ms=2, vh=3)
                        s.fxev("thump", s.T(tb), "low", gain=0.18)
                    if "clap" in L and pos in (4, 12):
                        s.note(kit, tb, 39, 86, 0.3, hum_ms=3, vh=4)
                    if "taiko" in L and pos in (0, 6, 8, 11, 14):
                        s.note(taiko, tb, 38 if pos in (0, 8) else 43, 86, 1.0, hum_ms=4)
                    if "rim" in L and pos in (3, 7, 10, 14):
                        s.note(kit, tb, 37, 60, 0.2, hum_ms=3, vh=4)

        # cold open
        s.fxev("riser", 0.0, "air", gain=0.25, t1=1.0)
        hit_at(1.1, "hood", "Cm", sustain=2.0)
        for p in voicing("Cm", 72, 84)[:2]:
            s.add(halo, 1.1, p, 54, 3.2)
        s.add(sub, 1.1, n("C1"), 90, 3.0)
        for k in range(2, 7):
            s.fxev("tick", s.T(k), "air", gain=0.06)
        # record: arpeggio, sub pulse, ticking hats
        hit_at(4.31, "step", "Cm")
        elec(4.31, 13.34, ["Cm", "Ab", "Eb", "Bb"], {"arp", "sub", "poly", "hats", "halo"})
        # churn: shuffled re-sort arp, rim clicks, kick pulse
        hit_at(13.34, "step", "Cm")
        elec(13.34, 28.3, ["Cm", "Ab", "Fm", "G"], {"arp", "churn", "sub", "poly", "hats", "kick", "rim"})
        # learner: off-beat e-piano arp, halo pad, low strings
        hit_at(28.3, "step", "Ab")
        elec(28.3, 38.52, ["Ab", "Eb", "Bb", "Cm"], {"arp", "up", "arpB", "sub", "halo", "lows", "hats", "half"})
        # Aladdin: low brass stab, trombone pad, taiko
        hit_at(38.52, "medium_brass", "Cm")
        elec(38.52, 47.68, ["Cm", "Bb", "Ab", "G"], {"arp", "sub", "tbn", "hats", "taiko", "poly"})
        # channels / webhooks: ping-pong plucks
        hit_at(47.68, "step", "Eb")
        elec(47.68, 57.97, ["Eb", "Bb", "Cm", "Ab"], {"ping", "sub", "hats", "kick", "halo", "poly"})
        # voice: orchestral-hybrid, strings ostinato with pizzicato
        hit_at(57.97, "step", "Fm")
        elec(57.97, 68.39, ["Fm", "Cm", "Ab", "G"], {"ost", "pizz", "sub", "lows", "half", "hats"})
        # intent: the decision ladder, layer upon layer
        hit_at(68.39, "step", "Cm")
        elec(68.39, 78.86, ["Cm", "Ab", "Eb", "Bb"], {"arp", "arpB", "sub", "poly", "hats", "kick", "clap"}, arp_vel=2)
        # cm03: build towards the human
        hit_at(78.86, "step", "Ab")
        elec(78.86, 93.87, ["Ab", "Fm", "Cm", "G"], {"arp", "up", "arpB", "ost", "sub", "lows", "hats", "kick",
                                                     "clap", "taiko"}, arp_vel=6,
             end=HB[93.87] - HB[78.86] - 1)
        b = HB[93.87] - 3
        for k in range(int(b * 4), int((b + 2) * 4)):
            s.note(kit, k / 4, 38, 60 + (k - b * 4) * 6, 0.3, hum_ms=3, vh=3)
        s.fxev("riser", 92.95, "air", gain=0.55, t1=93.65)
        s.add(revcym, 92.05, 60, 100, 1.6)
        # HITL: lift to E-flat major, a person takes control (piano), half-time
        hit_at(93.87, "big", "Eb", sustain=2.2)
        b0 = HB[93.87]
        ch = [("Eb", 4), ("Bb/D", 4), ("Cm", 4), ("Ab", 4), ("Eb", 4), ("Bb", 5)]
        bb = b0
        for sym, d in ch:
            for p in voicing(sym, 55, 72):
                s.note(bigs, bb, p, 70, d + 0.1, hum_ms=0, vh=3)
            bb += d
        low_bed(b0, ch, vel=68)
        elec(93.87, 109.18, ["Eb", "Bb", "Cm", "Ab", "Eb", "Bb"], {"arp", "sub", "halo", "half", "hats"}, arp_vel=-10)
        for start, notes in [(b0 + 2, ["Bb4", "Eb5", "G5", "F5"]), (b0 + 10, ["Bb4", "Eb5", "G5", "Ab5"]),
                             (b0 + 18, ["Bb4", "Eb5", "G5", "F5"])]:
            for i, (nm, dd) in enumerate(zip(notes, [1, 1, 1.5, 3])):
                s.note(piano, start + [0, 1, 2, 3.5][i], n(nm), 86 if i < 3 else 78, dd)
            s.note(piano, start, n("Eb3"), 72, 6)
        # loop: back to C minor, building, riser and silence
        hit_at(109.18, "step", "Cm")
        b0 = HB[109.18]
        elec(109.18, 119.34, ["Cm", "Ab", "Bb", "G"], {"arp", "up", "arpB", "ost", "pizz", "sub", "lows", "hats",
                                                       "kick", "clap", "taiko"}, arp_vel=8, end=15)
        for k in range(int((b0 + 12) * 4), int((b0 + 15) * 4)):
            s.note(kit, k / 4, 38, 56 + (k - (b0 + 12) * 4) * 5, 0.3, hum_ms=3, vh=3)
        s.fxev("riser", 118.4, "air", gain=0.8, t1=119.05)
        s.fxev("revcrash", 118.5, "air", gain=0.5, t1=119.05)
        s.add(revcym, 117.45, 60, 112, 1.6)
        # proof: BIGGEST hit, full ensemble + choir, C minor -> C major
        hit_at(119.34, "biggest", "Ab", sustain=2.4)
        b0 = HB[119.34]
        climax = [("Ab", 4), ("Eb", 4), ("Fm", 3), ("G", 4)]
        section(b0, climax, level=3, ost_vel=94, layer=True)
        elec(119.34, 128.54, ["Ab", "Eb", "Fm", "G"], {"arp", "up", "sub", "hats"}, arp_vel=10)
        bb = b0
        for sym, d in climax:
            for p in voicing(sym, 57, 72):
                s.note(choir, bb, p, 82, d, hum_ms=0, vh=4)
            for p in voicing(sym, 45, 64):
                s.note(bigs, bb, p, 84, d, hum_ms=0, vh=4)
            bb += d
        theme = [(0, "Eb4", 1), (1, "Ab4", 1), (2, "C5", 1.5), (3.5, "Bb4", 0.5), (4, "Bb4", 2), (6, "G4", 2),
                 (8, "Ab4", 1), (9, "C5", 1), (10, "Bb4", 1), (11, "B4", 4)]
        for o, nm, d in theme:
            s.note(horns, b0 + o, n(nm), 92, d, hum_ms=4)
            s.note(horns, b0 + o, n(nm) - 12, 84, d, hum_ms=4)
        for k in range(int((b0 + 13) * 4), int((b0 + 15) * 4)):
            s.note(kit, k / 4, 38, 70 + (k - (b0 + 13) * 4) * 5, 0.3, hum_ms=3, vh=3)
        # final statement: C major, sustained; final low boom
        tf = tF
        hit_at(tf, "final", "C", sustain=4.4)
        for p in voicing("C", 57, 74):
            s.add(choir, tf, p, 84, 4.4)
        for p in [n("C3"), n("E3"), n("G3"), n("C4")]:
            s.add(horns, tf, p, 86, 4.2)
        for p in [n("C2"), n("G2"), n("C3")]:
            s.add(lows, tf, p, 80, 4.6)
        for p in voicing("C", 62, 74):
            s.add(pad, tf, p, 70, 5.0)
        for p in voicing("C", 72, 84)[:2]:
            s.add(halo, tf, p, 56, 5.0)
        for i, nm in enumerate(["G4", "C5", "E5", "D5"]):
            s.add(piano, tf + 0.8 + 0.6 * i * (1.5 if i == 3 else 1), n(nm), 80, 2.6)
        s.add(piano, tf + 3.6, n("C5"), 74, 3.0)
        s.add(piano, tf + 3.6, n("C3"), 70, 3.0)
        s.add(timp, tB, 36, 110, 2.2)
        s.add(taiko, tB, 36, 110, 2)
        s.fxev("boom", tB, "low", gain=0.95, f=41)
        s.fxev("subdrop", tB, "low", gain=0.45)
        for tr in (choir, horns, lows, pad, bigs, halo):
            s.tracks[tr].setdefault("auto", [[0, 1]])
            s.tracks[tr]["auto"] += [[tf + 0.5, 1.0], [tB + 0.2, 0.55], [tB + 1.9, 0.0]]

    if cut == "full":
        # ================= problem 0 - 26.28 =================
        hit_at(0.0, "cold_open")
        for p in (n("D2"), n("A2")):
            s.note(lows, 0, p, 70, 40.5, hum_ms=0)
        b = 0
        for sym, d in [("Dm", 8), ("Bb/D", 8), ("Dm", 8), ("Gm/D", 8), ("Bb", 4), ("A", 5)]:
            if b >= 8:
                s.note(lows, b, voicing(sym, 50, 62)[0], 46 + b // 2, d, hum_ms=0)
            b += d
        for k in range(4, 82):
            beat = k / 2
            sym = next(sy for (sy, bb) in [("Dm", 8), ("Bb/D", 16), ("Dm", 24), ("Gm/D", 32), ("Bb", 36), ("A", 99)] if beat < bb)
            root = low(parse(sym)[0], 50)
            p = root + [0, 7, 12, 7][k % 4] if k % 4 else low(2, 50)
            s.note(pizz, beat, p, 60 + int(24 * k / 82) + (8 if k % 2 == 0 else 0), 0.4)
        for k in range(1, 41):
            s.fxev("tick", s.T(k), "air", gain=0.05 + 0.07 * k / 41)
        for start, notes in [(4, ["A4", "D5", "F5", "E5"]), (12, ["A4", "D5", "F5", "G5"]),
                             (20, ["A4", "D5", "F5", "E5"]), (28, ["Bb4", "D5", "G5", "F5"])]:
            for i, (nm, dd) in enumerate(zip(notes, [1, 1, 1.5, 3])):
                s.note(piano, start + [0, 1, 2, 3.5][i], n(nm), 84 if i < 3 else 76, dd)
            s.note(piano, start, n("D3"), 70, 6)
        for bar in range(6, 10):
            s.note(taiko, bar * 4, 38, 52 + bar * 5, 1.5, hum_ms=0)
            s.note(kit, bar * 4 + 2.5, 41, 40 + bar * 4, 0.6)
        for k in range(36 * 4, 41 * 4):
            s.note(timp, k / 4, n("A1"), 38 + (k - 144) * 3, 0.3, hum_ms=3)
        s.fxev("riser", 24.6, "air", gain=0.55, t1=25.98)
        s.fxev("revcrash", 24.9, "air", gain=0.35, t1=25.98)
        s.add(revcym, 24.42, 60, 100, 1.6)
        for p in voicing("A", 45, 69):
            s.add(bigs, 24.6, p, 70, 1.3)

        # ================= meet + looks 26.28 - 48.29 =================
        hit_at(26.28, "big", "Dm", sustain=2.4)
        section(HB[26.28], [("Dm", 4), ("Bb", 4), ("F", 4), ("C", 4), ("Dm", 4), ("Bb", 4), ("C", 4)], level=2)
        hit_at(43.28, "medium", "Gm")
        section(HB[43.28], [("Gm", 4), ("Bb", 2), ("A", 2)], level=2, layer=True)

        # ================= channels 48.29 - 56.51: a quick "ring" flourish, galloping texture =================
        hit_at(48.29, "light", "Dm")
        for i, nm in enumerate(["D5", "F5", "A5", "D6", "F6", "A6"]):
            s.add(cel, 48.29 + i * 0.05, n(nm), 78 - 3 * i, 0.6)
        for i, nm in enumerate(["D4", "F4", "A4", "D5"]):
            s.add(pizz, 48.29 + i * 0.075, n(nm), 92, 0.3)
        b0 = HB[48.29]
        section(b0, [("Dm", 4), ("F", 4), ("C", 4), ("A", 2)], level=2, ost_vel=84, layer=True, gallop=True)
        for k in range(int(14 * 4)):
            s.note(kit, b0 + k / 4, 42, {0: 50, 1: 30, 2: 42, 3: 30}[k % 4], 0.08, hum_ms=4, vh=4)

        # ================= safety 56.51 - 71.35: brass stab, then the rules =================
        hit_at(56.51, "medium_brass", "Bb")
        b0 = HB[56.51]
        ch = [("Bb", 4), ("Gm", 4), ("Dm", 4), ("A", 1)]
        section(b0, ch, level=2, ost_vel=84)
        bb = b0
        for sym, d in ch:
            for p in (low(parse(sym)[0], 38), low(parse(sym)[0], 38) + 7):
                s.note(tbn, bb, p, 64, d, hum_ms=0, vh=3)
            bb += d
        hit_at(64.29, "light", "Dm")
        section(HB[64.29], [("Dm", 4), ("Bb", 4), ("Gm", 2), ("A", 2)], level=1, ost_vel=76)
        # stop-hit, then near-silence until the borrower speaks
        hit_at(71.35, "stop", "Dm")

        # ================= st dialogue 74.5 - 79.4: pad only =================
        padbed(HB[71.35] + 5, [("Dm", 4), ("Bb", 5)], vel=52)

        # ================= whatsapp 79.9 - 112.44 =================
        hit_at(79.9, "light", "F")
        b0 = HB[79.9]
        ch = [("F", 4), ("C", 4), ("Dm", 4), ("Bb", 1)]
        bb = b0
        for sym, d in ch:  # light pizzicato pulse + pad under the narrator
            for k in range(int(d * 2)):
                r = low(parse(sym)[0], 50)
                s.note(pizz, bb + k / 2, r + [0, 7, 12, 7][k % 4], 70 if k % 2 == 0 else 60, 0.35)
            bb += d
        padbed(b0, ch, vel=58)
        for k in range(0, 12):
            s.fxev("tick", s.T(b0 + k), "air", gain=0.06)
        # dialogue: pad only, slow harmony
        padbed(b0 + 13, [("Bb", 8), ("F", 8), ("Gm", 8), ("Dm", 8), ("Bb", 5), ("C", 4)], vel=54)

        # ================= call 112.44 - 130.6: ring accent, pad only =================
        t = 112.44
        for rep in range(2):  # "ring ring": a soft high double tremolo, above the voice band
            for k in range(8):
                s.add(cel, t + rep * 0.9 + k * 0.05, n("A6") if k % 2 == 0 else n("F#6"), 58 - 2 * k, 0.12)
        padbed(HB[112.44], [("Gm", 8), ("Dm", 8), ("Eb", 8), ("A", 6)], vel=52, tremolo=False)

        # ================= fake-proof 130.6 - 149.63 =================
        hit_at(130.6, "medium", "Gm")
        b0 = HB[130.6]
        section(b0, [("Gm", 4), ("Eb", 4), ("Bb", 4), ("A", 4)], level=2, layer=False)
        padbed(b0 + 16, [("Dm", 8), ("Bb", 4), ("A", 4)], vel=50, tremolo=True)

        # ================= learns 149.63 - 159.72: build, then into C minor =================
        hit_at(149.63, "light", "Dm")
        b0 = HB[149.63]
        build = [("Dm", 4), ("Bb", 4), ("F", 4), ("G", 2)]
        bb = b0
        for i, (sym, d) in enumerate(build):
            ostinato(bb, d, sym, vel=84 + 4 * i, octave_lo=50 + (0 if i < 2 else 5), layer=i >= 1)
            drums(bb, d, 1 if i < 1 else (2 if i < 3 else 3), fill=False)
            bb += d
        low_bed(b0, build)
        for k in range(int((b0 + 11) * 4), int((b0 + 14) * 4)):
            s.note(kit, k / 4, 38, 50 + (k - (b0 + 11) * 4) * 5, 0.3, hum_ms=3, vh=3)
        s.fxev("riser", 158.1, "air", gain=0.6, t1=159.5)
        s.fxev("revcrash", 158.5, "air", gain=0.35, t1=159.5)
        s.add(revcym, 157.92, 60, 105, 1.6)

        # ================= UNDER THE HOOD 159.72 - 218.76: C minor, electronic =================
        hit_at(159.72, "hood", "Cm", sustain=2.0)
        hood = [(159.72, ["Cm", "Ab", "Eb", "Bb"], 1), (167.5, ["Cm", "Ab", "Fm", "G"], 2),
                (178.85, ["Ab", "Eb", "Bb", "Cm"], 3), (188.17, ["Cm", "Bb", "Ab", "G"], 4),
                (199.02, ["Fm", "Cm", "Ab", "G"], 5), (208.77, ["Cm", "Ab", "Eb", "Bb"], 6)]
        ends = [h[0] for h in hood[1:]] + [218.76]
        for (t0, loop, lvl), t1 in zip(hood, ends):
            if lvl > 1:
                hit_at(t0, "step", loop[0])
            b0, nb = HB[t0], HB[t1] - HB[t0]
            for k4 in range(0, nb, 4):
                sym = loop[(k4 // 4) % 4]
                d = min(4, nb - k4)
                bb = b0 + k4
                tones = voicing(sym, 48, 62)
                order = [0, 1, 2, 1, 2, 0, 1, 2, 0, 2, 1, 2, 1, 0, 2, 1]
                for k in range(int(d * 4)):  # 16th arp, accent on the 3-3-2
                    p = tones[order[k % 16] % len(tones)] + (12 if k % 8 == 6 and lvl >= 3 else 0)
                    s.note(arpA, bb + k / 4, p, (86 if k % 16 in (0, 3, 6, 8, 11, 14) else 66) + 2 * lvl, 0.14, hum_ms=2, vh=4)
                if lvl >= 3:
                    up = voicing(sym, 60, 72)
                    for k in range(int(d * 2)):
                        if k % 2 == 1:
                            s.note(arpB, bb + k / 2, up[(k // 2) % len(up)], 64 + lvl, 0.3, hum_ms=3, vh=4)
                r = low(parse(sym)[0], 24)
                for k in range(int(d * 2)):  # sub pulse in eighths
                    s.note(sub, bb + k / 2, r, 96 if k % 2 == 0 else 80, 0.42, hum_ms=0, vh=3)
                for p in voicing(sym, 51, 63):
                    s.note(poly, bb, p, 60 + 2 * lvl, d + 0.05, hum_ms=0, vh=2)
                if lvl in (1, 3, 6):
                    for p in voicing(sym, 72, 84)[:2]:
                        s.note(halo, bb, p, 50, d + 0.05, hum_ms=0, vh=2)
                if lvl >= 4:
                    for p in voicing(sym, 50, 64):
                        s.note(lows, bb, p, 56 + 3 * lvl, d + 0.05, hum_ms=0, vh=2)
                for k in range(int(d * 4)):  # ticking hats
                    pos = k % 16
                    s.note(kit, bb + k / 4, 42, {0: 56, 1: 30, 2: 44, 3: 30}[k % 4] + (6 if lvl >= 4 else 0), 0.07, hum_ms=3, vh=4)
                    if lvl >= 2 and pos % 4 == 0:
                        s.note(kit, bb + k / 4, 36, 78 + 3 * lvl, 0.4, hum_ms=2, vh=3)
                        s.fxev("thump", s.T(bb + k / 4), "low", gain=0.12 + 0.02 * lvl)
                    if lvl >= 4 and pos in (4, 12):
                        s.note(kit, bb + k / 4, 39, 70 + 4 * lvl, 0.3, hum_ms=3, vh=4)
                if lvl >= 4:
                    for k in range(int(d * 4)):
                        if k % 16 in (0, 6, 8, 11, 14):
                            s.note(taiko, bb + k / 4, 38 if k % 8 == 0 else 43, 66 + 3 * lvl, 1.0, hum_ms=4)
                if lvl >= 5:
                    ostinato(bb, d, sym, vel=74 + 3 * lvl, octave_lo=48)
            if lvl == 6:
                for k in range(int((HB[218.76] - 2) * 4), int(HB[218.76] * 4)):
                    s.note(kit, k / 4, 38, 60 + (k - (HB[218.76] - 2) * 4) * 6, 0.3, hum_ms=3, vh=3)

        # ================= operator 218.76 - 226.72: lift back towards D =================
        hit_at(218.76, "light", "Bb")
        b0 = HB[218.76]
        ch = [("Bb", 4), ("F/A", 4), ("Gm", 2), ("A", 3)]
        bb = b0
        for sym, d in ch:
            for p in voicing(sym, 55, 72):
                s.note(bigs, bb, p, 70, d + 0.1, hum_ms=0, vh=3)
            bb += d
        low_bed(b0, ch[:3])
        ostinato(b0, 10, "Bb", vel=78, layer=True)
        for i, nm in enumerate(["F4", "Bb4", "D5", "C5"]):
            s.note(piano, b0 + [0, 1, 2, 3.5][i], n(nm), 84, [1, 1, 1.5, 3][i])
        for k in range(0, 10 * 4):
            if k % 16 in (0, 10):
                s.note(taiko, b0 + k / 4, 38, 82, 1.0, hum_ms=3)
        s.fxev("riser", 225.1, "air", gain=0.8, t1=226.4)
        s.fxev("revcrash", 225.4, "air", gain=0.5, t1=226.4)
        s.add(revcym, 224.92, 60, 115, 1.6)
        for k in range(int((b0 + 10) * 4), int((b0 + 11.8) * 4)):
            s.note(timp, k / 4, n("A1"), 80 + (k - (b0 + 10) * 4) * 4, 0.3, hum_ms=3, vh=3)

    elif cut == "short":
        short_sections()
    else:
        tech_sections()

    if cut != "tech":
        # ================= proof: BIGGEST hit, back in D minor =================
        hit_at(tP, "biggest", "Bb", sustain=2.4)
        b0 = HB[tP]
        climax = [("Bb", 4), ("F", 4), ("Gm", 2), ("C", 4)]
        section(b0, climax, level=3, ost_vel=96, layer=True)
        bb = b0
        for sym, d in climax:
            for p in voicing(sym, 57, 72):
                s.note(choir, bb, p, 82, d, hum_ms=0, vh=4)
            for p in voicing(sym, 45, 64):
                s.note(bigs, bb, p, 84, d, hum_ms=0, vh=4)
            bb += d
        theme = [(0, "F4", 1), (1, "Bb4", 1), (2, "D5", 1.5), (3.5, "C5", 0.5), (4, "C5", 2), (6, "A4", 2),
                 (8, "Bb4", 1), (9, "D5", 1), (10, "C5", 3), (13, "E4", 1)]
        for o, nm, d in theme:
            s.note(horns, b0 + o, n(nm), 92, d, hum_ms=4)
            s.note(horns, b0 + o, n(nm) - 12, 84, d, hum_ms=4)
        hit_at(tS, "big", "Bb", sustain=2.2)
        b0 = HB[tS]
        s.fxev("riser", tF - 1.84, "air", gain=0.45, t1=tF - 0.12)
        section(b0, [("Bb", 4), ("C", 4)], level=3, ost_vel=98, layer=True)
        for sym, o in [("Bb", 0), ("C", 4)]:
            for p in voicing(sym, 57, 72):
                s.note(choir, b0 + o, p, 86, 4, hum_ms=0, vh=4)
            for p in voicing(sym, 45, 64):
                s.note(bigs, b0 + o, p, 86, 4, hum_ms=0, vh=4)
        for o, nm, d in [(0, "F4", 1), (1, "Bb4", 1), (2, "D5", 2), (4, "G4", 1), (5, "C5", 1), (6, "E5", 2)]:
            s.note(horns, b0 + o, n(nm), 96, d, hum_ms=4)
            s.note(horns, b0 + o, n(nm) - 12, 88, d, hum_ms=4)
        for k in range(int((b0 + 6) * 4), int((b0 + 8) * 4)):
            s.note(kit, k / 4, 38, 70 + (k - (b0 + 6) * 4) * 5, 0.3, hum_ms=3, vh=3)

        # ================= final: D major, final low boom =================
        tf = tF
        hit_at(tf, "final", "D", sustain=5.4)
        for p in voicing("D", 57, 74):
            s.add(choir, tf, p, 84, 5.2)
        for p in [n("D3"), n("F#3"), n("A3"), n("D4")]:
            s.add(horns, tf, p, 86, 4.8)
        for p in [n("D2"), n("A2"), n("D3")]:
            s.add(lows, tf, p, 80, 5.4)
        for p in voicing("D", 62, 74):
            s.add(pad, tf, p, 70, 5.8)
        for i, nm in enumerate(["A4", "D5", "F#5", "E5"]):
            s.add(piano, tf + 0.9 + 0.64 * i * (1.5 if i == 3 else 1), n(nm), 80, 3)
        s.add(piano, tf + 4.0, n("D5"), 74, 3.5)
        s.add(piano, tf + 4.0, n("D3"), 70, 3.5)
        s.add(timp, tB, 38, 110, 3)
        s.add(taiko, tB, 36, 110, 2)
        s.fxev("boom", tB, "low", gain=0.95, f=41)
        s.fxev("subdrop", tB, "low", gain=0.45)
        for tr in (choir, horns, lows, pad, bigs):
            s.tracks[tr].setdefault("auto", [[0, 1]])
            s.tracks[tr]["auto"] += [[tf + 0.5, 1.0], [tB + 0.2, 0.55], [tB + 2.0, 0.0]]

    # dynamics: dialogue scenes sit lower (pad only), the rest at full bed level
    m = [(0, 1.0)]
    dlg = ([(74.45, 79.6), (87.45, 111.3), (112.3, 130.45), (140.45, 149.45)] if cut == "full" else
           [(37.55, 48.9), (67.1, 81.4)] if cut == "short" else [])
    for a, b in dlg:
        m += [(a - 0.4, 1.0), (a, 0.62), (b, 0.62), (b + 0.3, 1.0)]
    if cut == "full":
        m += [(159.5, 1.0), (159.72, 0.86), (180.0, 0.82), (217.5, 0.8), (218.6, 1.0)]
    m.append((R, 1.0))
    s.master = m
    s.dump(path, reverbPreset="largeHall2", reverbLevel=0.9, lufs=-16)
    if hits_path:
        json.dump({"variant": {"full": "v3_cinematic_trailer", "short": "v3_trailer_short", "tech": "v3_tech"}[cut],
                   "timeline": {"full": "audio/film_timeline.json", "short": "audio/film_timeline_short.json",
                                "tech": "audio/film_timeline_tech.json"}[cut], "runtime_s": R,
                   "dialogue_sections_pad_only": {"full": [[74.5, 79.41], [87.5, 111.04], [112.44, 130.11], [140.5, 149.13]],
                                                  "short": [[37.7, 48.66], [67.2, 81.18]], "tech": []}[cut],
                   "tempo_anchors_beat_s": A,
                   "hits": [{"t": t, "type": k, "cue": c} for t, k, c in
                            {"full": V3_HITS, "short": V3_HITS_SHORT, "tech": V3_HITS_TECH}[cut]]},
                  open(hits_path, "w"), indent=1)
        print("wrote", hits_path)


# ---------------------------------------------------------------------------------------------
# V1 "Uplifting": D major, ~112 BPM, piano-led, warm strings, soft four-on-the-floor after the lift.
# ---------------------------------------------------------------------------------------------
LIFT = TL["markers"]["lift_meet_kairos"]
CLIMAX = TL["markers"]["climax_proof"]
FINAL = TL["markers"]["final_line"]


def v1(path, _=None):
    mid = (CLIMAX - LIFT) / 172  # 43 bars between the lift and the climax -> 112.4 BPM
    A = [(0, 0.25), (48, LIFT), (220, CLIMAX), (240, CLIMAX + 20 * mid), (242, CLIMAX + 20 * mid + 1.24),
         (244, FINAL), (260, FINAL + 16 * 0.75)]
    s = Score(A, seed=101)
    piano = s.track("piano", 0, volume=1.0, reverb=0.32, hp=55, cut=-4)
    strA = s.track("strings 1", 48, volume=0.7, reverb=0.45, hp=90, lp=6500, cut=-5, pan=-0.35)
    strB = s.track("strings 2", 49, volume=0.7, reverb=0.45, hp=90, lp=6500, cut=-5, pan=0.35)
    syn = s.track("synth strings", 50, volume=0.45, reverb=0.4, hp=150, lp=5000, cut=-6)
    pad = s.track("warm pad", 89, volume=0.55, reverb=0.4, hp=150, lp=3500, cut=-6)
    bass = s.track("bass", 33, volume=0.9, reverb=0.08, lp=2500, cut=-3)
    kit = s.track("drums", 0, perc=True, volume=0.75, reverb=0.18, cut=-4)
    glock = s.track("glockenspiel", 9, volume=0.4, reverb=0.5, cut=-2, pan=0.2,
                    delay={"time": 0.75 * mid / 4, "feedback": 22, "wet": 22, "lp": 7000})
    cel = s.track("celesta", 8, volume=0.5, reverb=0.5, cut=-3, pan=-0.2,
                  delay={"time": 0.75 * mid / 4, "feedback": 30, "wet": 28, "lp": 6000})
    choir = s.track("choir", 52, volume=0.35, reverb=0.6, hp=180, lp=5000, cut=-6)
    revcym = s.track("reverse cymbal", 119, volume=0.5, reverb=0.4, hp=300)
    fxlow = s.track("fx low", 0, fx="low", volume=0.3, reverb=0.15, hp=28)
    fxair = s.track("fx air", 0, fx="air", volume=0.15, reverb=0.4, hp=300, cut=-3)

    def pedal(beat):
        s.cc.append([round(s.T(beat) - 0.01, 4), piano, 64, 0])
        s.cc.append([round(s.T(beat) + 0.03, 4), piano, 64, 127])

    def bassnote(sym):
        return low(parse(sym)[2], 31)  # G1..F#2

    def piano_pulse(b, sym, vel, beats=4):
        rh = voicing(sym, 59, 74)
        acc = [1.0, 0.72, 0.86, 0.72, 0.95, 0.72, 0.86, 0.8]
        for k in range(int(beats * 2)):
            for p in rh:
                s.note(piano, b + k / 2, p, vel * acc[k % 8], 0.42, vh=5)
        r = bassnote(sym) + 12
        s.note(piano, b, r, vel * 0.9, beats * 0.5)
        s.note(piano, b + beats / 2, r + 12 if beats >= 4 else r, vel * 0.8, beats * 0.5)

    def piano_arp(b, sym, vel, beats=4, step=0.5):
        r = bassnote(sym) + 12
        up = voicing(sym, 57, 72)
        tones = [r, r + 7, r + 12] + [p for p in up if p > r + 12][:3]
        order = [0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 2, 3, 4, 5, 4, 2]
        pedal(b)
        for k in range(int(beats / step)):
            s.note(piano, b + k * step, tones[order[k % len(order)] % len(tones)], vel - (0 if k % 4 == 0 else 8), step * 2.2)

    def strings(b, sym, beats, vel, lo=55, hi=74, layers=(strA, strB)):
        v = voicing(sym, lo, hi)
        for tr in layers:
            for p in v:
                s.note(tr, b, p, vel, beats + 0.15, hum_ms=8, vh=4)
        s.note(strA, b, bassnote(sym) + 12, vel - 6, beats + 0.15, hum_ms=8, vh=4)

    def bassline(b, sym, style, vel=92, beats=4):
        r = bassnote(sym)
        if style == "whole":
            s.note(bass, b, r, vel - 10, beats - 0.1)
        elif style == "eighths":
            for k in range(int(beats * 2)):
                s.note(bass, b + k / 2, r, vel - (0 if k % 2 == 0 else 14), 0.45, vh=5)
        elif style == "octaves":
            for k in range(int(beats * 2)):
                s.note(bass, b + k / 2, r + (12 if k % 4 == 3 else 0), vel - (0 if k % 2 == 0 else 12), 0.45, vh=5)

    def drums(b, style, beats=4):
        for k in range(int(beats * 4)):
            pos, t = k % 16, b + k / 4
            if style in ("soft", "full", "full+", "back") and pos % 4 == 0:
                s.note(kit, t, 36, {"soft": 78, "back": 72}.get(style, 92), 0.4, hum_ms=3)
            if style == "dip" and pos == 0:
                s.note(kit, t, 36, 70, 0.4, hum_ms=3)
            if style in ("full", "full+") and pos in (4, 12):
                s.note(kit, t, 39, 82 if style == "full" else 90, 0.4, hum_ms=4)
            if style in ("full", "full+", "back") and pos in (2, 6, 10, 14):
                s.note(kit, t, 42, 62, 0.15, hum_ms=4)
            if style == "full+" and pos == 14:
                s.note(kit, t, 46, 60, 0.3, hum_ms=4)
            if style != "none":
                shaker = {0: 52, 1: 34, 2: 44, 3: 34}[pos % 4]
                s.note(kit, t, 70, shaker + (0 if style in ("full", "full+") else -8), 0.12, hum_ms=5, vh=5)

    def fill(b, strength=1.0):  # last beat: toms down
        for i, (p, v) in enumerate([(50, 70), (48, 74), (47, 78), (45, 84)]):
            s.note(kit, b + i / 4, p, v * strength, 0.4, hum_ms=3)

    hook = [(0, "A4", 0.5), (0.5, "D5", 0.5), (1, "F#5", 1.5), (2.5, "E5", 0.5), (3, "D5", 1),
            (4, "E5", 1.5), (5.5, "F#5", 0.5), (6, "A5", 2), (8, "B5", 1), (9, "A5", 0.5), (9.5, "F#5", 1.5),
            (11, "E5", 1), (12, "D5", 3)]

    # ---------------- intro: B minor colour, the same reminder sent to everyone ----------------
    intro = ["Bm", "Gmaj7", "Bm", "Gmaj7", "Em7", "Gmaj7", "Bm", "A/C#", "Em7", "F#m7", "Gmaj7", "A7sus4"]
    for bar, sym in enumerate(intro):
        b = bar * 4
        if bar < 4:
            piano_arp(b, sym, 84, step=1.0)
        else:
            piano_arp(b, sym, 80 + bar, step=0.5)
        if bar >= 4:
            strings(b, sym, 4, 50 + 2 * bar, lo=54, hi=71)
        if bar >= 8:
            bassline(b, sym, "whole", vel=78)
            for k in range(8):
                s.note(kit, b + k / 2, 37, 30 + bar + (6 if k % 2 == 0 else 0), 0.2, hum_ms=4)
        # the "same message" ping: one repeated celesta figure, same time every bar
        if bar < 11:
            for o in (0, 0.5):
                s.note(cel, b + 1 + o, n("F#6"), 52 + bar, 0.4, vh=3)
    s.tracks[strA]["auto"] = [[0, 0.5], [s.T(44), 0.6], [s.T(47.5), 0.8], [LIFT - 0.02, 1.0]]
    s.tracks[strB]["auto"] = [[0, 0.5], [s.T(44), 0.6], [s.T(47.5), 0.8], [LIFT - 0.02, 1.0]]
    for k in range(int(46 * 4), 48 * 4):
        s.note(kit, k / 4, 38, 30 + (k - 184) * 5, 0.2, hum_ms=3, vh=3)
    s.add(revcym, LIFT - 1.68, 60, 100, 1.7)
    s.fxev("riser", LIFT - 1.6, "air", gain=0.3, t1=LIFT - 0.12)

    # ---------------- middle: 43 bars, variation every 8 ----------------
    A1 = ["Dadd9", "A/C#", "Bm7", "Gmaj9", "D/F#", "G", "Em7", "Asus4|A"]
    Bp = ["Gmaj7", "A", "F#m7", "Bm7", "Em7", "F#m7", "Gmaj7", "Asus4|A"]
    Dp = ["Bm7", "Gmaj7", "D/F#", "Asus4", "Bm7", "Gmaj7", "Em7", "Asus4|A"]
    BLD = ["Em7", "F#m7", "G|A"]
    plan = [(A1, "A1"), (A1, "A2"), (Bp, "B1"), (Dp, "dip"), (A1, "A3"), (BLD, "build")]

    def halves(sym):
        return [(sym.split("|")[0], 2), (sym.split("|")[1], 2)] if "|" in sym else [(sym, 4)]

    bar = 0
    for chords, sec in plan:
        for i, csym in enumerate(chords):
            b = 48 + 4 * bar
            parts = halves(csym)
            first = bar == 0 or i == 0
            for j, (sym, d) in enumerate(parts):
                bb = b + 2 * j
                if sec in ("A1", "A2", "A3"):
                    piano_pulse(bb, sym, {"A1": 72, "A2": 76, "A3": 82}[sec], beats=d)
                    strings(bb, sym, d, {"A1": 66, "A2": 70, "A3": 78}[sec])
                    bassline(bb, sym, "eighths" if sec != "A3" else "octaves", vel=90 if sec == "A1" else 96, beats=d)
                elif sec == "B1":
                    piano_arp(bb, sym, 76, beats=d, step=0.25 if i % 2 else 0.5)
                    strings(bb, sym, d, 72, lo=57, hi=76)
                    bassline(bb, sym, "eighths", vel=90, beats=d)
                elif sec == "dip":
                    piano_arp(bb, sym, 70, beats=d, step=0.5)
                    for p in voicing(sym, 57, 72):
                        s.note(pad, bb, p, 72, d + 0.1, hum_ms=0, vh=3)
                    strings(bb, sym, d, 58, lo=50, hi=67)
                    bassline(bb, sym, "whole" if i < 4 else "eighths", vel=82, beats=d)
                else:  # build
                    piano_pulse(bb, sym, 84 + 2 * i, beats=d)
                    strings(bb, sym, d, 78 + 4 * i, lo=57, hi=78)
                    bassline(bb, sym, "octaves", vel=98, beats=d)
                    for p in voicing(sym, 57, 72):
                        s.note(syn, bb, p, 76 + 5 * i, d + 0.1, hum_ms=0)
            style = {"A1": "soft", "A2": "full", "B1": "full", "A3": "full+", "build": "full+"}.get(sec)
            if sec == "dip":
                style = "dip" if i < 4 else "back"
            drums(b, style)
            if first:
                s.note(kit, b, 49, 96 if sec != "dip" else 70, 2, hum_ms=0)
            if i == len(chords) - 1 and sec not in ("build",):
                fill(b + 3, 0.9 if sec != "dip" else 0.8)
            bar += 1
        if sec in ("A1", "A3"):
            b0 = 48 + 4 * (bar - len(chords))
            for o, nm, d in hook:
                s.note(glock, b0 + o, n(nm) + 12, 76, d, hum_ms=4)
        if sec == "A3":
            for i, csym in enumerate(chords):
                for sym, d in halves(csym):
                    pass
            for k in range(8):
                for p in voicing(A1[k].split("|")[0], 57, 72):
                    s.note(syn, b0 + 4 * k, p, 70, 4.1, hum_ms=0)
    # lift hit, build into the climax
    s.fxev("thump", LIFT, "low", gain=0.7)
    for p in (n("D2"), n("D3")):
        s.add(bass, LIFT, p, 105, 0.6)
    for k in range(int(216 * 4), 220 * 4):
        s.note(kit, k / 4, 38, 46 + (k - 864) * 4, 0.2, hum_ms=3, vh=3)
    for i, p in enumerate([45, 47, 48, 50]):
        s.note(kit, 219 + i / 4, p, 92 + 3 * i, 0.4, hum_ms=2)
    s.add(revcym, CLIMAX - 1.68, 60, 110, 1.7)
    s.fxev("riser", CLIMAX - 1.5, "air", gain=0.5, t1=CLIMAX - 0.12)
    s.tracks[strA].setdefault("auto", [[0, 1]])

    # ---------------- climax: 6 bars, fullest, the hook on piano in octaves ----------------
    CL = ["G", "A", "F#m7", "Bm7", "Gmaj7", "Asus4|A"]
    s.fxev("thump", CLIMAX, "low", gain=0.85)
    s.fxev("boom", CLIMAX, "low", gain=0.35, f=46)
    for i, csym in enumerate(CL):
        b = 220 + 4 * i
        for j, (sym, d) in enumerate(halves(csym)):
            bb = b + 2 * j
            strings(bb, sym, d, 88, lo=55, hi=79)
            for p in voicing(sym, 55, 72):
                s.note(syn, bb, p, 84, d + 0.1, hum_ms=0)
                s.note(choir, bb, p, 74, d + 0.1, hum_ms=0, vh=3)
            bassline(bb, sym, "octaves", vel=100, beats=d)
            r = bassnote(sym) + 12
            s.note(piano, bb, r, 92, d * 0.9)
            s.note(piano, bb, r - 12, 86, d * 0.9)
            for k in range(int(d * 2)):
                for p in voicing(sym, 57, 67):
                    s.note(piano, bb + k / 2, p, 64 if k % 2 else 74, 0.4, vh=4)
        drums(b, "full+" if i < 5 else "full")
        if i % 2 == 0:
            s.note(kit, b, 49, 104, 2, hum_ms=0)
    melody = [(0, "D5", 1.5), (1.5, "E5", 0.5), (2, "F#5", 1), (3, "G5", 1), (4, "A5", 3), (7, "E5", 1),
              (8, "C#5", 1.5), (9.5, "E5", 0.5), (10, "F#5", 1), (11, "A5", 1), (12, "B5", 2), (14, "A5", 1),
              (15, "F#5", 1), (16, "G5", 1.5), (17.5, "F#5", 0.5), (18, "E5", 1), (19, "D5", 1),
              (20, "E5", 2), (22, "C#5", 1), (23, "E5", 1)]
    for o, nm, d in melody:
        s.note(piano, 220 + o, n(nm), 96, d, hum_ms=4)
        s.note(piano, 220 + o, n(nm) - 12, 84, d, hum_ms=4)
        s.note(glock, 220 + o, n(nm) + 12, 70, d, hum_ms=4)
    for i, p in enumerate([50, 48, 47, 45, 43, 41]):
        s.note(kit, 242.5 + i / 4, p, 80 + 4 * i, 0.4, hum_ms=2)

    # ---------------- final: D add9 with a long natural tail ----------------
    tf = FINAL
    s.add(kit, tf, 49, 100, 3)
    s.add(kit, tf, 36, 100, 0.5)
    s.fxev("thump", tf, "low", gain=0.6)
    pedal(244)
    for p in [n("D2"), n("D3"), n("A3"), n("E4"), n("F#4"), n("A4")]:
        s.add(piano, tf, p, 88, 6.5)
    for p in [n("D2"), n("D3")]:
        s.add(bass, tf, p, 96, 4.5)
    for tr in (strA, strB):
        for p in [n("D3"), n("A3"), n("D4"), n("F#4"), n("A4"), n("E5")]:
            s.add(tr, tf, p, 82, 5.8)
    for p in [n("D4"), n("F#4"), n("A4"), n("E5")]:
        s.add(pad, tf, p, 74, 6.2)
        s.add(syn, tf, p, 70, 5.8)
    for i, nm in enumerate(["A5", "D6", "F#6", "E6", "D6"]):
        s.add(glock, tf + 0.75 * (1 + i) + (0.75 if i == 4 else 0), n(nm), 70 - 4 * i, 1.5)
    for tr in (strA, strB, pad, syn):
        s.tracks[tr].setdefault("auto", [[0, 1]])
        s.tracks[tr]["auto"] += [[tf + 1.0, 1.0], [137.0, 0.5], [138.8, 0.0]]
    # overall dynamic arc: middle sits ~3 dB under the climax, the dip ~5 dB, the build ramps up
    m0 = lambda bar: s.T(48 + 4 * bar)
    s.master = [(0, 0.8), (s.T(16), 0.7), (s.T(32), 0.6), (LIFT - 1.7, 0.62), (LIFT - 0.03, 0.66), (LIFT, 0.9),
                (m0(8), 0.82), (m0(23.5), 0.8), (m0(24), 0.68), (m0(31.5), 0.7), (m0(32), 0.68), (m0(40), 0.66),
                (CLIMAX - 0.6, 0.72), (CLIMAX - 0.03, 0.72), (CLIMAX, 1.0), (RUNTIME, 1.0)]
    s.dump(path, reverbPreset="largeHall", reverbLevel=0.85, lufs=-16)

# ---------------------------------------------------------------------------------------------
# V2 "Modern tech": A minor intro -> C major at the lift, ~118 BPM, e-piano stabs, muted-guitar
# plucks, pumping synth bass, tight hats and claps.
# ---------------------------------------------------------------------------------------------
def v2(path, _=None):
    mid = (CLIMAX - LIFT) / 180  # 45 bars -> 117.6 BPM
    A = [(0, 0.0), (52, LIFT), (232, CLIMAX), (300, CLIMAX + 68 * mid)]
    s = Score(A, seed=202)
    ep = s.track("e-piano", 4, volume=0.95, reverb=0.3, hp=90, cut=-5, pan=-0.1,
                 delay={"time": 0.75 * mid / 4 * 2, "feedback": 18, "wet": 14, "lp": 5000})
    pluck = s.track("muted guitar pluck", 28, volume=0.75, reverb=0.3, hp=180, lp=6000, cut=-7, pan=0.3,
                    delay={"time": 0.75 * mid / 4 * 2, "feedback": 35, "wet": 28, "lp": 4500})
    pad = s.track("warm pad", 89, volume=0.6, reverb=0.4, hp=160, lp=3200, cut=-6)
    syn = s.track("synth strings", 50, volume=0.5, reverb=0.4, hp=160, lp=4500, cut=-6, pan=-0.25)
    bass = s.track("synth bass", 38, volume=0.85, reverb=0.05, lp=1800, cut=-3)
    kit = s.track("drums", 0, perc=True, volume=0.8, reverb=0.14, cut=-4)
    cel = s.track("celesta", 8, volume=0.45, reverb=0.45, cut=-3, pan=0.25,
                  delay={"time": 0.75 * mid / 4 * 2, "feedback": 30, "wet": 25, "lp": 6000})
    revcym = s.track("reverse cymbal", 119, volume=0.5, reverb=0.35, hp=300)
    fxlow = s.track("fx low", 0, fx="low", volume=0.3, reverb=0.08, hp=30)
    fxair = s.track("fx air", 0, fx="air", volume=0.15, reverb=0.35, hp=300, cut=-3)

    def root(sym, lo=33):
        return low(parse(sym)[2], lo)

    def stabs(b, sym, vel, beats=4, rhythm=(0, 0.75, 1.5, 2.5, 3.25)):
        v = voicing(sym, 57, 72)
        for o in rhythm:
            if o < beats:
                for p in v:
                    s.note(ep, b + o, p, vel - (0 if o in (0, 2.5) else 8), 0.35, vh=5)

    def held(b, sym, vel, beats=4):
        for p in voicing(sym, 55, 70):
            s.note(ep, b, p, vel, beats * 0.95, vh=4)

    def arp(b, sym, vel, beats=4):
        tones = voicing(sym, 60, 76)
        order = [0, 1, 2, 1, 3 % len(tones), 2, 1, 2]
        for k in range(int(beats * 4)):
            s.note(pluck, b + k / 4, tones[order[k % 8] % len(tones)], vel + (8 if k % 4 == 0 else 0), 0.2, hum_ms=3, vh=5)

    def pads(b, sym, vel, beats=4, strings=False):
        for p in voicing(sym, 55, 71):
            s.note(pad, b, p, vel, beats + 0.05, hum_ms=0, vh=3)
            if strings:
                s.note(syn, b, p + 12 if p < 62 else p, vel - 6, beats + 0.05, hum_ms=0, vh=3)

    def bassline(b, sym, style, vel=96, beats=4):
        r = root(sym)
        if style == "whole":
            s.note(bass, b, r, vel - 12, beats - 0.1, vh=3)
            return
        for k in range(int(beats * 2)):
            p = r + (12 if style == "octave" and k % 2 == 1 else 0)
            s.note(bass, b + k / 2, p, vel - (0 if k % 2 == 0 else 10), 0.4 if style != "legato" else 0.48, hum_ms=3, vh=4)

    def drums(b, style, beats=4):
        for k in range(int(beats * 4)):
            pos, t = k % 16, b + k / 4
            if style in ("four", "full", "full+") and pos % 4 == 0:
                s.note(kit, t, 36, 100, 0.4, hum_ms=2, vh=4)
                s.fxev("thump", s.T(t), "low", gain=0.22)
            if style == "syncop" and pos in (0, 6, 8, 11):
                s.note(kit, t, 36, 98, 0.4, hum_ms=2, vh=4)
                s.fxev("thump", s.T(t), "low", gain=0.2)
            if style == "half" and pos in (0, 10):
                s.note(kit, t, 36, 90, 0.4, hum_ms=2, vh=4)
            if style in ("four", "full", "full+", "syncop", "half", "clap") and pos in (4, 12):
                s.note(kit, t, 39, 92 if style != "four" else 84, 0.3, hum_ms=3, vh=5)
                if style in ("full+",):
                    s.note(kit, t, 38, 70, 0.3, hum_ms=3, vh=5)
            if style in ("hats", "four", "half", "clap") and pos % 2 == 0:
                s.note(kit, t, 42, 58 if pos % 4 == 2 else 44, 0.1, hum_ms=4, vh=5)
            if style in ("full", "full+", "syncop"):
                s.note(kit, t, 42, {0: 64, 1: 36, 2: 54, 3: 40}[pos % 4], 0.08, hum_ms=4, vh=5)
            if style in ("full+",) and pos in (2, 6, 10, 14):
                s.note(kit, t, 46, 56, 0.25, hum_ms=4, vh=4)
            if style == "ticks" and pos % 2 == 0:
                s.note(kit, t, 42, 34 + (6 if pos % 4 == 2 else 0), 0.08, hum_ms=4, vh=4)

    # ---------------- intro: A minor, the same reminder over and over ----------------
    intro = ["Am", "Fmaj7", "Am", "Fmaj7", "Dm7", "Am", "Fmaj7", "Esus4|E", "Am", "Fmaj7", "Dm7", "Dm7", "Gsus4|G"]

    def halves(sym):
        return [(sym.split("|")[0], 2), (sym.split("|")[1], 2)] if "|" in sym else [(sym, 4)]

    for bar, csym in enumerate(intro):
        b = bar * 4
        for j, (sym, d) in enumerate(halves(csym)):
            bb = b + 2 * j
            held(bb, sym, 70 + bar, beats=d)
            if bar >= 2:
                arp(bb, sym, 50 + 2 * bar, beats=d)
            if bar >= 4:
                pads(bb, sym, 60 + bar, beats=d)
            if bar >= 8:
                bassline(bb, sym, "whole", vel=86, beats=d)
        # the "same message" ping: one repeated figure at the same time every bar
        if bar < 12:
            s.note(cel, b + 2.5, n("E6"), 56 + bar, 0.3, vh=3)
            s.note(cel, b + 2.75, n("E6"), 50 + bar, 0.3, vh=3)
        if 4 <= bar < 12:
            drums(b, "ticks")
    for k in range(int(50 * 4), 52 * 4):
        s.note(kit, k / 4, 38, 36 + (k - 200) * 7, 0.2, hum_ms=3, vh=3)
    s.add(revcym, LIFT - 1.68, 60, 100, 1.7)
    s.fxev("riser", LIFT - 1.8, "air", gain=0.35, t1=LIFT - 0.12)

    # ---------------- middle: C major, 45 bars ----------------
    A1 = ["C", "G/B", "Am7", "Fmaj7", "C", "G", "Fmaj7", "Gsus4|G"]
    B1 = ["Fmaj7", "G", "Em7", "Am7", "Fmaj7", "G", "Am7", "Gsus4|G"]
    DP = ["Am7", "Fmaj7", "C/E", "G", "Am7", "Fmaj7", "Dm7", "Gsus4|G"]
    BL = ["Fmaj7", "G", "Am7", "Fmaj7", "Gsus4|G"]
    plan = [(A1, "A1"), (A1, "A2"), (B1, "B1"), (DP, "dip"), (A1, "A3"), (BL, "build")]
    hook = [(0, "E5", 0.5), (0.75, "G5", 0.5), (1.5, "C6", 0.75), (2.5, "B5", 0.5), (3, "G5", 1),
            (4, "D5", 0.5), (4.75, "G5", 0.5), (5.5, "B5", 0.75), (6.5, "A5", 0.5), (7, "G5", 1),
            (8, "C5", 0.5), (8.75, "E5", 0.5), (9.5, "A5", 0.75), (10.5, "G5", 0.5), (11, "E5", 1),
            (12, "F5", 1.5), (13.5, "E5", 0.5), (14, "C5", 2)]
    bar = 0
    pump_beats = []
    for chords, sec in plan:
        b0 = 52 + 4 * bar
        for i, csym in enumerate(chords):
            b = 52 + 4 * bar
            for j, (sym, d) in enumerate(halves(csym)):
                bb = b + 2 * j
                if sec in ("A1", "A2", "A3", "build"):
                    stabs(bb, sym, {"A1": 80, "A2": 84, "A3": 88, "build": 88}[sec], beats=d)
                    pads(bb, sym, 66 if sec == "A1" else 72, beats=d, strings=sec in ("A3", "build"))
                    bassline(bb, sym, "eighths" if sec == "A1" else "octave", vel=98, beats=d)
                    if sec != "A1":
                        arp(bb, sym, 62 if sec == "A2" else 66, beats=d)
                elif sec == "B1":
                    held(bb, sym, 76, beats=d)
                    arp(bb, sym, 66, beats=d)
                    pads(bb, sym, 72, beats=d, strings=True)
                    bassline(bb, sym, "legato", vel=96, beats=d)
                else:  # dip
                    held(bb, sym, 72, beats=d)
                    pads(bb, sym, 70, beats=d)
                    if i >= 4:
                        arp(bb, sym, 58, beats=d)
                    bassline(bb, sym, "whole" if i < 4 else "eighths", vel=90, beats=d)
            style = {"A1": "four", "A2": "full", "B1": "syncop", "A3": "full+", "build": "full+"}.get(sec)
            if sec == "dip":
                style = "clap" if i < 4 else "half"
            drums(b, style)
            if sec != "dip" or i >= 4:
                pump_beats += [b, b + 1, b + 2, b + 3]
            if i == 0:
                s.note(kit, b, 49, 100 if sec != "dip" else 72, 2, hum_ms=0)
            if i == len(chords) - 1 and sec != "build":
                for k, p in enumerate([38, 38, 45, 38]):
                    s.note(kit, b + 3 + k / 4, p, 70 + 6 * k, 0.3, hum_ms=3)
            bar += 1
        if sec in ("A1", "A3"):
            for o, nm, d in hook:
                s.note(cel, b0 + o, n(nm) + 12, 72, d, hum_ms=3)
                if sec == "A3":
                    s.note(ep, b0 + 16 + o, n(nm), 74, d, hum_ms=3)
    s.fxev("thump", LIFT, "low", gain=0.7)
    s.fxev("boom", LIFT, "low", gain=0.25, f=48)
    # build -> climax
    for k in range(int(228 * 4), 232 * 4):
        s.note(kit, k / 4, 38, 50 + (k - 912) * 4, 0.2, hum_ms=2, vh=3)
    s.add(revcym, CLIMAX - 1.68, 60, 112, 1.7)
    s.fxev("riser", CLIMAX - 2.2, "air", gain=0.55, t1=CLIMAX - 0.12)

    # ---------------- climax: 6 bars, everything, hook on e-piano + celesta ----------------
    CL = ["F", "G", "Em7", "Am7", "F", "G"]
    s.fxev("thump", CLIMAX, "low", gain=0.9)
    s.fxev("boom", CLIMAX, "low", gain=0.4, f=46)
    for i, sym in enumerate(CL):
        b = 232 + 4 * i
        stabs(b, sym, 92)
        pads(b, sym, 80, strings=True)
        arp(b, sym, 72)
        bassline(b, sym, "octave", vel=104)
        drums(b, "full+")
        pump_beats += [b, b + 1, b + 2, b + 3]
        if i % 2 == 0:
            s.note(kit, b, 49, 108, 2, hum_ms=0)
    climax_hook = [(0, "A5", 0.5), (0.75, "C6", 0.5), (1.5, "F6", 0.75), (2.5, "E6", 0.5), (3, "C6", 1),
                   (4, "B5", 0.5), (4.75, "D6", 0.5), (5.5, "G6", 0.75), (6.5, "F6", 0.5), (7, "D6", 1),
                   (8, "G5", 0.5), (8.75, "B5", 0.5), (9.5, "E6", 0.75), (10.5, "D6", 0.5), (11, "B5", 1),
                   (12, "C6", 0.5), (12.75, "E6", 0.5), (13.5, "A6", 0.75), (14.5, "G6", 0.5), (15, "E6", 1),
                   (16, "A5", 0.5), (16.75, "C6", 0.5), (17.5, "F6", 0.75), (18.5, "E6", 0.5), (19, "C6", 1),
                   (20, "D6", 1), (21, "E6", 1), (22, "F6", 1), (23, "G6", 1)]
    for o, nm, d in climax_hook:
        s.note(ep, 232 + o, n(nm) - 12, 84, d, hum_ms=3)
        s.note(cel, 232 + o, n(nm), 74, d, hum_ms=3)
    # drop-out 130.13 -> 131.31 (voice gap), reverse cymbal into the final chord
    tdrop = s.T(256)
    s.add(revcym, FINAL - 1.68, 60, 100, 1.7)
    s.fxev("riser", tdrop + 0.05, "air", gain=0.35, t1=FINAL - 0.1)
    # ---------------- final: C add9, held, natural tail ----------------
    tf = FINAL
    s.add(kit, tf, 49, 104, 3)
    s.add(kit, tf, 36, 104, 0.5)
    s.fxev("thump", tf, "low", gain=0.8)
    s.fxev("boom", tf, "low", gain=0.3, f=44)
    for p in [n("C3"), n("G3"), n("D4"), n("E4"), n("G4"), n("C5")]:
        s.add(ep, tf, p, 86, 5.5)
    for p in [n("C2"), n("C3")]:
        s.add(bass, tf, p, 98, 3.5)
    for p in [n("E4"), n("G4"), n("C5"), n("D5")]:
        s.add(pad, tf, p, 80, 6.0)
        s.add(syn, tf, p, 74, 5.6)
    for i, nm in enumerate(["E6", "G6", "C7", "B6", "G6"]):
        s.add(cel, tf + 0.6 * (1 + i) + (0.6 if i == 4 else 0), n(nm), 66 - 3 * i, 1.2)
    for tr in (pad, syn, ep):
        s.tracks[tr].setdefault("auto", [[0, 1]])
        s.tracks[tr]["auto"] += [[tf + 1.0, 1.0], [137.0, 0.5], [138.8, 0.0]]
    # sidechain-style pump on pads, strings and bass while the kick runs
    for tr in (pad, syn):
        s.pump(tr, pump_beats, depth=0.45, rec=0.26)
    s.pump(bass, pump_beats, depth=0.6, rec=0.12)
    # drop the groove for the gap before the final chord
    s.notes = [x for x in s.notes if not (tdrop + 0.02 <= x[0] < FINAL - 0.01 and x[1] not in (revcym,))]
    m0 = lambda bar: s.T(52 + 4 * bar)
    s.master = [(0, 0.62), (s.T(16), 0.55), (LIFT - 1.8, 0.5), (LIFT - 0.03, 0.55), (LIFT, 0.9), (m0(8), 0.82),
                (m0(23.5), 0.8), (m0(24), 0.64), (m0(31.5), 0.66), (m0(32), 0.62), (m0(40), 0.6),
                (CLIMAX - 0.6, 0.66), (CLIMAX - 0.03, 0.66), (CLIMAX, 1.0), (RUNTIME, 1.0)]
    s.dump(path, reverbPreset="mediumHall", reverbLevel=0.85, lufs=-16)


if __name__ == "__main__":
    which, out = sys.argv[1], sys.argv[2]
    {"v1": v1, "v2": v2, "v3": v3, "v3_short": v3_short, "v3_tech": v3_tech}[which](out, *(sys.argv[3:4]))
