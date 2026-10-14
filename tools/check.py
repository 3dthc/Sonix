"""Objective checks for the library: pitch vs mapped root, loop seams, clipping, sizes."""
import json
import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prep import LIB, SR, midi_name  # noqa: E402

OUT = os.path.join(LIB, "library")


def f0(x, expect, start_s=0.05, secs=0.6):
    """Measure the fundamental near the expected pitch (±1 semitone).

    Returns (freq, strength): strength = that peak relative to the strongest peak
    between 0.4x and 2.6x the expected pitch. A wrongly mapped octave or note shows
    up as a weak peak (the energy sits somewhere else)."""
    m = x.mean(axis=1)
    a = int(start_s * SR)
    seg = m[a:a + int(secs * SR)]
    N = 1 << 19
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), N))
    fr = np.fft.rfftfreq(N, 1 / SR)
    def peak(f):
        lo, hi = np.searchsorted(fr, [f * 2 ** (-1 / 12), f * 2 ** (1 / 12)])
        i = lo + int(np.argmax(sp[lo:hi]))
        return fr[i], sp[i]
    blo, bhi = np.searchsorted(fr, [expect * 0.4, expect * 3.5])
    ref = sp[blo:bhi].max() + 1e-12
    # low pianos and strings often have a weak fundamental: accept the 2nd/3rd harmonics
    # (an octave mis-map would have no energy at the odd multiples 1x and 3x)
    cands = [(peak(expect * k), k) for k in (1, 2, 3)]
    (f1, a1), _ = cands[0]
    (f3, a3), _ = cands[2]
    (fb, ab), kb = max(cands, key=lambda c: c[0][1])
    strength = max(a1, a3) / ref
    return fb / kb, strength


def seam(x, ls, le, w=64):
    """Ratio of sample-to-sample jump at the loop seam vs typical jumps nearby (≈1 means seamless)."""
    m = x.mean(axis=1)
    jump = abs(m[ls] - m[le - 1])
    near = np.max(np.abs(np.diff(m[le - 2000:le])))   # steepest natural step just before the seam
    return jump / (near + 1e-9)


def main():
    bad = 0
    for inst in sorted(os.listdir(OUT)):
        kp = os.path.join(OUT, inst, "keymap.json")
        if not os.path.exists(kp):
            continue
        k = json.load(open(kp))
        cents, seams, peaks = [], [], []
        for z in k["zones"]:
            x, _ = sf.read(os.path.join(OUT, inst, z["file"]), always_2d=True)
            peaks.append(np.max(np.abs(x)))
            if z["root"] >= 33:     # pitch detection is unreliable at the very bottom
                exp = 440 * 2 ** ((z["root"] - 69) / 12)
                f, strength = f0(x, exp)
                c = 1200 * np.log2(f / exp) + z.get("tune", 0)
                if strength < 0.08:  # no real energy at the mapped pitch: likely mis-mapped
                    c = 999
                cents.append((abs(c), z["file"], round(c)))
            if k["loop"]:
                seams.append((seam(x, z["loop_start"], z["loop_end"]), z["file"]))
        worst = max(cents) if cents else (0, "-", 0)
        line = f"{inst:16s} zones {len(k['zones']):3d}  size {k['total_bytes'] / 1e6:5.1f} MB  peak {max(peaks):.2f}  worst pitch {worst[2]:+5d}c ({worst[1]})"
        off = [c for c in cents if c[0] > 35]
        if seams:
            ws = max(seams)
            line += f"  worst seam {ws[0]:.1f}x"
        print(line)
        for c in off[:6]:
            print("    pitch off:", c[1], c[2], "cents")
        bad += len(off)
    for f in sorted(os.listdir(os.path.join(LIB, "clips"))):
        if f.endswith(".wav"):
            x, _ = sf.read(os.path.join(LIB, "clips", f))
            clip = np.mean(np.abs(x) > 0.999)
            print(f"clip {f:22s} peak {np.max(np.abs(x)):.2f} rms {20 * np.log10(np.sqrt(np.mean(x ** 2))):.1f} dBFS clipped {clip:.4%}")
    print("zones more than 35 cents off:", bad)


if __name__ == "__main__":
    main()
