#!/usr/bin/env python3
"""Render the film's voice track from the fixed timeline, and (optionally) a ducked soundtrack for the MP4.

    python3 film/music/mix.py                                    # music/vo_hero.m4a (Neha, placed by timeline.json)
    python3 film/music/mix.py --music music/original/v1_uplifting.m4a
                                                                 # + music/soundtrack_hero.m4a (score ducked under Neha), for mux.swift

Voice: film/audio/vo_neha/*.wav (Sarvam bulbul:v3, speaker neha, pace 1.2), placed at the starts in
film/audio/vo_neha/timeline.json (runtime 139.635 s). High-pass 90 Hz, light 3-5 kHz presence lift, normalised to
about -16 LUFS (RMS of voiced blocks). With --music the score is ducked 11 dB under every line (20 ms attack,
300 ms release) and the master is limited to -1 dBFS. The player (kairos_film.html) applies the same ducking live,
so the .m4a soundtrack is only needed for exporting a video. Python stdlib + afconvert (macOS).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from itertools import repeat
from operator import add, mul

SR = 44100
HERE = os.path.dirname(os.path.abspath(__file__))
VO_DIR = os.path.join(HERE, "..", "audio", "vo_neha")


def read_wav(path):
    with wave.open(path, "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    assert sw == 2 and sr == SR, (path, sw, sr)
    a = array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    f = array("d", map(mul, a, repeat(1 / 32768.0, len(a))))
    return (f, f) if ch == 1 else (f[0::2], f[1::2])


def decode(path):
    tmp = os.path.join(tempfile.mkdtemp(), "x.wav")
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", "-c", "2", path, tmp], check=True)
    return read_wav(tmp)


def onepole(x, fc, high=False):
    a = math.exp(-2 * math.pi * fc / SR)
    y, out = 0.0, array("d")
    for v in x:
        y = (1 - a) * v + a * y
        out.append(v - y if high else y)
    return out


def render_vo(n, lines):
    vo = array("d", bytes(8 * n))
    for ln in lines:
        x, _ = read_wav(os.path.join(VO_DIR, ln["line"] + ".wav"))
        x = onepole(x, 90, high=True)
        band = onepole(onepole(x, 2800, high=True), 5500)
        x = array("d", map(lambda a, b: a + 0.26 * b, x, band))
        st = int(ln["start"] * SR)
        m = min(len(x), n - st)
        vo[st:st + m] = array("d", map(add, vo[st:st + m], x[:m]))
    blk, acc, cnt = int(0.05 * SR), 0.0, 0
    for b in range(0, n, blk):
        seg = vo[b:b + blk]
        e = sum(map(mul, seg, seg)) / max(1, len(seg))
        if e > 1e-5:
            acc, cnt = acc + e, cnt + 1
    g = 10 ** (-16 / 20) / math.sqrt(acc / max(cnt, 1))
    return array("d", map(mul, vo, repeat(g, n)))


def duck_gains(lines, n, depth_db=-11.0, block=220, attack=0.02, release=0.30):
    low = 10 ** (depth_db / 20)
    win = [(int((l["start"] - 0.03) * SR), int(l["end"] * SR)) for l in lines]
    ac, rc = 1 - math.exp(-block / (attack * SR)), 1 - math.exp(-block / (release * SR))
    g, out = 1.0, []
    for b in range(n // block + 1):
        i = b * block
        tgt = low if any(a <= i < z for a, z in win) else 1.0
        g += (tgt - g) * (ac if tgt < g else rc)
        out.append(g)
    return out, block


def limit(x, ceil_db=-1.0):
    c = 10 ** (ceil_db / 20)
    k = 0.85 * c

    def f(v):
        a = abs(v)
        if a <= k:
            return v
        y = k + (c - k) * math.tanh((a - k) / (c - k))
        return y if v > 0 else -y
    return array("d", map(f, x))


def write(path_m4a, L, R):
    n = len(L)
    tmp = path_m4a[:-4] + ".wav"
    out = array("h", bytes(4 * n))
    out[0::2] = array("h", (int(max(-1, min(1, v)) * 32767) for v in L))
    out[1::2] = array("h", (int(max(-1, min(1, v)) * 32767) for v in R))
    if sys.byteorder == "big":
        out.byteswap()
    with wave.open(tmp, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(out.tobytes())
    if shutil.which("afconvert"):
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "192000", tmp, path_m4a], check=True)
    print("wrote", os.path.relpath(path_m4a, os.path.join(HERE, "..")), f"(peak {20 * math.log10(max(max(map(abs, L)), 1e-9)):.2f} dBFS)")
    return tmp


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--music", help="score to duck under the voice (m4a/wav/mp3)")
    ap.add_argument("--music-level-db", type=float, default=-15.5, help="score RMS before ducking")
    a = ap.parse_args()
    tl = json.load(open(os.path.join(VO_DIR, "timeline.json"), encoding="utf-8"))
    n = int(tl["runtime_s"] * SR)
    vo = render_vo(n, tl["lines"])
    tmp = write(os.path.join(HERE, "vo_hero.m4a"), limit(vo), limit(vo))
    os.remove(tmp)
    if a.music:
        L, R = decode(a.music)
        L, R = (L[:n] + array("d", bytes(8 * max(0, n - len(L))))), (R[:n] + array("d", bytes(8 * max(0, n - len(R)))))
        rms = math.sqrt((sum(map(mul, L, L)) + sum(map(mul, R, R))) / (2 * n) + 1e-12)
        g0 = 10 ** (a.music_level_db / 20) / rms
        gains, blk = duck_gains(tl["lines"], n)
        for b, g in enumerate(gains):
            i, z = b * blk, min(n, (b + 1) * blk)
            if i >= z:
                break
            L[i:z] = array("d", map(mul, L[i:z], repeat(g0 * g, z - i)))
            R[i:z] = array("d", map(mul, R[i:z], repeat(g0 * g, z - i)))
        tmp = write(os.path.join(HERE, "soundtrack_hero.m4a"), limit(array("d", map(add, L, vo))), limit(array("d", map(add, R, vo))))
        os.remove(tmp)


if __name__ == "__main__":
    main()
