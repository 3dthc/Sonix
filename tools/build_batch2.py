"""Batch 2: VCSL keys, mallets, bells and plucks; Meta steel guitar; VSCO brass; more renders."""
import glob
import os
import re
import sys
import time
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prep  # noqa: E402
import synth  # noqa: E402
from build_demo import OUT, RAW, VEL2, rendered  # noqa: E402

VCSL = os.path.join(RAW, "VCSL")
NOTE = r"_([A-G]#?\d)(?:_|\.)"


def g(*parts):
    return sorted(glob.glob(os.path.join(*parts)))


def vibraphone():
    files = g(VCSL, "Idiophones/Struck Idiophones/Vibraphone/Hard Mallets/*_rr1_*.wav")
    z = prep.folder_zones(files, NOTE, lambda p: re.search(r"_v(\d)_", p).group(1), max_len=5)
    return prep.build_instrument("vibraphone", "BELLS", "vcsl", z, OUT, mono=True)


def glockenspiel():
    order = {"soft": 0, "medium": 1, "loud": 2}
    files = g(VCSL, "Idiophones/Struck Idiophones/Glockenspiel/*.wav")
    lvl = lambda p: order[re.search(r"glock_(\w+?)_", os.path.basename(p)).group(1)]
    z = prep.folder_zones(files, NOTE, lambda p: lvl(p) if lvl(p) != 1 else None, max_len=3.5)   # soft and loud
    return prep.build_instrument("glockenspiel", "BELLS", "vcsl", z, OUT, mono=True)


def tubular_bells():
    files = g(VCSL, "Idiophones/Struck Idiophones/Tubular Bells 2/*_1.wav")
    z = prep.folder_zones(files, NOTE, lambda p: re.search(r"_v(\d)_", p).group(1), max_len=6, fine_tune=True)
    return prep.build_instrument("tubular_bells", "BELLS", "vcsl", z, OUT, mono=True)


def marimba():
    order = {"soft": 0, "med": 1, "loud": 2}
    files = g(VCSL, "Idiophones/Struck Idiophones/Marimba/*_01.wav")
    z = prep.folder_zones(files, NOTE, lambda p: order[re.search(r"_(soft|med|loud)_", p).group(1)], max_len=4)
    return prep.build_instrument("marimba", "BELLS", "vcsl", z, OUT, mono=True)


def concert_harp():
    order = {"mf": 0, "f": 1}
    files = g(VCSL, "Chordophones/Composite Chordophones/Concert Harp/*1.wav")
    z = prep.folder_zones(files, r"_([A-G]#?\d)_", lambda p: order.get((re.search(r"_(mf|f)1\.wav", p) or [None, None])[1]), max_len=3.5, fine_tune=True)
    return prep.build_instrument("concert_harp", "PLUCK", "vcsl", z, OUT, mono=True)


def kalimba():
    files = g(VCSL, "Idiophones/Plucked Idiophones/Kalimba, Tanzania/*_rr1.wav") or \
        g(VCSL, "Idiophones/Plucked Idiophones/Kalimba, Tanzania/*.wav")
    z = prep.folder_zones(files, r"_Main_([A-G]#?\d)_", lambda p: 0, max_len=3, fine_tune=True)
    return prep.build_instrument("kalimba", "PLUCK", "vcsl", z, OUT, mono=True)


def harpsichord():
    files = g(VCSL, "Chordophones/Zithers/Harpsichord, Flemish/Sustains/Low/*.wav")
    z = prep.folder_zones(files, NOTE, lambda p: 0, max_len=3.2)
    return prep.build_instrument("harpsichord", "KEYS", "vcsl", z, OUT, mono=True)


def pipe_organ():
    """Quiet manual stop for soft playing, loud stop above velocity 80.

    Both stops share one naming convention. The quiet stop has a strong fundamental and
    measures +12 on all 21 notes; the loud stop's weak fundamental makes single notes
    ambiguous, so it takes the same, reliably measured offset."""
    files = g(VCSL, "Aerophones/Edge-blown Aerophones/Pipe Organ/Quiet/*_rr1.wav") + \
        g(VCSL, "Aerophones/Edge-blown Aerophones/Pipe Organ/Loud/*.wav")
    z = prep.folder_zones(files, r"_([A-G]#?\d)(?:_rr1)?\.wav", lambda p: "Loud" in p.split("/")[-2], max_len=2.2, shift=12)
    return prep.build_instrument("pipe_organ", "KEYS", "vcsl_organ", z, OUT, loop=True, loop_region=(0.3, 0.85), rate=32000)


def clavisynth():
    files = g(VCSL, "Electrophones/TX81Z/Clavisynth/*.wav")
    vel = lambda p: re.search(r"_vl(\d)", p).group(1)
    keep = (min(map(vel, files)), max(map(vel, files)))
    z = prep.folder_zones(files, NOTE, lambda p: vel(p) if vel(p) in keep else None, max_len=3)
    return prep.build_instrument("clavisynth", "KEYS", "vcsl", z, OUT, mono=True, rate=32000)


def steel_guitar():
    base = os.path.join(RAW, "Meta-open-sound/systems/instruments/meta-steel-guitar/ogg")
    strings = [("LowE", 40), ("A", 45), ("D", 50), ("G", 55), ("B", 59), ("HighE", 64)]
    files = []
    for root in range(40, 86, 3):
        s = next((n for n, o in strings if 0 <= root - o <= 4), None) or ("HighE" if root >= 64 else None)
        if s is None:
            s = [n for n, o in strings if o <= root][-1]
        name = prep.midi_name(root).replace("#", "s")
        files += [p for p in g(base, f"INSGuitarSteel_String{s}_Vel0*_{name}_01.ogg")]
    vel = lambda p: re.search(r"_Vel0(\d)_", p).group(1)
    keep = (min(map(vel, files)), max(map(vel, files)))      # the softest and hardest of its three layers
    z = prep.folder_zones(files, r"_([A-G]s?\d)_01", lambda p: vel(p) if vel(p) in keep else None, max_len=5, fine_tune=True)
    return prep.build_instrument("steel_guitar", "GUITAR", "metaopensound", z, OUT, mono=True)


def orch_brass():
    """Trombone low, horn middle, trumpet high: sustains, looped."""
    secs = [("Tenor Trombone/sus/*_1.wav", 0, 52), ("F Horn/sus/*_1.wav", 53, 64), ("Trumpet/sus/*_rr1.wav", 65, 127)]
    zones = []
    for pat, lo, hi in secs:
        files = g(RAW, "VSCO-2-CE/Brass", pat)
        vals = sorted({re.search(r"_v(\d)_", p).group(1) for p in files})
        keep = [vals[0], vals[-1]]                       # softest and loudest, as two layers
        z = prep.folder_zones(files, NOTE, lambda p: (re.search(r"_v(\d)_", p).group(1) if re.search(r"_v(\d)_", p).group(1) in keep else None), max_len=3.2)
        zones += [q for q in z if lo <= q["root"] <= hi]
    for q in zones:                                       # two layers across all three sections
        q["lovel"], q["hivel"] = VEL2[q["vel"] - 1]
    return prep.build_instrument("orch_brass", "BRASS", "vsco2ce", zones, OUT, loop=True, loop_region=(0.3, 0.85), rate=32000)


JOBS = [
    ("vibraphone", vibraphone), ("glockenspiel", glockenspiel), ("tubular_bells", tubular_bells),
    ("marimba", marimba), ("concert_harp", concert_harp), ("kalimba", kalimba),
    ("harpsichord", harpsichord), ("pipe_organ", pipe_organ), ("clavisynth", clavisynth),
    ("steel_guitar", steel_guitar), ("orch_brass", orch_brass),
    ("synth_brass", lambda: rendered("synth_brass", "BRASS", synth.synth_brass, range(36, 85, 3), True, max_len=2.4, rate=32000)),
    ("drawbar_organ", lambda: rendered("drawbar_organ", "KEYS", synth.drawbar_organ, range(36, 97, 3), True, max_len=2.0,
                                       rate=32000)),
    ("fm_bells", lambda: rendered("fm_bells", "BELLS", synth.fm_bell, range(48, 97, 3), False, vel_layers=VEL2, max_len=2.5,
                                  rate=32000, mono=True)),
    ("synth_pluck", lambda: rendered("synth_pluck", "PLUCK", synth.synth_pluck, range(36, 91, 4), False, vel_layers=VEL2,
                                     max_len=1.8, rate=32000)),
    ("ooh_choir", lambda: rendered("ooh_choir", "CHOIR", synth.ooh_choir, range(45, 82, 3), True, max_len=3.8, rate=32000,
                                   loop_region=(0.3, 0.9))),
    ("trance_chords", lambda: rendered("trance_chords", "SYNTH", synth.trance_chord, range(36, 79, 3), False, max_len=1.6,
                                       rate=32000)),
    ("riser", lambda: rendered("riser", "SYNTH", synth.riser, [60], False, max_len=4, rate=32000)),
    ("downlifter", lambda: rendered("downlifter", "SYNTH", synth.downlifter, [60], False, max_len=3, rate=32000)),
]


def main():
    only = set(sys.argv[1:])
    for name, job in JOBS:
        if only and name not in only:
            continue
        t = time.time()
        k = job()
        roots = [z["root"] for z in k["zones"]]
        print(f"{name:15s} {len(k['zones']):4d} zones {k['total_bytes'] / 1e6:6.1f} MB  "
              f"{prep.midi_name(min(roots))}-{prep.midi_name(max(roots))}  {time.time() - t:5.1f}s", flush=True)
    sizes = prep.write_credits(OUT)
    print(f"LIBRARY TOTAL {sum(sizes.values()) / 1e6:.1f} MB in {len(sizes)} instruments")


if __name__ == "__main__":
    main()
