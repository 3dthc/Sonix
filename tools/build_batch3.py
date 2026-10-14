"""Batch 3 (v0.3): more strings, a bass, a flute and a sax from VSCO 2 CE and VCSL; electric guitars, basses, pads,
leads and a chord stab rendered by tools/synth.py."""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prep  # noqa: E402
import synth  # noqa: E402
from build_demo import OUT, RAW, VEL2, rendered  # noqa: E402
from build_batch2 import NOTE, g  # noqa: E402

VSCO = os.path.join(RAW, "VSCO-2-CE")
VCSL = os.path.join(RAW, "VCSL")


def sections(name, secs, max_len, loop=False, **kw):
    """Cello low, viola middle, violin high (each section mapped by its own measured octave), two velocity layers."""
    zones = []
    vel = lambda p: re.search(r"_v(\d)", os.path.basename(p)).group(1)
    for pat, lo, hi in secs:
        files = [p for p in g(VSCO, "Strings", pat) if not re.search(r"_(rr|RR)[2-9]|_[2-9]\.wav$", os.path.basename(p))]
        vals = sorted({vel(p) for p in files})
        keep = [vals[0], vals[-1]]                        # the softest and loudest takes, as two layers
        z = prep.folder_zones(files, NOTE, lambda p: vel(p) if vel(p) in keep else None, max_len=max_len, fine_tune=True)
        zones += [q for q in z if lo <= q["root"] <= hi]
    for q in zones:
        q["lovel"], q["hivel"] = VEL2[q["vel"] - 1]
    return prep.build_instrument(name, "STRINGS", "vsco2ce", zones, OUT, loop=loop, **kw)


def pizz_strings():
    return sections("pizz_strings", [("Cello Section/pizzT/*.wav", 0, 52), ("Viola Section/pizz/*.wav", 53, 64),
                                     ("Violin Section/Pizz/*.wav", 65, 127)], 2.0)


def spic_strings():
    return sections("spic_strings", [("Cello Section/spic/*.wav", 0, 52), ("Viola Section/spic/*.wav", 53, 64),
                                     ("Violin Section/Spic/*.wav", 65, 127)], 1.0, fade_s=0.15)


def trem_strings():
    return sections("trem_strings", [("Cello Section/trem/*.wav", 0, 52), ("Viola Section/trem/*.wav", 53, 64),
                                     ("Violin Section/Trem/*.wav", 65, 127)], 2.6, loop=True, loop_region=(0.3, 0.85), rate=32000)


def solo_violin():
    files = g(VSCO, "Strings/Solo Violin/Arco Vib/*.wav")
    z = prep.folder_zones(files, r"_([A-G]#?\d)_(?:p|f)\.wav", lambda p: p.endswith("_f.wav"), max_len=3.2, fine_tune=True)
    return prep.build_instrument("solo_violin", "STRINGS", "vsco2ce", z, OUT, loop=True, loop_region=(0.3, 0.85), mono=True, rate=32000)


def upright_bass():
    files = [p for p in g(VSCO, "Strings/Solo Contrabass/Pizz/*_rr1.wav")]
    z = prep.folder_zones(files, NOTE, lambda p: re.search(r"_v(\d)_", p).group(1), max_len=3, fine_tune=True)
    return prep.build_instrument("upright_bass", "BASS", "vsco2ce", z, OUT, mono=True, rate=32000)


def flute():
    files = g(VSCO, "Woodwinds/Flute/susvib/*.wav")
    z = prep.folder_zones(files, NOTE, lambda p: 0, max_len=3.5, fine_tune=True)
    return prep.build_instrument("flute", "LEAD", "vsco2ce", z, OUT, loop=True, loop_region=(0.3, 0.85), mono=True, rate=32000)


def tenor_sax():
    files = g(VCSL, "Aerophones/Reed Aerophones/Tenor Saxophone/Vibrato/*.wav")
    z = prep.folder_zones(files, r"_Main_([A-G]#?\d)_", lambda p: 0, max_len=3, fine_tune=True)
    return prep.build_instrument("tenor_sax", "BRASS", "vcsl", z, OUT, loop=True, loop_region=(0.3, 0.85), mono=True, rate=32000)


JOBS = [
    ("solo_violin", solo_violin), ("pizz_strings", pizz_strings), ("trem_strings", trem_strings),
    ("spic_strings", spic_strings), ("upright_bass", upright_bass), ("flute", flute), ("tenor_sax", tenor_sax),
    ("synth_strings", lambda: rendered("synth_strings", "STRINGS", synth.synth_strings, range(36, 91, 4), True, max_len=3.2,
                                       rate=32000, loop_region=(0.3, 0.9))),
    ("clean_electric", lambda: rendered("clean_electric", "GUITAR", synth.clean_electric, range(40, 86, 3), False,
                                        vel_layers=VEL2, max_len=4, mono=True)),
    ("muted_electric", lambda: rendered("muted_electric", "GUITAR", synth.muted_electric, range(40, 80, 3), False,
                                        vel_layers=VEL2, max_len=1.0, mono=True, fade_s=0.1)),
    ("analog_bass", lambda: rendered("analog_bass", "BASS", synth.analog_bass, range(28, 65, 3), False, vel_layers=VEL2,
                                     max_len=1.8, rate=22050, mono=True)),
    ("fm_bass", lambda: rendered("fm_bass", "BASS", synth.fm_bass, range(28, 65, 3), False, vel_layers=VEL2,
                                 max_len=1.4, rate=22050, mono=True)),
    ("square_lead", lambda: rendered("square_lead", "LEAD", synth.square_lead, range(48, 91, 4), True, max_len=2.5,
                                     rate=32000)),
    ("analog_pad", lambda: rendered("analog_pad", "PAD", synth.analog_pad, range(36, 88, 4), True, max_len=3.2, rate=32000,
                                    loop_region=(0.3, 0.9))),
    ("glass_pad", lambda: rendered("glass_pad", "PAD", synth.glass_pad, range(36, 92, 4), True, max_len=3.2, rate=32000,
                                   loop_region=(0.3, 0.9))),
    ("air_pad", lambda: rendered("air_pad", "PAD", synth.air_pad, range(36, 88, 4), True, max_len=3.2, rate=32000,
                                 loop_region=(0.3, 0.9))),
    ("dub_chord", lambda: rendered("dub_chord", "SYNTH", synth.dub_chord, range(36, 76, 3), False, max_len=1.5,
                                   rate=32000)),
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
