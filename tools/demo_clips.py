"""Render audition clips by playing the prepared multisamples like the Sonix engine will:
zone lookup by key and velocity, repitching, loop playback, ADSR, plus a light reverb."""
import json
import os
import subprocess
import sys

import numpy as np
import soundfile as sf
from scipy.signal import fftconvolve

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prep import LIB, SR  # noqa: E402

OUT = os.path.join(LIB, "library")
CLIPS = os.path.join(LIB, "clips")
BPM = 92
BEAT = 60 / BPM


class Inst:
    def __init__(self, name):
        self.dir = os.path.join(OUT, name)
        self.k = json.load(open(os.path.join(self.dir, "keymap.json")))
        self.cache = {}

    def zone(self, note, vel):
        zs = [z for z in self.k["zones"] if z["lovel"] <= vel <= z["hivel"]]
        inside = [z for z in zs if z["lokey"] <= note <= z["hikey"]]
        return (inside or sorted(zs, key=lambda z: abs(z["root"] - note)))[0]

    def data(self, z):
        if z["file"] not in self.cache:
            self.cache[z["file"]] = sf.read(os.path.join(self.dir, z["file"]), dtype="float32", always_2d=True)[0]
        return self.cache[z["file"]]

    def play(self, note, vel, dur, attack=0.005, release=0.4, tune=0.0):
        z = self.zone(note, vel)
        x = self.data(z)
        ratio = 2 ** ((note - z["root"] + tune + z.get("tune", 0) / 100) / 12)
        need = int((dur + release) * SR * ratio) + 4
        if self.k["loop"]:
            ls, le = z["loop_start"], z["loop_end"]
            src = x[:le]
            while len(src) < need:
                src = np.concatenate([src, x[ls:le]])
        else:
            src = x
        n_out = min(int((dur + release) * SR), int((len(src) - 2) / ratio))
        pos = np.arange(n_out) * ratio
        i = pos.astype(int)
        f = (pos - i)[:, None]
        y = src[i] * (1 - f) + src[i + 1] * f
        t = np.arange(n_out) / SR
        env = np.minimum(1, t / max(attack, 1e-4))
        rel = np.clip(1 - (t - dur) / release, 0, 1)
        env = env * np.where(t > dur, rel, 1)
        gain = (0.35 + 0.65 * (vel / 127)) ** 1.2
        return y * (env * gain)[:, None]


def reverb_ir(secs=2.4, seed=7):
    rng = np.random.default_rng(seed)
    n = int(secs * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal((n, 2)) * np.exp(-t / (secs / 6.9))[:, None]
    ir[: int(0.012 * SR)] = 0
    return ir / np.sqrt(np.sum(ir ** 2))


IR = reverb_ir()


def render(inst_name, events, wet=0.22, tail=3.0, **play_kw):
    inst = Inst(inst_name)
    length = max(b + d for b, d, *_ in events) * BEAT + tail
    buf = np.zeros((int(length * SR) + SR, 2))
    for ev in events:
        beat, dur, notes, vel = ev[:4]
        kw = dict(play_kw, **(ev[4] if len(ev) > 4 else {}))
        for n in notes if isinstance(notes, (list, tuple)) else [notes]:
            y = inst.play(n, vel, dur * BEAT, **kw)
            s = int(beat * BEAT * SR)
            buf[s:s + len(y)] += y[: len(buf) - s]
    rev = fftconvolve(buf, IR, axes=0)[: len(buf)]
    mix = buf * (1 - wet * 0.5) + rev * wet * 2.2
    mix = mix[: int(length * SR)]
    # gentle level match across clips: RMS to about -18 dBFS, peaks kept under -1 dBFS
    rms = np.sqrt(np.mean(mix ** 2)) + 1e-9
    mix *= min(10 ** (-18 / 20) / rms, 10 ** (-1 / 20) / np.max(np.abs(mix)))
    n = int(0.05 * SR)
    mix[-n:] *= np.linspace(1, 0, n)[:, None]
    return mix


# ------------------------------------------------------------------ music
# I - vi - IV - V in C, one bar each
CH = [[48, 52, 55, 59], [45, 48, 52, 55], [45, 48, 52, 53], [43, 47, 50, 52]]
BASS = [36, 33, 29, 31]


def piano_part():
    ev = []
    mel = [(76, 74), (72, 76), (69, 72), (71, 74)]
    for b in range(4):
        t = b * 4
        ev.append((t, 4, BASS[b], 78))
        for i, n in enumerate(CH[b]):
            ev.append((t + i * 0.5, 4 - i * 0.5, n, 60 + 6 * i))
        ev.append((t + 2, 1, mel[b][0], 88))
        ev.append((t + 3, 1.6, mel[b][1], 92))
    ev.append((16, 6, [36, 43, 52, 59, 62, 67], 116))
    ev.append((16, 6, 72, 120))
    return ev


def strings_part():
    ev = []
    top = [76, 72, 69, 74]
    for b in range(4):
        v = 70 if b < 2 else 105                                   # swell into the upper layer
        ev.append((b * 4, 4.2, [BASS[b] + 12] + CH[b], v))
        ev.append((b * 4, 4.2, top[b], v))
    ev.append((16, 5, [36, 48, 55, 64, 67, 72], 112))
    return ev


def pad_part():
    ev = [(b * 4, 4.3, [BASS[b] + 12] + [n + 12 for n in CH[b]], 90) for b in range(4)]
    ev.append((16, 5, [48, 60, 64, 67, 71, 74], 90))
    return ev


def choir_part():
    ev = [(b * 4, 4.4, [n + 12 for n in CH[b]] + [BASS[b] + 24], 90) for b in range(4)]
    ev.append((16, 5, [60, 64, 67, 71, 76], 90))
    return ev


def lead_part():
    riff = [[72, 79, 76, 79, 84, 79, 76, 79], [69, 76, 72, 76, 81, 76, 72, 76],
            [69, 77, 72, 77, 81, 77, 72, 77], [71, 79, 74, 79, 83, 79, 74, 79]]
    ev = []
    for b in range(4):
        for i, n in enumerate(riff[b]):
            ev.append((b * 4 + i * 0.5, 0.42, n, 100))
    ev.append((16, 3, 84, 110))
    return ev


def bass_part():
    pat = [0, 0, 12, 0, 0, 12, 0, 7]
    ev = []
    for b in range(4):
        for i, o in enumerate(pat):
            ev.append((b * 4 + i * 0.5, 0.38, BASS[b] + o, 100))
    ev.append((16, 3, 24, 105))
    return ev


def epiano_part():
    ev = []
    top = [[76, 74], [72, 76], [69, 72], [71, 74]]
    for b in range(4):
        t = b * 4
        ev.append((t, 1.4, [BASS[b] + 12] + [n + 12 for n in CH[b]], 70))
        ev.append((t + 1.5, 2.3, [n + 12 for n in CH[b]], 104))
        ev.append((t + 2.5, 0.5, top[b][0], 96))
        ev.append((t + 3, 1, top[b][1], 110))
    ev.append((16, 5, [48, 64, 67, 71, 74, 79], 115))
    return ev


def guitar_part():
    shapes = [[48, 52, 55, 60, 64], [45, 52, 57, 60, 64], [41, 48, 53, 57, 60], [43, 47, 50, 55, 59]]
    order = [0, 2, 1, 3, 4, 3, 2, 1]
    ev = []
    for b in range(4):
        for i, k in enumerate(order):
            ev.append((b * 4 + i * 0.5, 2.5, shapes[b][k], 64 + (i % 4 == 0) * 40))
    ev.append((16, 5, [40, 47, 52, 56, 59, 64], 110))
    return ev


def mallet_part():
    mel = [76, 79, 74, 72, 76, 72, 69, 71]
    ev = []
    for b in range(4):
        for i in range(8):
            ev.append((b * 4 + i * 0.5, 1.5, CH[b][i % 4] + 12, 70 + (i % 2) * 20))
        ev.append((b * 4, 2, mel[b * 2] + 12, 105))
        ev.append((b * 4 + 2, 2, mel[b * 2 + 1] + 12, 100))
    ev.append((16, 4, [60, 64, 67, 71, 76, 84], 110))
    return ev


def glock_part():
    mel = [84, 88, 91, 88, 84, 81, 84, 86, 88, 86, 83, 79, 84]
    return [(i, 1.5, n, 90 + (i % 2) * 20) for i, n in enumerate(mel)]


def bell_part():
    mel = [(72, 2), (67, 2), (69, 2), (64, 2), (65, 2), (60, 2), (62, 2), (67, 2), (72, 4)]
    ev, t = [], 0
    for n, d in mel:
        ev.append((t, d, n, 110))
        t += d
    return ev


def harp_part():
    ev = []
    for b in range(4):
        notes = [BASS[b] + 12] + CH[b] + [n + 12 for n in CH[b]] + [CH[b][0] + 24]
        for i, n in enumerate(notes):
            ev.append((b * 4 + i * 0.25, 3.5, n, 70 + i * 4))
    ev.append((16, 5, [36, 48, 55, 64, 72, 79, 84], 105))
    return ev


def stab_part():
    ev = []
    for b in range(4):
        for t in (0, 1.5, 3):
            ev.append((b * 4 + t, 0.6, [BASS[b] + 12] + [n + 12 for n in CH[b]], 112))
    ev.append((16, 3, [48, 60, 64, 67, 71], 120))
    return ev


def pluck_part():
    riff = [[60, 67, 72, 67, 64, 67, 72, 76], [57, 64, 69, 64, 60, 64, 69, 72],
            [53, 60, 65, 60, 57, 60, 65, 69], [55, 62, 67, 62, 59, 62, 67, 71]]
    ev = []
    for b in range(4):
        for i, n in enumerate(riff[b]):
            ev.append((b * 4 + i * 0.5, 0.45, n, 70 + (i % 4 == 0) * 45))
    ev.append((16, 3, [60, 67, 72, 76], 115))
    return ev


def trance_part():
    roots = [60, 57, 53, 55]
    gate = [1, 0, 1, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 1, 1]
    ev = []
    for b in range(4):
        for i, g in enumerate(gate):
            if g:
                ev.append((b * 4 + i * 0.25, 0.2, roots[b], 105))
    ev.append((16, 2, 60, 115))
    return ev


def fx_part():
    return [(0, 6, 60, 110)]


CLIPS_DEF = [
    ("grand_piano", piano_part, dict(release=0.6), 0.22),
    ("upright_piano", piano_part, dict(release=0.6), 0.2),
    ("string_ensemble", strings_part, dict(attack=0.25, release=0.9), 0.28),
    ("nylon_guitar", guitar_part, dict(release=0.8), 0.18),
    ("warm_pad", pad_part, dict(attack=0.6, release=1.6), 0.3),
    ("aah_choir", choir_part, dict(attack=0.35, release=1.2), 0.3),
    ("supersaw_lead", lead_part, dict(attack=0.004, release=0.15), 0.25),
    ("sub_bass", bass_part, dict(attack=0.003, release=0.06), 0.05),
    ("fm_epiano", epiano_part, dict(release=0.5), 0.2),
    ("vibraphone", mallet_part, dict(release=1.2), 0.2),
    ("glockenspiel", glock_part, dict(release=1.0), 0.25),
    ("tubular_bells", bell_part, dict(release=2.0), 0.3),
    ("marimba", mallet_part, dict(release=0.5), 0.18),
    ("concert_harp", harp_part, dict(release=1.5), 0.25),
    ("kalimba", mallet_part, dict(release=0.8), 0.2),
    ("harpsichord", piano_part, dict(release=0.3), 0.2),
    ("pipe_organ", strings_part, dict(attack=0.02, release=0.5), 0.35),
    ("clavisynth", epiano_part, dict(release=0.3), 0.15),
    ("steel_guitar", guitar_part, dict(release=0.8), 0.18),
    ("orch_brass", strings_part, dict(attack=0.12, release=0.6), 0.28),
    ("synth_brass", stab_part, dict(attack=0.01, release=0.25), 0.22),
    ("drawbar_organ", epiano_part, dict(attack=0.005, release=0.08), 0.15),
    ("fm_bells", mallet_part, dict(release=1.5), 0.25),
    ("synth_pluck", pluck_part, dict(release=0.3), 0.25),
    ("ooh_choir", choir_part, dict(attack=0.35, release=1.2), 0.3),
    ("trance_chords", trance_part, dict(attack=0.002, release=0.05), 0.25),
    ("riser", fx_part, dict(release=0.2), 0.3),
    ("downlifter", fx_part, dict(release=0.2), 0.3),
]


def main():
    os.makedirs(CLIPS, exist_ok=True)
    only = set(sys.argv[1:])
    for name, part, kw, wet in CLIPS_DEF:
        if only and name not in only:
            continue
        mix = render(name, part(), wet=wet, **kw)
        wav = os.path.join(CLIPS, name + ".wav")
        sf.write(wav, mix, SR, subtype="PCM_16")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-b:a", "192k", wav[:-4] + ".mp3"], check=True)
        print(name, round(len(mix) / SR, 1), "s")


if __name__ == "__main__":
    main()
