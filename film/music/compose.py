#!/usr/bin/env python3
"""Kairos Theme: original music, draft character voices and UI foley for the Kairos film.

Everything is generated here from code (Python standard library only, fixed random seed):
no samples, no loops, no third-party audio. The scene table (ids, durations, voice-over
cues and speakers) is read from ../kairos_film.html, so the music hits the film's scene
changes and every spoken line starts on its caption.

    python3 film/music/compose.py              # both cuts, music + voices + soundtrack + .m4a
    python3 film/music/compose.py --cut operator
    python3 film/music/compose.py --no-voice    # music and foley only (no macOS `say` needed)

Writes, per cut (full | operator), into film/music/:
    kairos_theme_<cut>.wav   music only            (44.1 kHz stereo, 16-bit)
    vo_<cut>.wav             voices only (draft macOS TTS)
    soundtrack_<cut>.wav     music ducked ~10 dB under every line + voices + foley
    *.m4a                    AAC copies of the three files, used by kairos_film.html
    cue_sheet_<cut>.json     sections, bars, chords, voice placements, foley (timing log)

Voices (draft TTS, macOS): narrator Rishi (en_IN), Ravi Aman (en_IN), Ritu Lekha (hi_IN),
Priya Tara (en_IN). A final mix could use Sarvam TTS (Ritu / Advait) instead.
"""
from __future__ import annotations

import argparse
import hashlib
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
BPM = 96.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT
SEED = 20261120
HERE = os.path.dirname(os.path.abspath(__file__))
FILM = os.path.join(HERE, "..", "kairos_film.html")
CACHE = os.path.join(HERE, ".cache")

# --------------------------------------------------------------------------------------
# 1. Scene table from the film
# --------------------------------------------------------------------------------------
CUE_RE = re.compile(r'\[\s*([\d.]+)\s*,\s*(`[^`]*`|CONFIG\.spoken\.\w+)\s*(?:,\s*"(\w+)")?\s*(?:,\s*(`[^`]*`))?\s*\]')


def parse_film(path):
    src = open(path, encoding="utf-8").read()
    product = re.search(r'product:\s*"([^"]+)"', src).group(1)
    borrower = re.search(r'borrower:\s*"([^"]+)"', src).group(1)
    opname = re.search(r'operator:\s*\{[^}]*?name:\s*"([^"]+)"', src, re.S).group(1)
    spoken = dict(re.findall(r'^\s*(\w+):\s*"([^"]+)"', src[src.index("spoken: {"):src.index("operator: {")], re.M))
    subs = {"${B}": borrower, "${P}": product, "${PR}": opname}

    def text(tok):
        if tok.startswith("CONFIG.spoken."):
            return spoken[tok.split(".")[-1]]
        t = tok.strip("`")
        for k, v in subs.items():
            t = t.replace(k, v)
        return t

    table = src[src.index("const ALL_SCENES = ["):src.index("const GROUP =")]
    scenes = {}
    for chunk in table.split('{id:"')[1:]:
        sid = chunk[:chunk.index('"')]
        dur = float(re.search(r"dur:([\d.]+)", chunk).group(1))
        body = chunk[chunk.index("cues:["):]
        main, _, op = body.partition("opCues:[")

        def cues(block):
            out = []
            for m in CUE_RE.finditer(block):
                out.append({"t": float(m.group(1)), "caption": text(m.group(2)), "speaker": m.group(3),
                            "say": text(m.group(4)) if m.group(4) else None})
            return out
        scenes[sid] = {"id": sid, "dur": dur, "cues": cues(main), "opCues": cues(op) if op else None}
    cuts = {}
    for name in ("full", "operator"):
        ids = re.search(name + r':\s*\[([^\]]*)\]', src[src.index("const CUTS"):]).group(1)
        cuts[name] = re.findall(r'"(\w+)"', ids)
    return scenes, cuts, {"product": product, "borrower": borrower, "operator": opname}


def timeline(scenes, cut_ids, cut):
    out, t = [], 0.0
    for sid in cut_ids:
        s = dict(scenes[sid])
        if cut == "operator" and s["opCues"]:
            s["cues"] = s["opCues"]
        s["start"], s["end"] = t, t + s["dur"]
        t += s["dur"]
        out.append(s)
    return out, t


# --------------------------------------------------------------------------------------
# 2. Buffer helpers (array('d') + map() so the heavy loops run in C)
# --------------------------------------------------------------------------------------
def zeros(n):
    return array("d", bytes(8 * n))


def add_into(dst, src, start, gain=1.0):
    if start < 0:
        src = src[-start:]
        start = 0
    n = min(len(src), len(dst) - start)
    if n <= 0:
        return
    s = src if len(src) == n else src[:n]
    seg = dst[start:start + n]
    if gain == 1.0:
        dst[start:start + n] = array("d", map(add, seg, s))
    else:
        dst[start:start + n] = array("d", map(add, seg, map(mul, s, repeat(gain, n))))


def scaled(buf, g):
    return array("d", map(mul, buf, repeat(g, len(buf))))


def times(a, b):
    n = min(len(a), len(b))
    return array("d", map(mul, a[:n], b[:n]))


_env_cache = {}


def env_adsr(n, a, d, s_level, r):
    """Attack/decay/sustain/release envelope over n samples (a, d, r in seconds)."""
    key = (n, a, d, s_level, r)
    if key in _env_cache:
        return _env_cache[key]
    A, D, R = int(a * SR), int(d * SR), int(r * SR)
    out = array("d")
    for i in range(n):
        if i < A:
            v = i / max(A, 1)
        elif i < A + D:
            v = 1 - (1 - s_level) * (i - A) / max(D, 1)
        else:
            v = s_level
        if i > n - R:
            v *= max(0.0, (n - i) / max(R, 1))
        out.append(v)
    _env_cache[key] = out
    return out


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


# --------------------------------------------------------------------------------------
# 3. Instruments (all synthesised)
# --------------------------------------------------------------------------------------
_cycle_cache = {}


def saw_cycle(period, harmonics):
    """One band-limited saw period (additive), integer length."""
    key = (period, harmonics)
    if key not in _cycle_cache:
        c = array("d", (sum(math.sin(2 * math.pi * h * i / period) / h * (1.0 / (1 + (h / harmonics) ** 2))
                            for h in range(1, harmonics + 1)) * 0.55 for i in range(period)))
        _cycle_cache[key] = c
    return _cycle_cache[key]


_tone_cache = {}


def pad_tone(m, bright, n, side):
    """Detuned saw pair (periods P and P+1 give a natural chorus). bright: 'hi' or 'lo' harmonics."""
    key = (m, bright, n, side)
    if key in _tone_cache:
        return _tone_cache[key]
    P = int(round(SR / midi_hz(m)))
    H = 9 if bright == "hi" else 3
    a, b = saw_cycle(P, H), saw_cycle(P + (1 if side == 0 else -1), H)
    ta = (a * (n // len(a) + 1))[:n]
    off = (side * 37) % len(b)
    tb = ((b * (n // len(b) + 2))[off:off + n])
    out = array("d", map(add, ta, tb))
    _tone_cache[key] = out
    return out


def pad_chord(notes, seconds, side, brightness):
    """Pad with a filter-like envelope: crossfade dark -> bright -> dark."""
    n = int(seconds * SR)
    lo = zeros(n)
    hi = zeros(n)
    for m in notes:
        add_into(lo, pad_tone(m, "lo", n, side), 0, 1.0 / len(notes))
        add_into(hi, pad_tone(m, "hi", n, side), 0, 1.0 / len(notes))
    amp = env_adsr(n, 0.6, 0.8, 0.85, 0.9)
    open_ = env_adsr(n, 1.0, 1.2, 0.35, 0.6)
    w = scaled(open_, brightness)
    inv = array("d", map(lambda x: 1.0 - x, w))
    mixed = array("d", map(add, map(mul, lo, inv), map(mul, hi, w)))
    return times(mixed, amp)


_pluck_cache = {}


def pluck(m, seconds=0.9):
    if m in _pluck_cache:
        return _pluck_cache[m]
    f = midi_hz(m)
    n = int(seconds * SR)
    parts = [(1.0, 0.42), (0.45, 0.22), (0.22, 0.12), (0.10, 0.07)]
    out = array("d")
    w = 2 * math.pi * f / SR
    for i in range(n):
        t = i / SR
        v = 0.0
        for h, (amp, tau) in enumerate(parts, 1):
            v += amp * math.exp(-t / tau) * math.sin(w * h * i)
        a = min(1.0, i / 90.0)
        out.append(v * a * 0.5)
    _pluck_cache[m] = out
    return out


_sub_cache = {}


def sub(m, seconds):
    key = (m, seconds)
    if key in _sub_cache:
        return _sub_cache[key]
    n = int(seconds * SR)
    P = SR / midi_hz(m)
    out = array("d", (math.sin(2 * math.pi * i / P) for i in range(n)))
    out = times(out, env_adsr(n, 0.02, 0.3, 0.7, 0.25))
    _sub_cache[key] = out
    return out


RNG = random.Random(SEED)


def noise(n, rng=None):
    r = rng or RNG
    return array("d", (r.uniform(-1, 1) for _ in range(n)))


def onepole(buf, cutoff):
    a = math.exp(-2 * math.pi * cutoff / SR)
    out = array("d")
    y = 0.0
    for x in buf:
        y = (1 - a) * x + a * y
        out.append(y)
    return out


def highpass(buf, cutoff):
    lo = onepole(buf, cutoff)
    return array("d", map(lambda x, l: x - l, buf, lo))


_drum = {}


def kick():
    if "kick" not in _drum:
        n = int(0.42 * SR)
        out = array("d")
        ph = 0.0
        for i in range(n):
            t = i / SR
            f = 45 + 95 * math.exp(-t / 0.045)
            ph += 2 * math.pi * f / SR
            out.append(math.sin(ph) * math.exp(-t / 0.16) * (min(1, i / 40)))
        _drum["kick"] = out
    return _drum["kick"]


def clap():
    if "clap" not in _drum:
        rng = random.Random(SEED + 1)
        n = int(0.32 * SR)
        nz = highpass(noise(n, rng), 900)
        e = array("d")
        for i in range(n):
            t = i / SR
            bursts = sum(math.exp(-(t - o) / 0.006) for o in (0, 0.011, 0.023) if t >= o)
            e.append(0.6 * bursts + math.exp(-t / 0.09) * 0.8 if t > 0.023 else 0.6 * bursts)
        _drum["clap"] = times(onepole(nz, 5000), e)
    return _drum["clap"]


def hat(open_=False):
    key = "hat_o" if open_ else "hat_c"
    if key not in _drum:
        rng = random.Random(SEED + (3 if open_ else 2))
        n = int((0.28 if open_ else 0.06) * SR)
        nz = highpass(highpass(noise(n, rng), 6000), 6000)
        tau = 0.09 if open_ else 0.016
        _drum[key] = array("d", (x * math.exp(-(i / SR) / tau) for i, x in enumerate(nz)))
    return _drum[key]


_fx = {}


def crowd(seconds, seed, swell=False):
    """Crowd-like murmur / cheer from filtered noise with slow random amplitude."""
    key = ("crowd", seconds, seed, swell)
    if key in _fx:
        return _fx[key]
    rng = random.Random(seed)
    n = int(seconds * SR)
    band = onepole(highpass(noise(n, rng), 300), 2200)
    out = array("d")
    am, target = 0.6, 0.6
    for i, x in enumerate(band):
        if i % 2205 == 0:
            target = 0.45 + 0.55 * rng.random()
        am += (target - am) * 0.0004
        if swell:
            t = i / n
            shape = math.sin(math.pi * min(1.0, t * 1.15)) ** 1.5
        else:
            shape = 1.0
        out.append(x * am * shape * 2.2)
    _fx[key] = out
    return out


def whoosh(seconds=1.0):
    key = ("whoosh", seconds)
    if key not in _fx:
        rng = random.Random(SEED + 7)
        n = int(seconds * SR)
        nz = noise(n, rng)
        out = array("d")
        y = 0.0
        for i, x in enumerate(nz):
            t = i / n
            c = 300 + 4200 * math.sin(math.pi * t) ** 2
            a = math.exp(-2 * math.pi * c / SR)
            y = (1 - a) * x + a * y
            out.append(y * math.sin(math.pi * t) ** 2 * 1.6)
        _fx[key] = out
    return _fx[key]


def blip(freq, seconds, tau):
    key = ("blip", freq, seconds, tau)
    if key not in _fx:
        n = int(seconds * SR)
        _fx[key] = array("d", (math.sin(2 * math.pi * freq * i / SR) * math.exp(-(i / SR) / tau) * min(1, i / 30)
                              for i in range(n)))
    return _fx[key]


def foley(kind):
    """Small tactile UI sounds."""
    if kind == "click":
        b = array("d", map(add, blip(2400, 0.04, 0.006), scaled(highpass(noise(int(0.04 * SR), random.Random(5)), 3000), 0.15)))
        return scaled(b, 0.6)
    if kind == "tick":
        return scaled(blip(3200, 0.03, 0.004), 0.35)
    if kind == "pop":
        return scaled(array("d", map(add, blip(880, 0.12, 0.03), blip(1320, 0.12, 0.02))), 0.35)
    if kind == "chime":
        return scaled(array("d", map(add, blip(1046.5, 0.9, 0.35), scaled(blip(1568, 0.9, 0.25), 0.6))), 0.35)
    if kind == "buzz":
        n = int(0.38 * SR)
        return array("d", (0.5 * math.copysign(1, math.sin(2 * math.pi * 150 * i / SR)) * (0.5 + 0.5 * math.sin(2 * math.pi * 28 * i / SR))
                           * min(1, i / 200) * min(1, (n - i) / 400) * 0.35 for i in range(n)))
    if kind == "thunk":
        return array("d", map(add, scaled(blip(90, 0.3, 0.08), 0.9), scaled(onepole(noise(int(0.3 * SR), random.Random(9)), 900), 0.25)))
    if kind == "ring":
        return scaled(array("d", map(add, blip(660, 0.5, 0.2), blip(880, 0.5, 0.2))), 0.25)
    if kind == "swish":
        return scaled(whoosh(0.45), 0.25)
    if kind == "close":
        return array("d", map(add, scaled(blip(70, 0.45, 0.1), 0.7), scaled(onepole(noise(int(0.45 * SR), random.Random(11)), 500), 0.3)))
    raise ValueError(kind)


# --------------------------------------------------------------------------------------
# 4. Arrangement: one style per scene (sections follow the film's scene table)
# --------------------------------------------------------------------------------------
CH = {  # chord tones (pad), root for sub
    "Am": ([57, 60, 64], 45), "F": ([53, 57, 60, 64], 41), "C": ([55, 60, 64], 48), "G": ([55, 59, 62], 43),
    "Dm": ([53, 57, 62], 38), "Em": ([55, 59, 64], 40), "E": ([56, 59, 64], 40), "Cmaj7": ([55, 59, 60, 64], 48),
    "Fmaj7": ([53, 57, 60, 64], 41), "Dm7": ([53, 57, 60, 62], 38),
}
STYLES = {
    #        progression                    pad  bright arp          kick       clap  hats   sub   extra
    "tense":   (["Am", "Fmaj7", "Dm", "E"],   .55, .25, "sparse",    None,      False, "tick", True,  None),
    "lift":    (["C", "G", "Am", "F"],        .55, .65, "eighths",   None,      False, None,   True,  "rise"),
    "pulse":   (["Am", "F", "C", "G"],        .45, .5,  "eighths",   "half",    False, "8",    True,  None),
    "calm":    (["Fmaj7", "C", "Dm7", "G"],   .5,  .35, "quarters",  None,      False, None,   True,  None),
    "build":   (["Am", "F", "C", "G"],        .45, .55, "build",     "build",   False, "build", True, None),
    "uplift":  (["F", "G", "C", "Am"],        .5,  .8,  "sixteenths", "four",   True,  "8",    True,  None),
    "morning": (["Cmaj7", "Fmaj7"],           .5,  .45, "eighths",   None,      False, "8soft", True, None),
    "match":   (["C", "F", "G", "C"],         .42, .7,  "sixteenths", "four",   True,  "16",   True,  "crowd"),
    "result":  (["F", "G", "C", "Am"],        .5,  .75, "eighths",   "four",    True,  "8",    True,  "cheer"),
    "close":   (["F", "G", "C", "C"],         .6,  .5,  "close",     None,      False, None,   True,  None),
    "intro":   (["Cmaj7", "Fmaj7"],           .5,  .4,  "quarters",  None,      False, None,   True,  None),
}
SCENE_STYLE = {"s1": "tense", "s2": "tense", "s3": "lift", "s4": "pulse", "s4b": "pulse", "s5": "calm", "s6": "calm",
               "s7": "build", "s8": "uplift", "s9": "close", "oi": "intro", "o1": "morning", "o2": "match",
               "o3": "calm", "o4": "morning", "o5": "result", "oc": "close"}
HITS = {"s3", "s8", "o2", "o5"}            # soft impact + riser into these scenes
FOLEY = {  # scene id -> [(local time, kind, gain)]
    "s1": [(1.0, "buzz", 1), (2.6, "buzz", 1), (4.2, "buzz", 1), (5.8, "buzz", 1), (7.4, "buzz", 1)],
    "s3": [(5.2, "click", .8), (7.8, "tick", 1), (9.0, "tick", 1), (10.2, "tick", 1), (11.4, "pop", .8)],
    "s4": [(0.2, "pop", .8), (3.9, "pop", .8), (6.6, "pop", .7), (7.7, "pop", .7), (8.6, "ring", 1),
           (22.2, "click", .8), (23.6, "chime", 1), (25.2, "swish", 1)],
    "s4b": [(4.4, "tick", 1), (5.0, "tick", 1), (5.6, "tick", 1)],
    "s5": [(7.0, "tick", 1), (7.35, "tick", 1), (7.7, "tick", 1), (8.05, "tick", 1), (11.2, "thunk", 1), (13.5, "chime", .7)],
    "s6": [(2.8, "click", 1), (3.6, "tick", 1), (4.4, "tick", 1)],
    "s7": [(5.6, "pop", .7)],
    "o1": [(2.8, "chime", .7), (5.4, "tick", .8), (5.9, "tick", .8), (6.4, "tick", .8), (6.9, "tick", .8)],
    "o2": [(9.6, "pop", .7)],
    "o3": [(2.2, "pop", .9), (7.5, "click", 1), (9.0, "tick", 1), (9.4, "tick", 1), (9.8, "tick", 1), (10.2, "tick", 1), (11.2, "chime", .8)],
    "o4": [(2.8, "click", .8), (4.9, "click", .8), (7.0, "click", .8), (8.6, "click", .8), (10.0, "click", .8)],
    "o5": [(12.3, "close", 1)],
    "s8": [(7.0, "tick", 1)],
}
CROWD_SWELLS = {"o2": [(2.6, 2.0, .5), (6.4, 2.0, .5), (10.2, 2.0, .5), (14.0, 2.0, .5), (18.6, 3.2, 1.0)],
                "o5": [(0.6, 3.4, .9)]}


def render_music(tl, total, log):
    n = int((total + 3.0) * SR)
    L, R, sendL, sendR, fx = zeros(n), zeros(n), zeros(n), zeros(n), zeros(n)
    rng = random.Random(SEED)
    for s in tl:
        style = SCENE_STYLE.get(s["id"], "calm")
        prog, padg, bright, arp, kickp, clapp, hats, subp, extra = STYLES[style]
        S, D = s["start"], s["dur"]
        bars = []
        k = 0
        while k * BAR < D - 0.05:
            t0 = S + k * BAR
            chord = prog[k % len(prog)]
            if style == "close" and k >= 2:
                chord = "C"
            remain = min(BAR, S + D - t0)
            notes, root = CH[chord]
            last_bar = (t0 + BAR >= S + D - 0.05)
            hold = remain + (2.6 if (style == "close" and last_bar) else 0.9)
            for side, dst, sdst in ((0, L, sendL), (1, R, sendR)):
                pc = pad_chord(notes, hold, side, bright)
                add_into(dst, pc, int(t0 * SR), padg * 0.32)
                add_into(sdst, pc, int(t0 * SR), padg * 0.10)
            if subp:
                sb = sub(root - 12 if root >= 45 else root, round(min(remain, BAR) + 0.2, 3))
                g = 0.28 if style in ("match", "uplift", "pulse", "build", "result") else 0.2
                add_into(L, sb, int(t0 * SR), g)
                add_into(R, sb, int(t0 * SR), g)
            # arpeggio / pluck
            frac = k / max(1, int(D / BAR))
            if arp != "none":
                step = {"sparse": 2 * BEAT, "quarters": BEAT, "eighths": BEAT / 2, "sixteenths": BEAT / 4,
                        "build": BEAT / 2 if frac < 0.5 else BEAT / 4, "close": BEAT}[arp]
                tones = [m + 12 for m in notes] + [notes[0] + 24]
                j = 0
                tt = 0.0
                while tt < remain - 0.01:
                    if arp == "sparse" and rng.random() < 0.35:
                        tt += step
                        j += 1
                        continue
                    m = tones[(j * 2 + (j // len(tones))) % len(tones)] if arp != "close" else tones[min(j, len(tones) - 1)]
                    g = {"sparse": .16, "quarters": .14, "eighths": .12, "sixteenths": .09, "build": .1, "close": .15}[arp]
                    if arp == "build":
                        g *= 0.6 + 0.6 * frac
                    pan = 0.35 if j % 2 else -0.35
                    pl = pluck(m)
                    st = int((t0 + tt) * SR)
                    add_into(L, pl, st, g * (1 - pan))
                    add_into(R, pl, st, g * (1 + pan))
                    add_into(sendL, pl, st, g * 0.5)
                    add_into(sendR, pl, st, g * 0.5)
                    tt += step
                    j += 1
            # drums
            for b in range(4):
                tb = t0 + b * BEAT
                if tb >= S + D - 0.05:
                    break
                st = int(tb * SR)
                kk = (kickp == "four") or (kickp == "half" and b in (0, 2)) or (kickp == "build" and (frac >= 0.5 or b in (0, 2)))
                if kk:
                    g = 0.5 if kickp != "build" else 0.3 + 0.25 * frac
                    add_into(L, kick(), st, g)
                    add_into(R, kick(), st, g)
                if clapp and b in (1, 3):
                    add_into(L, clap(), st, 0.16)
                    add_into(R, clap(), st, 0.16)
                    add_into(sendL, clap(), st, 0.08)
                    add_into(sendR, clap(), st, 0.08)
                if hats:
                    sub_steps = {"tick": 2, "8": 2, "8soft": 2, "16": 4, "build": 2 if frac < 0.5 else 4}[hats]
                    hg = {"tick": .05, "8": .09, "8soft": .05, "16": .07, "build": .05 + .06 * frac}[hats]
                    for q in range(sub_steps):
                        if hats == "tick" and q == 0:
                            continue
                        sq = int((tb + q * BEAT / sub_steps) * SR)
                        acc = 1.0 if q % 2 else 0.7
                        add_into(R, hat(), sq, hg * acc)
                        add_into(L, hat(), sq, hg * acc * 0.7)
            bars.append({"bar": k + 1, "t": round(t0, 3), "chord": chord})
            k += 1
        log.append({"scene": s["id"], "style": style, "start": round(S, 3), "end": round(S + D, 3),
                    "bars": len(bars), "chords": [b["chord"] for b in bars]})
        # scene hits: soft riser into the scene and an impact on its first downbeat
        if s["id"] in HITS and S > 0:
            add_into(fx, whoosh(1.2), int((S - 1.1) * SR), 0.18)
            add_into(L, kick(), int(S * SR), 0.35)
            add_into(R, kick(), int(S * SR), 0.35)
            add_into(sendL, hat(True), int(S * SR), 0.4)
            add_into(sendR, hat(True), int(S * SR), 0.4)
        if extra == "crowd":
            bed = crowd(D, SEED + 21)
            add_into(L, bed, int(S * SR), 0.035)
            add_into(R, crowd(D, SEED + 22), int(S * SR), 0.035)
        for (lt, length, g) in CROWD_SWELLS.get(s["id"], []):
            sw = crowd(length, SEED + int(lt * 10) + 40, swell=True)
            add_into(L, sw, int((S + lt) * SR), 0.11 * g)
            add_into(R, crowd(length, SEED + int(lt * 10) + 41, swell=True), int((S + lt) * SR), 0.11 * g)
    # transitions: a soft whoosh across every cut
    for s in tl[1:]:
        add_into(fx, whoosh(0.9), int((s["start"] - 0.45) * SR), 0.10)
    # reverb (multi-tap, different per side) on the send bus
    for d, g in ((0.031, .42), (0.067, .34), (0.113, .27), (0.181, .2), (0.263, .14), (0.37, .09), (0.52, .05)):
        add_into(L, sendL, int(d * SR), g)
        add_into(R, sendR, int((d * 1.13) * SR), g)
    return L, R, fx


def render_foley(tl, n):
    out = zeros(n)
    for s in tl:
        for lt, kind, g in FOLEY.get(s["id"], []):
            add_into(out, foley(kind), int((s["start"] + lt) * SR), 0.5 * g)
    return out


# --------------------------------------------------------------------------------------
# 5. Voices (macOS `say`, draft) with auto-fit to the gap before the next line
# --------------------------------------------------------------------------------------
VOICES = {None: ("Rishi", 165), "ravi": ("Aman", 158), "ravi_inner": ("Aman", 150), "ritu": ("Lekha", 150),
          "priya": ("Tara", 165)}
SPEAKER_NAME = {None: "Narrator", "ravi": "Ravi", "ravi_inner": "Ravi (thinking)", "ritu": "Ritu (voice bot)",
                "priya": "Priya"}


def read_wav_mono(path):
    with wave.open(path, "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    assert sw == 2 and sr == SR, (sw, sr)
    a = array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    if ch == 2:
        a = a[0::2]
    return array("d", map(mul, a, repeat(1 / 32768.0, len(a))))


def tts(text, voice, rate):
    os.makedirs(CACHE, exist_ok=True)
    key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:16]
    wav = os.path.join(CACHE, key + ".wav")
    if not os.path.exists(wav):
        aiff = os.path.join(CACHE, key + ".aiff")
        subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", aiff, text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", "-c", "1", aiff, wav], check=True,
                       stdout=subprocess.DEVNULL)
        os.remove(aiff)
    return read_wav_mono(wav)


def clean_for_tts(text):
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)          # drop caption translations
    return text.replace("…", ",").replace("“", "").replace("”", "").replace("×", " times")


def render_voices(tl, n, log):
    vo, char = zeros(n), zeros(n)
    for s in tl:
        cues = s["cues"]
        for i, c in enumerate(cues):
            voice, rate = VOICES.get(c["speaker"], VOICES[None])
            text = c["say"] or clean_for_tts(c["caption"])
            start = s["start"] + c["t"]
            nxt = s["start"] + cues[i + 1]["t"] if i + 1 < len(cues) else s["end"] + 0.35
            avail = nxt - start - 0.12
            buf = tts(text, voice, rate)
            dur = len(buf) / SR
            used = rate
            if dur > avail:
                used = int(min(rate * 1.15, rate * dur / avail * 1.04))
                buf = tts(text, voice, used)
                dur = len(buf) / SR
            gain = 0.9 if c["speaker"] is None else (0.62 if c["speaker"] == "ravi_inner" else 0.85)
            add_into(vo, buf, int(start * SR), gain)
            if c["speaker"]:
                add_into(char, buf, int(start * SR), gain * (0.55 if c["speaker"] == "ravi_inner" else 0.3))
            log.append({"scene": s["id"], "t": round(start, 2), "speaker": SPEAKER_NAME.get(c["speaker"], c["speaker"]),
                        "voice": voice, "rate": used, "dur": round(dur, 2), "gap": round(avail, 2),
                        "overrun": round(max(0.0, dur - avail), 2), "text": text})
    # small room on the character lines so they sit in a space; narrator stays dry and close
    roomL, roomR = zeros(n), zeros(n)
    for d, g in ((0.011, .5), (0.019, .4), (0.029, .32), (0.041, .24), (0.057, .16), (0.079, .1)):
        add_into(roomL, char, int(d * SR), g)
        add_into(roomR, char, int(d * 1.21 * SR), g)
    return vo, roomL, roomR


# --------------------------------------------------------------------------------------
# 6. Ducking, silence beat, mastering, files
# --------------------------------------------------------------------------------------
def duck_curve(vo, n, depth_db=-10.0, block=441):
    """Per-block music gain: about -10 dB under speech, 60 ms attack (with lookahead), 400 ms release."""
    low = 10 ** (depth_db / 20.0)
    nb = n // block + 1
    act = []
    for b in range(nb):
        seg = vo[b * block:(b + 1) * block]
        e = max(seg, default=0.0) - min(seg, default=0.0)
        act.append(e > 0.02)
    look = 6
    gains, g = [], 1.0
    for b in range(nb):
        target = low if any(act[b:b + look]) else 1.0
        rate = 0.25 if target < g else 0.025
        g += (target - g) * rate
        gains.append(g)
    return gains, block


def apply_blocks(buf, gains, block):
    for b, g in enumerate(gains):
        a, z = b * block, min(len(buf), (b + 1) * block)
        if a >= z:
            break
        if g != 1.0:
            buf[a:z] = array("d", map(mul, buf[a:z], repeat(g, z - a)))


def silence_beat(tl, n, block=441):
    """Purposeful quiet before the final line: music dips to ~-26 dB for ~1 s before the close."""
    gains = [1.0] * (n // block + 1)
    for s in tl:
        if s["id"] in ("s9", "oc"):
            a, b = s["start"] - 1.2, s["start"] + 0.4
            for i in range(int(a * SR / block), min(len(gains), int(b * SR / block))):
                x = (i * block / SR - a) / (b - a)
                gains[i] = min(gains[i], 0.05 + 0.95 * abs(1 - 2 * x) ** 1.5)
    return gains, block


def fade(buf, total_s, fin=0.8, fout=2.5):
    n = len(buf)
    a, z = int(fin * SR), int(total_s * SR)
    for i in range(0, a, 441):
        buf[i:i + 441] = array("d", map(mul, buf[i:i + 441], repeat(i / a, len(buf[i:i + 441]))))
    fo = int(fout * SR)
    for i in range(z - fo, n, 441):
        g = max(0.0, (z - i) / fo)
        seg = buf[i:i + 441]
        buf[i:i + 441] = array("d", map(mul, seg, repeat(g, len(seg))))


def stats(L, R):
    pk = max(max(L), -min(L), max(R), -min(R))
    ss = sum(map(mul, L, L)) + sum(map(mul, R, R))
    rms = math.sqrt(ss / (2 * len(L)))
    return pk, rms


def master(L, R, target_rms_db=-16.0, peak_db=-1.0):
    pk, rms = stats(L, R)
    g = min(10 ** (target_rms_db / 20) / max(rms, 1e-9), 10 ** (peak_db / 20) / max(pk, 1e-9) * 1.6)
    L2, R2 = scaled(L, g), scaled(R, g)
    ceil = 10 ** (peak_db / 20)
    knee = 0.8 * ceil

    def lim(x):
        ax = abs(x)
        if ax <= knee:
            return x
        y = knee + (ceil - knee) * math.tanh((ax - knee) / (ceil - knee))
        return y if x > 0 else -y
    pk2 = max(max(L2), -min(L2), max(R2), -min(R2))
    if pk2 > knee:
        L2, R2 = array("d", map(lim, L2)), array("d", map(lim, R2))
    pk3, rms3 = stats(L2, R2)
    return L2, R2, {"peak_dbfs": round(20 * math.log10(max(pk3, 1e-9)), 2), "rms_dbfs": round(20 * math.log10(max(rms3, 1e-9)), 2)}


def write_wav(path, L, R, total_s):
    n = int(total_s * SR)
    L, R = L[:n], R[:n]
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


def to_m4a(wav):
    if not shutil.which("afconvert"):
        return None
    m4a = wav[:-4] + ".m4a"
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "160000", wav, m4a], check=True)
    return m4a


def build(cut, scenes, cuts, voice=True):
    tl, total = timeline(scenes, cuts[cut], cut)
    n = int((total + 3.0) * SR)
    music_log, vo_log = [], []
    print(f"[{cut}] {total:.1f} s, {len(tl)} scenes")
    L, R, fx = render_music(tl, total, music_log)
    sfx = render_foley(tl, n)
    # music-only stem
    mL, mR = L[:], R[:]
    sg, blk = silence_beat(tl, n)
    apply_blocks(mL, sg, blk)
    apply_blocks(mR, sg, blk)
    fade(mL, total)
    fade(mR, total)
    mL, mR, m_stats = master(mL, mR, -18.0)
    write_wav(os.path.join(HERE, f"kairos_theme_{cut}.wav"), mL, mR, total)
    files = [f"kairos_theme_{cut}.wav"]
    report = {"cut": cut, "runtime_s": total, "tempo_bpm": BPM, "bar_s": BAR, "sections": music_log,
              "music_master": m_stats}
    if voice and shutil.which("say"):
        vo, roomL, roomR = render_voices(tl, n, vo_log)
        vL = array("d", map(add, vo, roomL))
        vR = array("d", map(add, vo, roomR))
        vL2, vR2, v_stats = master(vL[:], vR[:], -18.0)
        write_wav(os.path.join(HERE, f"vo_{cut}.wav"), vL2, vR2, total)
        # soundtrack: music ducked under every line + transitions/foley + voices
        gains, blk = duck_curve(vo, n)
        apply_blocks(L, gains, blk)
        apply_blocks(R, gains, blk)
        apply_blocks(fx, gains, blk)
        apply_blocks(L, sg, blk)
        apply_blocks(R, sg, blk)
        fade(L, total)
        fade(R, total)
        sL = array("d", map(add, map(add, map(mul, L, repeat(0.55, n)), fx), sfx))
        sR = array("d", map(add, map(add, map(mul, R, repeat(0.55, n)), fx), sfx))
        sL = array("d", map(add, sL, map(mul, vL, repeat(0.95, n))))
        sR = array("d", map(add, sR, map(mul, vR, repeat(0.95, n))))
        sL, sR, s_stats = master(sL, sR, -16.0)
        write_wav(os.path.join(HERE, f"soundtrack_{cut}.wav"), sL, sR, total)
        files += [f"vo_{cut}.wav", f"soundtrack_{cut}.wav"]
        report.update({"voices": vo_log, "vo_master": v_stats, "soundtrack_master": s_stats,
                       "duck_db": -10, "overruns": [v for v in vo_log if v["overrun"] > 0]})
    else:
        print("  (voices skipped: macOS `say` not available or --no-voice)")
    for f in files:
        m = to_m4a(os.path.join(HERE, f))
        print(f"  wrote {f}" + (f" + {os.path.basename(m)}" if m else ""))
    with open(os.path.join(HERE, f"cue_sheet_{cut}.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    for sec in music_log:
        print(f"  {sec['start']:7.2f}-{sec['end']:7.2f}  {sec['scene']:4s} {sec['style']:8s} {sec['bars']:2d} bars  {' '.join(sec['chords'])}")
    for v in vo_log:
        flag = f"  OVERRUN {v['overrun']}s" if v["overrun"] > 0 else ""
        print(f"  {v['t']:7.2f}  {v['speaker']:16s} {v['voice']:5s} r{v['rate']:<3d} {v['dur']:5.2f}s/{v['gap']:5.2f}s{flag}  {v['text'][:60]}")
    if "soundtrack_master" in report:
        print(f"  soundtrack peak {report['soundtrack_master']['peak_dbfs']} dBFS, rms {report['soundtrack_master']['rms_dbfs']} dBFS")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cut", choices=("full", "operator", "both"), default="both")
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    scenes, cuts, names = parse_film(FILM)
    for cut in (("full", "operator") if a.cut == "both" else (a.cut,)):
        build(cut, scenes, cuts, voice=not a.no_voice)


if __name__ == "__main__":
    main()
