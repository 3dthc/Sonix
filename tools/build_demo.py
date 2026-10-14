"""Build the Sonix demo pack: prepared multisamples from every source + our renders."""
import glob
import json
import os
import re
import sys
import time
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prep  # noqa: E402
import synth  # noqa: E402

LIB = prep.LIB
RAW = os.path.join(LIB, "raw")
OUT = os.path.join(LIB, "library")
VEL4 = [(1, 48), (49, 80), (81, 104), (105, 127)]
VEL3 = [(1, 56), (57, 96), (97, 127)]
VEL2 = [(1, 80), (81, 127)]


def piano_len(root):
    """How long a piano note is kept: 7 s in the bass down to 2.5 s at the top (the tail below -60 dB is cut)."""
    return float(np.clip(7 - (root - 21) * 0.07, 2.5, 7))


def grand_piano():
    files = glob.glob(os.path.join(RAW, "SalamanderGrandPiano/Samples/*.flac"))
    layer_of = {6: 0, 11: 1, 16: 2}                  # 3 of Salamander's 16 velocity layers
    zones = []
    for p in files:
        m = re.fullmatch(r"([A-G]#?)(\d)v(\d+)\.flac", os.path.basename(p))
        note, octv, v = m.group(1), m.group(2), int(m.group(3))
        if v not in layer_of:
            continue
        root = prep.note_to_midi(note + octv)
        L = layer_of[v]
        zones.append(dict(path=p, root=root, vel=L + 1, lovel=VEL3[L][0], hivel=VEL3[L][1], max_len=piano_len(root)))
    return prep.build_instrument("grand_piano", "PIANO", "salamander", zones, OUT)


def upright_piano():
    zones = []
    for p in glob.glob(os.path.join(RAW, "Meta-open-sound/systems/instruments/meta-piano/ogg/*.ogg")):
        m = re.fullmatch(r"INSPiano_Vel0(\d)_([A-G]s?\d)_01\.ogg", os.path.basename(p))
        L = {2: 0, 4: 1}.get(int(m.group(1)))      # two of its four velocity layers
        root = prep.note_to_midi(m.group(2))
        if (root - 21) % 4 or L is None:          # every major third
            continue
        zones.append(dict(path=p, root=root, vel=L + 1, lovel=VEL2[L][0], hivel=VEL2[L][1], max_len=piano_len(root) * 0.5))
    return prep.build_instrument("upright_piano", "PIANO", "metaopensound", zones, OUT)


def string_ensemble():
    """Cello section low, viola section middle, violin section high, sustained with vibrato."""
    sec = [("Cello Section/susvib", 0, 52), ("Viola Section/susvib", 53, 64), ("Violin Section/susVib", 65, 127)]
    zones = []
    for folder, lo, hi in sec:
        files = [p for p in glob.glob(os.path.join(RAW, "VSCO-2-CE/Strings", folder, "*.wav"))
                 if not p.endswith("_2.wav")]                      # skip duplicate takes
        vels = sorted({int(re.search(r"_v(\d)", os.path.basename(p)).group(1)) for p in files})
        for p in files:
            m = re.search(r"_([A-G]#?\d)_v(\d)", os.path.basename(p))
            # VSCO names strings with middle C = C3; pitch-checked: file 'A3' sounds A4
            root = prep.note_to_midi(m.group(1)) + 12
            if not lo <= root <= hi:
                continue
            L = vels.index(int(m.group(2)))
            zones.append(dict(path=p, root=root, vel=L + 1, lovel=VEL2[L][0], hivel=VEL2[L][1], max_len=3.0))
    return prep.build_instrument("string_ensemble", "STRINGS", "vsco2ce", zones, OUT, loop=True,
                                 loop_region=(0.3, 0.85), rate=32000)


def nylon_guitar():
    base = os.path.join(RAW, "Meta-open-sound/systems/instruments/meta-nylon-guitar/ogg")
    strings = [("LowE", 40), ("A", 45), ("D", 50), ("G", 55), ("B", 59), ("HighE", 64)]
    takes = ["Vel01Finger", "Vel03Pick"]
    zones = []
    for root in range(40, 84, 3):
        s = next((n for n, o in strings if 0 <= root - o <= 4), None) or ("HighE" if root >= 64 else None)
        if s is None:
            s = [n for n, o in strings if o <= root][-1]
        name = prep.midi_name(root).replace("#", "s")
        for L, take in enumerate(takes):
            p = os.path.join(base, f"INSGuitarNylon_String{s}_{take}_{name}_01.ogg")
            if os.path.exists(p):
                tune = -round(prep.measured_cents(prep.load(p), root))     # string tuning of the recording
                zones.append(dict(path=p, root=root, vel=L + 1, lovel=VEL2[L][0], hivel=VEL2[L][1], max_len=4.5, tune=tune))
    return prep.build_instrument("nylon_guitar", "GUITAR", "metaopensound", zones, OUT, mono=True)


def rendered(name, category, fn, roots, loop, vel_layers=None, **kw):
    """Render fn(root, [vel,] rng) per zone. fn may return (audio, cents) when it can't hit the pitch exactly
    (a plucked string's whole-sample delay line): the zone then carries a tuning correction."""
    rng = np.random.default_rng(zlib.crc32(name.encode()))
    zones = []

    def zone(x, **z):
        if isinstance(x, tuple):
            x, cents = x
            z["tune"] = -round(cents)
        zones.append(dict(audio=x.astype(np.float32), **z))

    for r in roots:
        if vel_layers:
            for L, (lo, hi) in enumerate(vel_layers):
                zone(fn(r, (L + 0.5) / len(vel_layers), rng), root=r, vel=L + 1, lovel=lo, hivel=hi)
        else:
            zone(fn(r, rng), root=r, vel=1, lovel=1, hivel=127)
    return prep.build_instrument(name, category, "sonix", zones, OUT, loop=loop, trim=False, **kw)


def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = [
        ("grand_piano", grand_piano),
        ("upright_piano", upright_piano),
        ("string_ensemble", string_ensemble),
        ("nylon_guitar", nylon_guitar),
        ("warm_pad", lambda: rendered("warm_pad", "PAD", synth.warm_pad, range(36, 88, 3), True, max_len=3.2,
                                      rate=32000, loop_region=(0.3, 0.9))),
        ("supersaw_lead", lambda: rendered("supersaw_lead", "LEAD", synth.supersaw_lead, range(48, 91, 3), True,
                                           max_len=2.5, rate=32000)),
        ("aah_choir", lambda: rendered("aah_choir", "CHOIR", synth.formant_choir, range(45, 82, 3), True, max_len=3.8,
                                       rate=32000, loop_region=(0.3, 0.9))),
        ("sub_bass", lambda: rendered("sub_bass", "BASS", synth.sub_bass, range(28, 62, 3), True, max_len=2,
                                      rate=22050, mono=True)),
        ("fm_epiano", lambda: rendered("fm_epiano", "KEYS", synth.fm_epiano, range(36, 97, 3), False,
                                        vel_layers=VEL2, max_len=3, rate=32000, mono=True)),
    ]
    only = set(sys.argv[1:])
    for name, job in jobs:
        if only and name not in only:
            continue
        t = time.time()
        k = job()
        print(f"{name:16s} {len(k['zones']):4d} zones {k['total_bytes'] / 1e6:7.1f} MB  {time.time() - t:5.1f}s", flush=True)
    sizes = prep.write_credits(OUT)
    print(f"TOTAL {sum(sizes.values()) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
