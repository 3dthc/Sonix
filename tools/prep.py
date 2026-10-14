"""Sonix sample prep.

Turns raw recordings and our own renders into Sonix multisamples:
trim -> fit length -> (loop) -> normalize per instrument -> 44.1 kHz / 16-bit FLAC
-> keymap JSON, with the source and license of every file recorded.
"""
import json
import os
import re
from fractions import Fraction

import numpy as np
import soundfile as sf
from scipy.signal import correlate, resample_poly

SR = 44100
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.dirname(HERE)
SOURCES = json.load(open(os.path.join(LIB, "sources.json")))

NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_to_midi(name):
    """'A0', 'F#3', 'Bb2', 'As0' (A-sharp), 'Cs4' -> MIDI number (C4 = 60)."""
    m = re.fullmatch(r"([A-Ga-g])(#|b|s)?(-?\d)", name)
    if not m:
        raise ValueError(name)
    n = NOTE_BASE[m.group(1).upper()]
    acc = m.group(2)
    if acc in ("#", "s"):
        n += 1
    elif acc == "b":
        n -= 1
    return n + 12 * (int(m.group(3)) + 1)


def midi_name(m):
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[m % 12]}{m // 12 - 1}"


def load(path, sr=SR):
    """Load any file as float32 stereo at `sr`."""
    x, fs = sf.read(path, dtype="float32", always_2d=True)
    if fs != sr:
        fr = Fraction(sr, fs).limit_denominator(1000)
        x = resample_poly(x, fr.numerator, fr.denominator, axis=0).astype(np.float32)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2]


def db(v):
    return 20 * np.log10(max(v, 1e-12))


def trim_start(x, thresh_db=-50.0, pre_ms=2.0):
    env = np.max(np.abs(x), axis=1)
    peak = env.max()
    idx = np.argmax(env > peak * 10 ** (thresh_db / 20))
    idx = max(0, idx - int(SR * pre_ms / 1000))
    return x[idx:]


def fit_length(x, max_s, fade_s=0.3, floor_db=-60.0):
    """Cut the tail where it falls below `floor_db` (re peak) or at max_s, then fade out. (44.1 kHz input)"""
    env = np.max(np.abs(x), axis=1)
    peak = env.max()
    above = np.nonzero(env > peak * 10 ** (floor_db / 20))[0]
    end = min(len(x), (above[-1] + 1) if len(above) else len(x), int(max_s * SR))
    x = x[:end].copy()
    n = min(int(fade_s * SR), len(x))
    if n > 0:
        x[-n:] *= np.cos(np.linspace(0, np.pi / 2, n))[:, None] ** 2
    return x


def find_loop(x, region=(0.35, 0.9), xfade_s=0.25, win=4096, sr=SR):
    """Pick loop points in the steady part and bake an equal-power crossfade.

    Returns (y, loop_start, loop_end): y ends at loop_end; playback jumps from
    loop_end back to loop_start without a click.
    """
    mono = x.mean(axis=1)
    n = len(mono)
    end = int(n * region[1])
    lo = int(n * region[0])
    L = int(xfade_s * sr)
    ref = mono[end - win:end]
    # search start candidates in the first half of the region
    s_lo, s_hi = max(lo, L + win), max(lo + 1, int(n * (region[0] + (region[1] - region[0]) * 0.45)))
    seg = mono[s_lo - win:s_hi]
    c = correlate(seg, ref, mode="valid")
    energy = np.sqrt(np.convolve(seg ** 2, np.ones(win), mode="valid")) * np.sqrt(np.sum(ref ** 2)) + 1e-9
    score = c / energy
    best = int(np.argmax(score))
    start = s_lo + best
    y = x[:end].copy()
    w = np.sin(np.linspace(0, np.pi / 2, L)) ** 2
    y[end - L:end] = x[end - L:end] * (1 - w)[:, None] + x[start - L:start] * w[:, None]
    return y, start, end, float(score[best])


def _spectrum(x, start_s=0.05, secs=0.6):
    m = x.mean(axis=1)
    a = min(int(start_s * SR), max(0, len(m) - 4096))
    seg = m[a:a + int(secs * SR)]
    N = 1 << 19
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), N))
    return sp, np.fft.rfftfreq(N, 1 / SR)


def measured_root(x, named, search=(-24, -12, 0, 12, 24)):
    """Pick the octave of `named` that the audio actually sounds in.

    Scores each candidate by energy at its harmonics minus energy at the
    half-harmonics (which only an octave-too-high candidate would see).
    Sample names in different libraries use different middle-C conventions,
    so the mapping always follows the measured pitch."""
    sp, fr = _spectrum(x)

    def s(f):
        if f > SR * 0.45:
            return 0.0
        lo, hi = np.searchsorted(fr, [f * 2 ** (-0.4 / 12), f * 2 ** (0.4 / 12)])
        return float(sp[lo:hi].max()) if hi > lo else 0.0

    # The true pitch is the HIGHEST candidate whose harmonic series explains the spectrum:
    # little energy between its harmonics (an octave too high leaves real partials there),
    # and real energy on them (an octave too low only adds empty odd harmonics, so it is
    # never preferred because we take the highest passing candidate).
    passing = []
    for k in sorted(search):
        m = named + k
        if not 12 <= m <= 120:
            continue
        f = 440 * 2 ** ((m - 69) / 12)
        harm = np.mean([s(f * h) for h in range(1, 9)])
        half = np.mean([s(f * (h - 0.5)) for h in range(1, 9)])
        if harm > 0 and half / harm < 0.25:
            passing.append(m)
    return passing[-1] if passing else named


def measured_cents(x, root):
    """Deviation of the sample from equal temperament at `root`, in cents (from the
    strongest of the first three harmonics, searched within a semitone)."""
    sp, fr = _spectrum(x)
    f = 440 * 2 ** ((root - 69) / 12)
    best = (0.0, 0.0)
    for h in (1, 2, 3):
        lo, hi = np.searchsorted(fr, [f * h * 2 ** (-0.7 / 12), f * h * 2 ** (0.7 / 12)])
        i = lo + int(np.argmax(sp[lo:hi]))
        if sp[i] > best[0]:
            best = (sp[i], 1200 * np.log2(fr[i] / (f * h)))
    return float(best[1])


def folder_zones(files, note_re, layer_key, max_len=8.0, measure=True, extra=None, fine_tune=False, shift=None):
    """Build zones from files: `note_re` captures the note name, `layer_key(path)` gives
    a sortable velocity label (None = skip). Layers spread evenly over velocity 1-127."""
    items = []
    for p in files:
        m = re.search(note_re, os.path.basename(p))
        if not m:
            continue
        lk = layer_key(p)
        if lk is None:
            continue
        items.append((p, note_to_midi(m.group(1)), lk))
    labels = sorted({lk for *_, lk in items})
    n = len(labels)
    bounds = [(1 + round(127 * i / n), round(127 * (i + 1) / n)) for i in range(n)]
    bounds[0] = (1, bounds[0][1])
    loaded = [(p, named, lk, load(p)) for p, named, lk in items]
    # One sample set uses one naming convention, so apply the octave offset most samples
    # measure at. Single notes can mis-measure (bells, weak low fundamentals); the mode can't.
    if shift is None and measure and loaded:
        offs = [measured_root(x, named) - named for _, named, _, x in loaded]
        shift = max(set(offs), key=offs.count)
    shift = shift or 0
    zones, seen = [], set()
    for p, named, lk, x in loaded:
        root = named + shift
        L = labels.index(lk)
        if (root, L) in seen:
            continue
        seen.add((root, L))
        z = dict(audio=x, path=p, root=root, vel=L + 1, lovel=bounds[L][0], hivel=bounds[L][1], max_len=max_len)
        if fine_tune:   # instruments tuned off equal temperament get a per-zone correction
            z["tune"] = -round(measured_cents(x, root))
        if extra:
            z.update(extra(p, root))
        zones.append(z)
    return zones


def key_ranges(roots, stretch=12):
    """Split the keyboard between sorted root notes (midpoints)."""
    roots = sorted(roots)
    out = {}
    for i, r in enumerate(roots):
        lo = 0 if i == 0 else (roots[i - 1] + r) // 2 + 1
        hi = 127 if i == len(roots) - 1 else (r + roots[i + 1]) // 2
        lo = max(lo, r - stretch)
        hi = min(hi, r + stretch)
        out[r] = (lo, hi)
    return out


def to_rate(x, rate):
    if rate == SR:
        return x
    fr = Fraction(rate, SR).limit_denominator(1000)
    return resample_poly(x, fr.numerator, fr.denominator, axis=0).astype(np.float32)


def build_instrument(name, category, source, zones, out_root, loop=False,
                     max_len=8.0, peak_db=-1.0, loop_region=(0.35, 0.9), trim=True,
                     rate=SR, mono=False, floor_db=-60.0, fade_s=0.3):
    """zones: list of dicts {audio (array) or path, root, vel (layer index), lovel, hivel}.

    rate: the stored sample rate (32000 or 22050 for sounds with little or nothing above 16 / 11 kHz: synth pads,
    basses); mono: store one channel (the engine's chorus and spread widen it); floor_db: where a one-shot's tail
    is cut (re its peak), then faded over fade_s."""
    assert source in SOURCES, source
    out_dir = os.path.join(out_root, name)
    if os.path.isdir(out_dir):                       # a rebuild replaces the whole instrument
        for f in os.listdir(out_dir):
            os.remove(os.path.join(out_dir, f))
    os.makedirs(out_dir, exist_ok=True)
    processed = []
    for z in zones:
        x = z["audio"] if "audio" in z else load(z["path"])
        if trim:
            x = trim_start(x)
        x = fit_length(x, z.get("max_len", max_len), fade_s=0.02 if loop else fade_s, floor_db=floor_db)
        if mono:
            x = np.repeat(x.mean(axis=1, keepdims=True), 2, axis=1)
        x = to_rate(x, rate)
        ls = le = None
        q = None
        if loop:
            x, ls, le, q = find_loop(x, region=loop_region, sr=rate)
        processed.append((z, x, ls, le, q))

    gain = 10 ** (peak_db / 20) / max(np.max(np.abs(x)) for _, x, *_ in processed)

    layers = {}
    for z, *_ in processed:
        layers.setdefault(z["vel"], []).append(z["root"])
    ranges = {v: key_ranges(r) for v, r in layers.items()}

    entries, total = [], 0
    for z, x, ls, le, q in processed:
        fn = f"{name}_{z['root']:03d}_{midi_name(z['root']).replace('#', 's')}_v{z['vel']}.flac"
        path = os.path.join(out_dir, fn)
        y = np.clip(x * gain, -1, 1)
        sf.write(path, y[:, :1] if mono else y, rate, subtype="PCM_16", format="FLAC")
        size = os.path.getsize(path)
        total += size
        lk, hk = ranges[z["vel"]][z["root"]]
        e = {"file": fn, "root": z["root"], "lokey": lk, "hikey": hk,
             "lovel": z["lovel"], "hivel": z["hivel"], "frames": len(x),
             "orig": os.path.relpath(z["path"], LIB) if "path" in z else "rendered",
             "bytes": size}
        if "tune" in z:
            e["tune"] = z["tune"]
        if loop:
            e.update(loop_start=int(ls), loop_end=int(le), loop_score=round(q, 3))
        entries.append(e)

    keymap = {"name": name, "category": category, "source": source,
              "license": SOURCES[source]["license"], "credit": SOURCES[source]["credit"],
              "samplerate": rate, "loop": loop, "zones": sorted(entries, key=lambda e: (e["lovel"], e["root"])),
              "total_bytes": total}
    json.dump(keymap, open(os.path.join(out_dir, "keymap.json"), "w"), indent=1)
    return keymap


def write_credits(out_root):
    """Regenerate LICENSES.md and a per-file CSV from every keymap in the library."""
    rows, used, sizes = [], {}, {}
    for inst in sorted(os.listdir(out_root)):
        kp = os.path.join(out_root, inst, "keymap.json")
        if not os.path.exists(kp):
            continue
        k = json.load(open(kp))
        used.setdefault(k["source"], []).append(inst)
        sizes[inst] = k["total_bytes"]
        for z in k["zones"]:
            rows.append(f'{inst}/{z["file"]},{k["source"]},{k["license"]},"{z["orig"]}"')
    with open(os.path.join(out_root, "files.csv"), "w") as f:
        f.write("file,source,license,original\n" + "\n".join(rows) + "\n")
    lines = ["# Sonix sound library: sources and licenses", ""]
    for s, insts in used.items():
        src = SOURCES[s]
        lines += [f"## {src['name']}", "", f"- License: {src['license']}",
                  f"- Source: {src['url'] or 'original to this project'}",
                  f"- Credit: {src['credit']}", f"- Used in: {', '.join(insts)}", ""]
    lines += ["Every sample file and its original is listed in files.csv."]
    open(os.path.join(out_root, "LICENSES.md"), "w").write("\n".join(lines) + "\n")
    return sizes
