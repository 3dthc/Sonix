#!/usr/bin/env python3
"""Pack the prepared library (library/<instrument>/keymap.json + FLAC) into the files the Sonix engine loads.

    python3 -I tools/pack_sonix.py [instrument ...]

Writes engine/<instrument>.snx (one per instrument), src/instruments.h (the engine's instrument table) and the
INSTRUMENT option list in vst/params.json. The engine decodes an instrument into RAM when it is first picked.

Sample file "SNXB" v1 (read by src/engine.c, "sample file"):
  header (32 bytes): "SNXB", u32 version 1, u32 samples, u32 zones, u32 data offset, u32 flags (1 = mono),
                     f32 level trim, u32 sample rate (44100, 32000 or 22050; 0 in older files = 44100)
  per sample (16 bytes): u32 byte offset into data, u32 frames, u32 loop start, u32 loop end (0 = one-shot)
  per zone (12 bytes): u8 lokey, hikey, root, lovel, hivel, pad; i16 tune (cents); u32 sample index
  data, per sample: u32 len0, u32 len1, then the left and right bitstreams.
The bitstream codec is Fretty's (3dthc/fretty-mpc, MIT): lossless 16-bit, 1024-frame blocks, a header byte
(order << 5 | k), then one Rice code per frame of the zigzagged residual of a fixed order-0..3 predictor.
SONIX_DUMP_RAW=<dir> also writes each instrument's raw 16-bit audio, for tools/check_codec.c.
"""
import json
import os
import struct
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LIB = os.path.join(REPO, "library")
ENGINE = os.path.join(REPO, "engine")

# The INSTRUMENT list, in category order: (file, display name, short name for the LCD's layer cells, <= 12 chars).
ORDER = [
    ("grand_piano", "Grand Piano", "GRAND PIANO"), ("upright_piano", "Upright Piano", "UPRIGHT"),
    ("fm_epiano", "FM E-Piano", "FM E-PIANO"), ("clavisynth", "Clavisynth", "CLAVISYNTH"),
    ("harpsichord", "Harpsichord", "HARPSICHRD"), ("drawbar_organ", "Drawbar Organ", "DRAWBAR"),
    ("pipe_organ", "Pipe Organ", "PIPE ORGAN"),
    ("nylon_guitar", "Nylon Guitar", "NYLON GTR"), ("steel_guitar", "Steel Guitar", "STEEL GTR"),
    ("clean_electric", "Clean Electric", "CLEAN ELEC"), ("muted_electric", "Muted Electric", "MUTED ELEC"),
    ("string_ensemble", "String Ensemble", "STRING ENS"), ("solo_violin", "Solo Violin", "SOLO VIOLIN"),
    ("pizz_strings", "Pizzicato", "PIZZICATO"), ("trem_strings", "Tremolo Strings", "TREMOLO"),
    ("spic_strings", "Spiccato", "SPICCATO"), ("synth_strings", "Synth Strings", "SYNTH STR"),
    ("orch_brass", "Orchestral Brass", "ORCH BRASS"), ("synth_brass", "Synth Brass", "SYNTH BRASS"),
    ("tenor_sax", "Tenor Sax", "TENOR SAX"),
    ("aah_choir", "Aah Choir", "AAH CHOIR"), ("ooh_choir", "Ooh Choir", "OOH CHOIR"),
    ("warm_pad", "Warm Pad", "WARM PAD"), ("analog_pad", "Analog Pad", "ANALOG PAD"),
    ("glass_pad", "Glass Pad", "GLASS PAD"), ("air_pad", "Air Pad", "AIR PAD"),
    ("trance_chords", "Trance Chords", "TRANCE"), ("dub_chord", "Dub Chord", "DUB CHORD"),
    ("riser", "Riser", "RISER"), ("downlifter", "Downlifter", "DOWNLIFTER"),
    ("supersaw_lead", "Supersaw Lead", "SUPERSAW"), ("square_lead", "Square Lead", "SQUARE LEAD"),
    ("flute", "Flute", "FLUTE"),
    ("concert_harp", "Concert Harp", "HARP"), ("kalimba", "Kalimba", "KALIMBA"),
    ("synth_pluck", "Synth Pluck", "SYNTH PLUCK"),
    ("sub_bass", "Sub Bass", "SUB BASS"), ("upright_bass", "Upright Bass", "UPR BASS"),
    ("analog_bass", "Analog Bass", "ANALOG BASS"), ("fm_bass", "FM Bass", "FM BASS"),
    ("vibraphone", "Vibraphone", "VIBRAPHONE"), ("marimba", "Marimba", "MARIMBA"),
    ("glockenspiel", "Glockenspiel", "GLOCK"), ("tubular_bells", "Tubular Bells", "TUBULAR"),
    ("fm_bells", "FM Bells", "FM BELLS"),
]

LIBRARY_VERSION = "0.3.0"   # written to engine/LIBRARY_VERSION; install.sh rebuilds the instruments when it differs

REF_DB = -20.0      # every instrument is trimmed so its typical note attack sits near this loudness,
PEAK_MAX = 0.4      # unless that would push a single hard-played note's peak above this (a 4-note chord then stays
                    # clear of the output limiter: plucked and struck sounds have a high peak for their loudness)


# ------------------------------------------------------------------ codec (from Fretty's tools/pack_samples.py)
BLOCK = 1024
ESC_Q = 24


def residuals(x, order):
    p = np.concatenate([np.zeros(3, np.int64), x])
    n = len(x)
    if order == 0:
        return x.copy()
    if order == 1:
        return p[3:] - p[2:2 + n]
    if order == 2:
        return p[3:] - 2 * p[2:2 + n] + p[1:1 + n]
    return p[3:] - 3 * p[2:2 + n] + 3 * p[1:1 + n] - p[0:n]


def encode_channel(x):
    """int64 samples -> bytes: per 1024-frame block a header byte (order << 5 | k), then Rice-coded residuals."""
    n = len(x)
    nb = (n + BLOCK - 1) // BLOCK
    best_cost = np.full(nb, np.inf)
    best_u = np.zeros(n, np.uint64)
    best_k = np.zeros(nb, np.int64)
    best_o = np.zeros(nb, np.int64)
    pad = nb * BLOCK - n
    for order in range(4):
        r = residuals(x, order)
        u = np.where(r >= 0, 2 * r, -2 * r - 1).astype(np.int64)
        ub = np.concatenate([u, np.zeros(pad, np.int64)]).reshape(nb, BLOCK)
        cnt = np.full(nb, BLOCK)
        cnt[-1] = BLOCK - pad
        mean = ub.sum(1) / cnt
        k = np.clip(np.floor(np.log2(mean * 0.69 + 1)), 0, 20).astype(np.int64)
        q = ub >> k[:, None]
        cost = np.where(q >= ESC_Q, ESC_Q + 20, q + 1 + k[:, None])
        cost[-1, BLOCK - pad:] = 0
        cost = cost.sum(1) + 8
        better = cost < best_cost
        best_cost = np.where(better, cost, best_cost)
        best_k = np.where(better, k, best_k)
        best_o = np.where(better, order, best_o)
        sel = np.repeat(better, BLOCK)[:n]
        best_u = np.where(sel, u.astype(np.uint64), best_u)
    kk = np.repeat(best_k, BLOCK)[:n].astype(np.uint64)
    q = best_u >> kk
    esc = q >= ESC_Q
    L_res = np.where(esc, ESC_Q + 20, q + 1 + kk).astype(np.int64)
    V_res = np.where(esc, best_u, (np.uint64(1) << kk) | (best_u & ((np.uint64(1) << kk) - np.uint64(1))))
    starts = np.arange(nb) * BLOCK
    L = np.insert(L_res, starts, 8)
    V = np.insert(V_res, starts, ((best_o << 5) | best_k).astype(np.uint64))
    end = np.cumsum(L) - 1
    total = int(end[-1]) + 1
    words = np.zeros(total // 64 + 2, np.uint64)
    w = end // 64
    s = (63 - end % 64).astype(np.uint64)
    np.bitwise_or.at(words, w, V << s)
    hi_mask = s > 0
    np.bitwise_or.at(words, w[hi_mask] - 1, V[hi_mask] >> (np.uint64(64) - s[hi_mask]))
    return words.astype(">u8").tobytes()[:(total + 7) // 8]


# ------------------------------------------------------------------ packing
def attack_db(audio, rate=44100):
    """Loudness of the loudest half second (the attack for most sounds; the peak of a riser)."""
    x = audio.astype(np.float64) / 32768
    w = int(0.5 * rate)
    best = -120.0
    for s in range(0, max(1, len(x) - w + 1), w // 2):
        best = max(best, 10 * np.log10(np.mean(x[s:s + w] ** 2) + 1e-12))
    return best


def pack(name):
    k = json.load(open(os.path.join(LIB, name, "keymap.json")))
    rate = k["samplerate"]
    assert rate in (44100, 32000, 22050), name
    samples, zones, levels, peaks = [], [], [], []
    mono = True
    for z in k["zones"]:
        a, sr = sf.read(os.path.join(LIB, name, z["file"]), dtype="int16", always_2d=True)
        assert sr == rate
        if a.shape[1] == 1:
            a = np.repeat(a, 2, axis=1)
        if not np.array_equal(a[:, 0], a[:, 1]):
            mono = False
        ls, le = (z["loop_start"], z["loop_end"]) if k["loop"] else (0, 0)
        if le:
            a = a[:le]
        idx = len(samples)
        samples.append((a, ls, le))
        zones.append((z["lokey"], z["hikey"], z["root"], z["lovel"], z["hivel"], int(z.get("tune", 0)), idx))
        if z["hivel"] >= 100 or z["lovel"] == 1 and z["hivel"] == 127:
            levels.append(attack_db(a, rate))
            peaks.append(np.abs(a.astype(np.int32)).max() / 32768)
    # loudness trim: typical attack of the top velocity layer to REF_DB
    gain = float(np.clip(10 ** ((REF_DB - np.median(levels)) / 20), 0.25, 8.0)) if levels else 1.0
    if peaks:
        gain = min(gain, PEAK_MAX / np.percentile(peaks, 90))

    data = bytearray()
    ents = []
    raw = []
    for a, ls, le in samples:
        c0 = encode_channel(a[:, 0].astype(np.int64))
        c1 = b"" if mono else encode_channel(a[:, 1].astype(np.int64))
        ents.append((len(data), len(a), ls, le))
        data += struct.pack("<II", len(c0), len(c1)) + c0 + c1
        raw.append(a)
    ns, nz = len(samples), len(zones)
    doff = 32 + ns * 16 + nz * 12
    out = bytearray(b"SNXB" + struct.pack("<IIIIIfI", 1, ns, nz, doff, 1 if mono else 0, gain, rate))
    for e in ents:
        out += struct.pack("<IIII", *e)
    for lo, hi, root, lv, hv, tune, idx in zones:
        out += struct.pack("<BBBBBBhI", lo, hi, root, lv, hv, 0, tune, idx)
    assert len(out) == doff
    out += data
    path = os.path.join(ENGINE, name + ".snx")
    with open(path + ".new", "wb") as f:
        f.write(out)
    os.replace(path + ".new", path)
    dump = os.environ.get("SONIX_DUMP_RAW")
    if dump:
        os.makedirs(dump, exist_ok=True)
        np.concatenate(raw).astype("<i2").tofile(os.path.join(dump, name + ".raw"))
    return len(out), gain, mono


CATEGORIES = ["PIANO", "KEYS", "GUITAR", "STRINGS", "BRASS", "CHOIR", "PAD", "SYNTH", "LEAD", "PLUCK", "BASS", "BELLS"]


def write_tables():
    names = [n for n, _, _ in ORDER]
    assert all(len(sh) <= 12 for _, _, sh in ORDER)
    cats = [CATEGORIES.index(json.load(open(os.path.join(LIB, n, "keymap.json")))["category"]) for n in names]
    with open(os.path.join(REPO, "src", "instruments.h"), "w") as f:
        f.write("/* Generated by tools/pack_sonix.py: the INSTRUMENT list (engine/<file>.snx), in panel order. */\n")
        f.write(f"#define NINST {len(names)}\n")
        f.write("static const char *INST_FILE[NINST] = {\n" + "".join(f'    "{n}",\n' for n in names) + "};\n")
        f.write("static const char *INST_NAME[NINST] = {\n" + "".join(f'    "{d}",\n' for _, d, _ in ORDER) + "};\n")
        f.write("static const char *INST_SHORT[NINST] = {\n" + "".join(f'    "{sh}",\n' for _, _, sh in ORDER) + "};\n")
        f.write(f"#define NCAT {len(CATEGORIES)}\n")
        f.write("static const char *CAT_NAME[NCAT] = { " + ", ".join(f'"{c}"' for c in CATEGORIES) + " };\n")
        f.write("static const unsigned char INST_CAT[NINST] = { " + ", ".join(str(c) for c in cats) + " };\n")
    pp = os.path.join(REPO, "vst", "params.json")
    if os.path.exists(pp):
        p = json.load(open(pp))
        for q in p["params"]:
            if q["key"] in ("inst_a", "inst_b", "inst_c", "inst_d"):
                q["options"] = [d for _, d, _ in ORDER]
                if q["default"] not in q["options"]:
                    q["default"] = ORDER[0][1]
        json.dump(p, open(pp, "w"), indent=2)
        open(pp, "a").write("\n")


def main():
    os.makedirs(ENGINE, exist_ok=True)
    want = sys.argv[1:] or [n for n, _, _ in ORDER]
    total = 0
    for n in want:
        size, gain, mono = pack(n)
        total += size
        print(f"{n:16s} {size / 1e6:6.1f} MB  trim {gain:5.2f}{'  mono' if mono else ''}", flush=True)
    write_tables()
    if not sys.argv[1:]:
        with open(os.path.join(ENGINE, "LIBRARY_VERSION"), "w") as f:
            f.write(LIBRARY_VERSION + "\n")
    print(f"packed {len(want)} instruments, {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
