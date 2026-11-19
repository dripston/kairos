#!/usr/bin/env python3
"""Kairos Theme (launch cut): an original high-energy trailer / EDM score, mixed with the Sarvam VO.

Music: Python standard library only, fixed random seed. No samples, loops, presets or third-party audio.
Voice: the recorded Sarvam bulbul:v3 lines in ../audio/vo_sarvam/ (narrator Advait, agent Ritu).

The beat grid comes from ../kairos_film.html: CONFIG.film.bpm and the scene table (lengths in bars,
VO placements in seconds from scene start). Every on-screen slam carries data-beat (and data-hit)
in the HTML; the score puts a brass stab / tom / impact exactly on those beats.

    python3 film/music/compose.py               # all cuts (hero, teaser)
    python3 film/music/compose.py --cut hero

Writes per cut into film/music/ (WAVs are git-ignored; the .m4a copies are what the film plays):
    kairos_theme_<cut>  music only           soundtrack_<cut>  music ducked under VO + VO
    vo_<cut>            VO only               beat_map_<cut>.json  grid, sections, drops, hits, VO, loudness
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import wave
from array import array
from itertools import repeat
from operator import add, mul

SR = 44100
SEED = 20261120
HERE = os.path.dirname(os.path.abspath(__file__))
FILM = os.path.join(HERE, "..", "kairos_film.html")
VO_DIR = os.path.join(HERE, "..", "audio", "vo_sarvam")


# ----------------------------------------------------------------------------- film parsing
def parse_film(path=FILM):
    src = open(path, encoding="utf-8").read()
    bpm = float(re.search(r"bpm:\s*([\d.]+)", src).group(1))
    a = src.index("const ALL_SCENES = [")
    table = src[a:src.index("];", a)]
    scenes = {}
    for m in re.finditer(r'\{id:"(\w+)",\s*name:"([^"]*)",\s*bars:(\d+)(.*?)\}\s*(?:,|$)', table, re.S):
        flags = m.group(4)
        scenes[m.group(1)] = {"id": m.group(1), "name": m.group(2), "bars": int(m.group(3)),
                              "drop": "drop:true" in flags, "final": "final:true" in flags,
                              "mode": (re.search(r'mode:"(\w+)"', flags) or [None, None])[1],
                              "vo": [(k, float(t)) for k, t in re.findall(r'\["(\w+)",\s*([\d.]+)\]', flags)]}
    c = src.index("const CUTS")
    cuts = {name: re.findall(r'"(\w+)"', ids) for name, ids in re.findall(r'(\w+):\s*\[([^\]]*)\]', src[c:src.index("};", c)])}
    for sid in scenes:
        sec = re.search(r'<section class="scene" id="%s"[^>]*>(.*?)</section>' % sid, src, re.S)
        hits = []
        if sec:
            for tag in re.findall(r"<[^>]*data-beat=\"[\d.]+\"[^>]*>", sec.group(1)):
                h = re.search(r'data-hit="(\w+)"', tag)
                if h:
                    hits.append((float(re.search(r'data-beat="([\d.]+)"', tag).group(1)), h.group(1)))
        scenes[sid]["hits"] = sorted(set(hits))
    return bpm, scenes, cuts


def timeline(bpm, scenes, ids):
    beat = 60.0 / bpm
    t, out = 0.0, []
    for sid in ids:
        s = dict(scenes[sid])
        s["start"], s["dur"] = t, s["bars"] * 4 * beat
        s["end"] = t + s["dur"]
        t = s["end"]
        out.append(s)
    return out, t


# ----------------------------------------------------------------------------- buffers
def zeros(n):
    return array("d", bytes(8 * n))


def add_into(dst, src, start, gain=1.0):
    if start < 0:
        src, start = src[-start:], 0
    n = min(len(src), len(dst) - start)
    if n <= 0:
        return
    s = src if len(src) == n else src[:n]
    seg = dst[start:start + n]
    if gain == 1.0:
        dst[start:start + n] = array("d", map(add, seg, s))
    else:
        dst[start:start + n] = array("d", map(add, seg, map(mul, s, repeat(gain, n))))


def scaled(b, g):
    return array("d", map(mul, b, repeat(g, len(b))))


def times(a, b):
    n = min(len(a), len(b))
    return array("d", map(mul, a[:n], b[:n]))


def env(n, a, d, s, r):
    A, D, R = max(1, int(a * SR)), max(1, int(d * SR)), max(1, int(r * SR))
    out = array("d")
    for i in range(n):
        v = i / A if i < A else (1 - (1 - s) * (i - A) / D if i < A + D else s)
        if i > n - R:
            v *= max(0.0, (n - i) / R)
        out.append(v)
    return out


def expdecay(n, tau, attack=0.002):
    A = max(1, int(attack * SR))
    return array("d", ((i / A if i < A else 1.0) * math.exp(-(i / SR) / tau) for i in range(n)))


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


RNG = random.Random(SEED)


def noise(n, seed):
    r = random.Random(seed)
    return array("d", (r.uniform(-1, 1) for _ in range(n)))


def onepole(buf, cutoff):
    a = math.exp(-2 * math.pi * cutoff / SR)
    y, out = 0.0, array("d")
    for x in buf:
        y = (1 - a) * x + a * y
        out.append(y)
    return out


def hp(buf, cutoff):
    return array("d", map(lambda x, l: x - l, buf, onepole(buf, cutoff)))


# ----------------------------------------------------------------------------- oscillators
_cyc = {}


def saw_cycle(period, harm):
    k = (period, harm)
    if k not in _cyc:
        _cyc[k] = array("d", (sum(math.sin(2 * math.pi * h * i / period) / h for h in range(1, harm + 1)) * 0.6
                              for i in range(period)))
    return _cyc[k]


_tone = {}


def supersaw(m, n, harm=14, voices=(-3, -2, -1, 0, 1, 2, 3)):
    """Detuned saw stack: integer periods P-3..P+3 give a natural 10-30 cent supersaw spread."""
    k = (m, n, harm, voices)
    if k in _tone:
        return _tone[k]
    P = int(round(SR / hz(m)))
    out = zeros(n)
    for j, v in enumerate(voices):
        c = saw_cycle(max(8, P + v), harm)
        off = (j * 131) % len(c)
        add_into(out, (c * (n // len(c) + 2))[off:off + n], 0, 1.0 / len(voices) ** 0.5)
    _tone[k] = out
    return out


_cache = {}


def cached(key, fn):
    if key not in _cache:
        _cache[key] = fn()
    return _cache[key]


# ----------------------------------------------------------------------------- instruments
def kick():
    def f():
        n, out, ph = int(0.38 * SR), array("d"), 0.0
        r = random.Random(1)
        for i in range(n):
            t = i / SR
            ph += 2 * math.pi * (48 + 170 * math.exp(-t / 0.035)) / SR
            v = math.sin(ph) * math.exp(-t / 0.17) + 0.5 * math.exp(-t / 0.004) * r.uniform(-1, 1)
            out.append(math.tanh(1.8 * v))
        return out
    return cached("kick", f)


def tom(pitch=1.0):
    def f():
        n, out, ph = int(0.7 * SR), array("d"), 0.0
        nz = onepole(noise(n, 77), 900)
        for i in range(n):
            t = i / SR
            ph += 2 * math.pi * (70 + 110 * math.exp(-t / 0.06)) * pitch / SR
            out.append(math.tanh(1.5 * (math.sin(ph) * math.exp(-t / 0.28) + 0.6 * nz[i] * math.exp(-t / 0.05))))
        return out
    return cached(("tom", pitch), f)


def snare():
    def f():
        n = int(0.32 * SR)
        nz = hp(noise(n, 11), 1200)
        tone = array("d", (math.sin(2 * math.pi * 190 * i / SR) for i in range(n)))
        body = array("d", map(add, times(nz, expdecay(n, 0.11)), scaled(times(tone, expdecay(n, 0.05)), 0.6)))
        room = zeros(n + int(0.12 * SR))
        add_into(room, body, 0)
        for d, g in ((0.013, .45), (0.027, .35), (0.041, .25), (0.067, .18), (0.093, .1)):
            add_into(room, body, int(d * SR), g)
        return room
    return cached("snare", f)


def hat(open_=False):
    def f():
        n = int((0.22 if open_ else 0.045) * SR)
        return times(hp(hp(noise(n, 5 if open_ else 4), 7000), 7000), expdecay(n, 0.07 if open_ else 0.012))
    return cached(("hat", open_), f)


def impact():
    """Boom + noise burst + crash: drops, cold open and the final hit."""
    def f():
        n, out, ph = int(2.6 * SR), array("d"), 0.0
        burst, crash = onepole(noise(n, 21), 3000), hp(noise(n, 22), 5000)
        for i in range(n):
            t = i / SR
            ph += 2 * math.pi * (38 + 90 * math.exp(-t / 0.12)) / SR
            v = 1.1 * math.sin(ph) * math.exp(-t / 0.9) + 0.9 * burst[i] * math.exp(-t / 0.12) + 0.35 * crash[i] * math.exp(-t / 1.1)
            out.append(math.tanh(1.4 * v))
        return out
    return cached("impact", f)


def sweep(seconds, rising, seed):
    """Noise riser (rising) or downlifter (falling) with a pitched tone underneath."""
    def f():
        n, nz = int(seconds * SR), noise(int(seconds * SR), seed)
        out, y, ph = array("d"), 0.0, 0.0
        for i in range(n):
            x = i / n if rising else 1 - i / n
            a = math.exp(-2 * math.pi * (250 + 9000 * x * x) / SR)
            y = (1 - a) * nz[i] + a * y
            ph += 2 * math.pi * (120 + 1500 * x * x) / SR
            out.append((0.8 * y + 0.25 * math.sin(ph)) * (x ** 1.6 if rising else x ** 1.5))
        return out
    return cached(("sweep", round(seconds, 3), rising), f)


def reverse_swell(seconds=0.6):
    return cached(("rev", seconds), lambda: array("d", (x * (i / int(seconds * SR)) ** 3 for i, x in
                                                       enumerate(onepole(noise(int(seconds * SR), 41), 4000)))))


def whoosh(seconds=0.35):
    def f():
        n, nz = int(seconds * SR), noise(int(seconds * SR), 61)
        out, y = array("d"), 0.0
        for i in range(n):
            x = i / n
            a = math.exp(-2 * math.pi * (500 + 6000 * math.sin(math.pi * x) ** 2) / SR)
            y = (1 - a) * nz[i] + a * y
            out.append(y * math.sin(math.pi * x) ** 2 * 1.5)
        return out
    return cached(("whoosh", seconds), f)


def blip(freq, seconds, tau):
    return cached(("blip", freq, seconds, tau), lambda: array("d", (math.sin(2 * math.pi * freq * i / SR) * math.exp(-(i / SR) / tau)
                                                                     for i in range(int(seconds * SR)))))


def stamp():
    return cached("stamp", lambda: array("d", map(add, scaled(tom(0.6), 0.9),
                                                  scaled(times(onepole(noise(int(0.7 * SR), 71), 800), expdecay(int(0.7 * SR), 0.08)), 0.6))))


def brass(chord, seconds=0.42):
    """Synth-brass stab: bright supersaw with a fast filter envelope (bright -> dark crossfade)."""
    def f():
        n = int(seconds * SR)
        hi, lo = zeros(n), zeros(n)
        for m in chord:
            add_into(hi, supersaw(m, n, 18, (-2, 0, 2)), 0, 1 / len(chord))
            add_into(lo, supersaw(m, n, 4, (-2, 0, 2)), 0, 1 / len(chord))
        e = expdecay(n, 0.09)
        return times(array("d", map(lambda h, l, w: h * w + l * (1 - w), hi, lo, e)), env(n, 0.004, 0.15, 0.55, 0.12))
    return cached(("brass", tuple(chord), seconds), f)


def pad(chord, seconds, bright=14):
    def f():
        n, out = int(seconds * SR), zeros(int(seconds * SR))
        for m in chord:
            add_into(out, supersaw(m, n, bright), 0, 1 / len(chord))
        return times(out, env(n, 0.02, 0.3, 0.8, 0.25))
    return cached(("pad", tuple(chord), round(seconds, 3), bright), f)


def bass_note(m, seconds, growl=True):
    def f():
        n = int(seconds * SR)
        f0 = hz(m - 12)
        s = array("d", (math.sin(2 * math.pi * f0 * i / SR) for i in range(n)))
        if growl:
            g, e = supersaw(m, n, 10, (-1, 0, 1)), expdecay(n, 0.08)
            s = array("d", map(lambda a, b, w: a * 0.85 + b * (0.25 + 0.5 * w), s, g, e))
        return times(s, env(n, 0.003, 0.05, 0.85, 0.03))
    return cached(("bass", m, round(seconds, 4), growl), f)


def pluck(m):
    def f():
        f0 = hz(m)
        return array("d", ((math.sin(2 * math.pi * f0 * i / SR) + 0.4 * math.sin(4 * math.pi * f0 * i / SR))
                           * math.exp(-(i / SR) / 0.09) * min(1, i / 40) * 0.6 for i in range(int(0.35 * SR))))
    return cached(("pluck", m), f)


# ----------------------------------------------------------------------------- arrangement
CH = {"Dm": ([62, 65, 69], 38), "Bb": ([58, 62, 65], 34), "F": ([60, 65, 69], 41), "C": ([60, 64, 67], 36),
      "Gm": ([62, 67, 70], 43), "D": ([62, 66, 69], 38), "A": ([61, 64, 69], 45)}
PROG = {"intro": ["Dm", "Dm", "Bb", "A"], "build": ["Dm", "Bb", "Gm", "A"], "drop": ["Dm", "Bb", "F", "C"],
        "drive": ["Dm", "Bb", "F", "C"], "groove": ["Bb", "F", "C", "Dm", "Bb"], "tight": ["Dm", "Dm", "Bb", "C"],
        "lift": ["Bb", "C", "Dm", "C"], "breakdown": ["Bb", "F", "C", "Dm", "Bb", "A"], "final": ["Dm", "D", "D", "D"]}
FULL = ("drop", "drive", "tight", "lift", "groove")


def render(bpm, tl, total):
    B = 60.0 / bpm
    n = int((total + 2.5) * SR)
    drums, pump, fx = [zeros(n), zeros(n)], [zeros(n), zeros(n)], zeros(n)
    rev = zeros(n)
    log, drops, finals, silences = [], [], [], []

    def put(bufs, src, t, g=1.0, pan=0.0):
        if isinstance(bufs, list):
            add_into(bufs[0], src, int(t * SR), g * (1 - pan))
            add_into(bufs[1], src, int(t * SR), g * (1 + pan))
        else:
            add_into(bufs, src, int(t * SR), g)

    for idx, s in enumerate(tl):
        mode = s["mode"] or ("drop" if s["drop"] else "final" if s["final"] else "drive")
        S, prog = s["start"], PROG[mode]
        nxt = tl[idx + 1] if idx + 1 < len(tl) else None
        sec = {"scene": s["id"], "name": s["name"], "mode": mode, "start": round(S, 3), "end": round(s["end"], 3),
               "bars": s["bars"], "chords": []}
        if idx == 0 or s["drop"]:
            put(fx, impact(), S, 0.75 if idx == 0 else 0.6)
            if s["drop"]:
                drops.append(round(S, 3))
                put(fx, sweep(4 * B, False, 52), S + 0.25, 0.16)
        for bar in range(s["bars"]):
            t0 = S + bar * 4 * B
            ch = prog[bar % len(prog)]
            notes, root = CH[ch]
            sec["chords"].append(ch)
            if mode == "final":
                continue
            # harmony
            if mode in ("drop", "drive", "lift"):
                put(pump, pad([m + 12 for m in notes] + [notes[0]], 4 * B + 0.1, 16 if mode == "drop" else 12), t0, 0.15)
            elif mode == "groove":
                put(pump, pad([m + 12 for m in notes], 4 * B + 0.1, 9), t0, 0.11)
            elif mode == "breakdown":
                put(pump, pad([m + 12 for m in notes], 4 * B + 0.3, 6 + bar * 2), t0, 0.12 + 0.02 * bar)
                for k in range(16):
                    m = ([x + 12 for x in notes] + [notes[0] + 24])[k % 4]
                    put(pump, pluck(m), t0 + k * B / 4, 0.05 + 0.01 * bar, 0.3 if k % 2 else -0.3)
            elif mode in ("intro", "build"):
                put(pump, pad([m + 12 for m in notes], 4 * B + 0.2, 5 if mode == "intro" else 4 + bar * 3), t0, 0.08 + 0.02 * bar)
            elif mode == "tight":
                for b in (0, 1.5, 2.5):
                    put(pump, brass(notes), t0 + b * B, 0.08)
            # bass
            if mode in FULL or (mode == "build" and bar >= 1) or (mode == "breakdown" and bar >= 4):
                step = B / 4 if mode == "tight" else B / 2
                for k in range(int(round(4 * B / step))):
                    put(pump, bass_note(root, step * 0.9, growl=(mode != "build")), t0 + k * step, 0.30)
            elif mode in ("intro", "breakdown"):
                put(pump, bass_note(root, 4 * B, growl=False), t0, 0.22)
            # drums
            for b in range(4):
                tb = t0 + b * B
                if mode in FULL or (mode == "build" and bar < s["bars"] - 1) or (mode == "breakdown" and bar >= 4):
                    put(drums, kick(), tb, 0.62)
                if mode in FULL and b in (1, 3):
                    put(drums, snare(), tb, 0.28)
                    put(rev, snare(), tb, 0.12)
                if mode in FULL + ("build",) or (mode == "breakdown" and bar >= 4):
                    for q in range(4):
                        put(drums, hat(), tb + q * B / 4, 0.07 if q % 2 else 0.045, 0.25)
                    if mode in ("drop", "drive"):
                        put(drums, hat(True), tb + B / 2, 0.05, 0.3)
                if mode in ("intro", "tight", "build") and b in (0, 2):
                    put(drums, tom(1.0), tb, 0.45, -0.15)
                if mode == "intro" and b == 3:
                    put(drums, tom(1.25), tb + B / 2, 0.4, 0.15)
            # snare roll into drops
            if (mode == "build" and bar == s["bars"] - 1) or (mode == "breakdown" and bar >= 4):
                div = 8 if bar == s["bars"] - 1 else 4
                for k in range(div * 4):
                    put(drums, snare(), t0 + k * B / div, 0.08 + 0.22 * k / (div * 4))
        # slams on the beat grid
        for beat, kind in s["hits"]:
            th = S + beat * B
            notes, _ = CH[prog[int(beat // 4) % len(prog)]]
            if kind == "stab":
                put(pump, brass([m + 12 for m in notes]), th, 0.22)
                put(drums, tom(0.9), th, 0.3)
            elif kind == "tom":
                put(drums, tom(1.1), th, 0.55)
            elif kind == "impact":
                put(fx, impact(), th, 0.4)
            elif kind == "stamp":
                put(drums, stamp(), th, 0.55)
            elif kind in ("click", "tick"):
                put(fx, blip(2600, 0.03, 0.005), th, 0.35 if kind == "click" else 0.18)
            elif kind == "whoosh":
                put(fx, whoosh(0.3), th - 0.15, 0.25)
        # transitions: a whoosh on every cut; riser + reverse swell into drops
        if nxt:
            put(fx, whoosh(0.35), nxt["start"] - 0.18, 0.22)
            if nxt["drop"]:
                rl = (8 if s["bars"] >= 4 else 4) * B
                put(fx, sweep(rl, True, 31), nxt["start"] - rl, 0.42)
                put(fx, reverse_swell(0.6), nxt["start"] - 0.6, 0.35)
        if mode == "final":   # beat 0 final hit, beat 1 silence, beat 2 logo sting
            finals.append(round(S, 3))
            put(fx, impact(), S, 0.55)
            put(pump, brass([m + 12 for m in CH["Dm"][0]] + [50], 0.9 * B), S, 0.4)
            put(drums, tom(0.8), S, 0.6)
            st = S + 2 * B
            sting = pad([62, 66, 69, 74, 78], 6.0, 16)
            put(pump, sting, st, 0.26)
            put(pump, brass([62, 66, 69, 74]), st, 0.3)
            put(pump, bass_note(38, 5.5, growl=False), st, 0.35)
            put(rev, sting, st, 0.25)
            for k, m in enumerate((74, 78, 81, 86)):
                put(pump, pluck(m), st + 0.5 + k * B / 2, 0.12, 0.3 if k % 2 else -0.3)
            silences.append((S + B, st))
            sec.update({"final_hit": round(S, 3), "silence": [round(S + B, 3), round(st, 3)], "logo_sting": round(st, 3)})
        log.append(sec)
    # sidechain pump on chords + bass after every kick (5 ms blocks)
    blk = 220
    for buf in pump:
        for b in range(0, len(buf), blk):
            t = b / SR
            sc = next((x for x in tl if x["start"] <= t < x["end"]), tl[-1])
            mode = sc["mode"] or ("drop" if sc["drop"] else "final" if sc["final"] else "drive")
            depth = 0.65 if mode in FULL else (0.0 if mode == "final" else 0.25)
            if depth:
                g = 1 - depth * math.exp(-((t - sc["start"]) % B) / 0.11)
                seg = buf[b:b + blk]
                buf[b:b + blk] = array("d", map(mul, seg, repeat(g, len(seg))))
    revL, revR = zeros(n), zeros(n)
    for d, g in ((0.029, .5), (0.061, .4), (0.097, .32), (0.149, .25), (0.211, .18), (0.307, .12), (0.43, .07)):
        add_into(revL, rev, int(d * SR), g)
        add_into(revR, rev, int(d * 1.17 * SR), g)
    outL = array("d", map(add, map(add, map(add, drums[0], pump[0]), revL), fx))
    outR = array("d", map(add, map(add, map(add, drums[1], pump[1]), revR), fx))
    for a_, b_ in silences:           # hard silence between the final hit and the sting
        a, b, fd = int(a_ * SR), int(b_ * SR), int(0.03 * SR)
        for buf in (outL, outR):
            for i in range(a - fd, a):
                buf[i] *= (a - i) / fd
            buf[a:b] = zeros(b - a)
    return outL, outR, {"sections": log, "drops": drops, "final": finals}


# ----------------------------------------------------------------------------- voice
def read_mono(path):
    with wave.open(path, "rb") as w:
        assert w.getframerate() == SR and w.getsampwidth() == 2, path
        ch, raw = w.getnchannels(), w.readframes(w.getnframes())
    a = array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    if ch == 2:
        a = a[0::2]
    return array("d", map(mul, a, repeat(1 / 32768.0, len(a))))


def voice_eq(x):
    """High-pass 90 Hz, then about +2 dB presence around 3-5 kHz (parallel band boost)."""
    y = hp(x, 90)
    band = onepole(hp(y, 2800), 5500)
    return array("d", map(lambda a, b: a + 0.33 * b, y, band))


def render_vo(tl, n, lines):
    vo, placed = zeros(n), []
    for s in tl:
        for key, off in s["vo"]:
            path = os.path.join(VO_DIR, key + ".wav")
            buf = voice_eq(read_mono(path))
            t = s["start"] + off
            add_into(vo, buf, int(t * SR))
            placed.append({"line": key, "scene": s["id"], "t": round(t, 3), "dur": round(len(buf) / SR, 2),
                           "speaker": lines.get(key, {}).get("speaker"), "text": lines.get(key, {}).get("text")})
    # normalise speech to about -16 LUFS (approximated by RMS over voiced 50 ms blocks)
    blk, acc, cnt = int(0.05 * SR), 0.0, 0
    for b in range(0, n, blk):
        seg = vo[b:b + blk]
        e = sum(map(mul, seg, seg)) / max(1, len(seg))
        if e > 1e-5:
            acc, cnt = acc + e, cnt + 1
    rms = math.sqrt(acc / max(cnt, 1))
    g = 10 ** (-16 / 20) / max(rms, 1e-9)
    return scaled(vo, g), placed


def duck_gains(vo, n, depth_db=-9.0, block=220, attack=0.02, release=0.25):
    low = 10 ** (depth_db / 20)
    nb = n // block + 1
    act = [max(map(abs, vo[b * block:(b + 1) * block]), default=0) > 0.02 for b in range(nb)]
    look = int(0.03 * SR / block) + 1
    a_c, r_c = 1 - math.exp(-block / (attack * SR)), 1 - math.exp(-block / (release * SR))
    out, g = [], 1.0
    for b in range(nb):
        target = low if any(act[b:b + look]) else 1.0
        g += (target - g) * (a_c if target < g else r_c)
        out.append(g)
    return out, block


def apply_blocks(buf, gains, block):
    for b, g in enumerate(gains):
        a, z = b * block, min(len(buf), (b + 1) * block)
        if a >= z:
            break
        if g < 0.999:
            buf[a:z] = array("d", map(mul, buf[a:z], repeat(g, z - a)))


# ----------------------------------------------------------------------------- master + files
def fade_tail(L, R, n, seconds=1.2):
    fo = int(seconds * SR)
    for buf in (L, R):
        for i in range(n - fo, n):
            buf[i] *= (n - i) / fo


def loudness(L, R):
    n = len(L)
    pk = max(max(L), -min(L), max(R), -min(R))
    ss = sum(map(mul, L, L)) + sum(map(mul, R, R))
    return {"peak_dbfs": round(20 * math.log10(max(pk, 1e-9)), 2), "rms_dbfs": round(20 * math.log10(max(math.sqrt(ss / (2 * n)), 1e-9)), 2)}


def saturate(L, R, target_rms_db=-12.6, ceiling_db=-1.0):
    """Drive into a tanh soft-clip with a -1 dBFS ceiling: dense, loud, no hard clipping."""
    ss = sum(map(mul, L, L)) + sum(map(mul, R, R))
    rms = math.sqrt(ss / (2 * len(L)))
    drive = 10 ** (target_rms_db / 20) / max(rms, 1e-9) * 1.3
    c = 10 ** (ceiling_db / 20)
    f = lambda x: c * math.tanh(x * drive / c)
    return array("d", map(f, L)), array("d", map(f, R))


def limit(L, R, ceiling_db=-1.0):
    c = 10 ** (ceiling_db / 20)
    k = 0.85 * c

    def f(x):
        a = abs(x)
        if a <= k:
            return x
        y = k + (c - k) * math.tanh((a - k) / (c - k))
        return y if x > 0 else -y
    return array("d", map(f, L)), array("d", map(f, R))


def write_wav(path, L, R):
    n = len(L)
    out = array("h", bytes(4 * n))
    out[0::2] = array("h", map(int, map(mul, L, repeat(32767.0, n))))
    out[1::2] = array("h", map(int, map(mul, R, repeat(32767.0, n))))
    if sys.byteorder == "big":
        out.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(out.tobytes())
    if shutil.which("afconvert"):
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "192000", path, path[:-4] + ".m4a"], check=True)


def build(cut):
    bpm, scenes, cuts = parse_film()
    tl, total = timeline(bpm, scenes, cuts[cut])
    n = int(total * SR)
    print(f"[{cut}] {bpm:g} BPM, {sum(s['bars'] for s in tl)} bars, {total:.3f} s")
    L, R, info = render(bpm, tl, total)
    L, R = L[:n], R[:n]
    mL, mR = saturate(L[:], R[:])
    fade_tail(mL, mR, n)
    write_wav(os.path.join(HERE, f"kairos_theme_{cut}.wav"), mL, mR)
    lines = json.load(open(os.path.join(VO_DIR, "lines.json"), encoding="utf-8"))
    vo, placed = render_vo(tl, n, lines)
    write_wav(os.path.join(HERE, f"vo_{cut}.wav"), *limit(vo[:], vo[:]))
    gains, blk = duck_gains(vo, n)
    sL, sR = mL[:], mR[:]
    apply_blocks(sL, gains, blk)
    apply_blocks(sR, gains, blk)
    sL, sR = limit(array("d", map(add, sL, vo)), array("d", map(add, sR, vo)))
    write_wav(os.path.join(HERE, f"soundtrack_{cut}.wav"), sL, sR)
    B = 60 / bpm
    beat_map = {"cut": cut, "bpm": bpm, "beat_s": round(B, 5), "bar_s": round(4 * B, 5), "runtime_s": total,
                "drops_s": info["drops"], "final_hit_s": info["final"], "sections": info["sections"], "vo": placed,
                "hits": [{"scene": s["id"], "beat": b, "t": round(s["start"] + b * B, 3), "kind": k} for s in tl for b, k in s["hits"]],
                "loudness": {"music": loudness(mL, mR), "soundtrack": loudness(sL, sR), "vo_target": "-16 LUFS (RMS approx.)",
                             "duck": "-9 dB, 20 ms attack, 250 ms release"}}
    json.dump(beat_map, open(os.path.join(HERE, f"beat_map_{cut}.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    for s in info["sections"]:
        x = f"  hit {s['final_hit']} silence {s['silence']} sting {s['logo_sting']}" if "final_hit" in s else ""
        print(f"  {s['start']:7.3f}-{s['end']:7.3f} {s['scene']:4s} {s['mode']:9s} {s['bars']} bars {' '.join(s['chords'])}{x}")
    for v in placed:
        print(f"  VO {v['t']:7.3f} +{v['dur']:4.2f}s {v['line']}")
    print(f"  drops {info['drops']} · {len(beat_map['hits'])} slams · music {beat_map['loudness']['music']} · soundtrack {beat_map['loudness']['soundtrack']}")
    return beat_map


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cut", default="all")
    a = ap.parse_args()
    _, _, cuts = parse_film()
    for c in (cuts if a.cut == "all" else [a.cut]):
        build(c)
