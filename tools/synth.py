"""Sonix render generator: original synthesis for the multisamples we own outright.

Every sound here is computed from scratch (oscillators, filters, formants),
so the output carries no third-party rights.
"""
import numpy as np
from scipy.signal import butter, sosfilt, lfilter

SR = 44100


def mtof(m, a4=440.0):
    return a4 * 2 ** ((m - 69) / 12)


def _polyblep(t, dt):
    out = np.zeros_like(t)
    a = t < dt
    x = t[a] / dt[a] if np.ndim(dt) else t[a] / dt
    out[a] = x + x - x * x - 1
    b = t > 1 - dt
    x = (t[b] - 1) / (dt[b] if np.ndim(dt) else dt)
    out[b] = x * x + x + x + 1
    return out


def saw(freq, n, phase0=0.0):
    """Band-limited saw (polyBLEP). freq may be scalar or per-sample array."""
    f = np.broadcast_to(np.asarray(freq, dtype=np.float64), (n,))
    dt = f / SR
    t = (phase0 + np.cumsum(dt)) % 1.0
    return (2 * t - 1 - _polyblep(t, dt)).astype(np.float64)


def lowpass(x, cutoff, order=2):
    sos = butter(order, min(cutoff, SR * 0.45), "low", fs=SR, output="sos")
    return sosfilt(sos, x, axis=0)


def highpass(x, cutoff, order=2):
    sos = butter(order, cutoff, "high", fs=SR, output="sos")
    return sosfilt(sos, x, axis=0)


def resonator(x, f, bw):
    """Two-pole resonator (formant filter), unity peak gain."""
    r = np.exp(-np.pi * bw / SR)
    th = 2 * np.pi * f / SR
    a = [1, -2 * r * np.cos(th), r * r]
    b = [(1 - r * r) * 0.5]
    return lfilter(b, a, x)


def fade_in(x, ms):
    n = int(SR * ms / 1000)
    x[:n] *= np.linspace(0, 1, n)[:, None] if x.ndim == 2 else np.linspace(0, 1, n)
    return x


def vibrato(n, rate, depth_cents, rng, jitter=0.0):
    t = np.arange(n) / SR
    v = np.sin(2 * np.pi * rate * t + rng.uniform(0, 2 * np.pi)) * depth_cents
    if jitter:
        noise = lowpass(rng.standard_normal(n), 6.0, 1)
        v += noise / (np.std(noise) + 1e-9) * jitter
    return 2 ** (v / 1200)


def supersaw(f, n, voices, spread_cents, rng):
    """Detuned saw stack, spread across the stereo field."""
    out = np.zeros((n, 2))
    offs = np.linspace(-1, 1, voices) * spread_cents
    for i, c in enumerate(offs):
        drift = vibrato(n, rng.uniform(0.1, 0.35), 1.5, rng)
        s = saw(f * 2 ** (c / 1200) * drift, n, rng.uniform())
        pan = 0.5 + 0.45 * (i / (voices - 1) * 2 - 1) if voices > 1 else 0.5
        out[:, 0] += s * np.cos(pan * np.pi / 2)
        out[:, 1] += s * np.sin(pan * np.pi / 2)
    return out / np.sqrt(voices)


# ---------------------------------------------------------------- instruments

def warm_pad(m, rng, secs=7.0):
    f = mtof(m)
    n = int(secs * SR)
    x = supersaw(f, n, 5, 14, rng)
    x += 0.5 * supersaw(f * 0.5, n, 3, 8, rng)               # sub-octave body
    x = lowpass(x, 900 + 3.5 * f, 2)                           # warm, a little brightness up high
    x = highpass(x, 40)
    return fade_in(x, 40)


def supersaw_lead(m, rng, secs=4.0):
    f = mtof(m)
    n = int(secs * SR)
    x = supersaw(f, n, 7, 22, rng)
    x = lowpass(x, 7000 + 4 * f, 2)
    x = highpass(x, 80)
    return fade_in(x, 5)


VOWEL_AH = [(800, 80, 1.0), (1150, 90, 0.5), (2900, 120, 0.25), (3900, 130, 0.2), (4950, 140, 0.1)]


def formant_choir(m, rng, secs=7.0, singers=8, formants=VOWEL_AH):
    """'Aah' ensemble: glottal-like sources with vibrato and jitter, through vowel formants."""
    f = mtof(m)
    n = int(secs * SR)
    out = np.zeros((n, 2))
    for i in range(singers):
        det = rng.uniform(-10, 10)
        fr = f * 2 ** (det / 1200) * vibrato(n, rng.uniform(4.6, 5.8), rng.uniform(12, 22), rng, jitter=4)
        src = saw(fr, n, rng.uniform())
        src = lfilter([1], [1, -0.92], src) * 0.08               # spectral tilt of a voice
        breath = highpass(rng.standard_normal(n), 1500) * 0.006
        src = src + breath
        voice = sum(g * resonator(src, F * rng.uniform(0.97, 1.03), bw) for F, bw, g in formants)
        swell = 1 + 0.08 * np.sin(2 * np.pi * rng.uniform(0.15, 0.35) * np.arange(n) / SR + rng.uniform(0, 6))
        pan = (i + 0.5) / singers
        out[:, 0] += voice * swell * np.cos(pan * np.pi / 2)
        out[:, 1] += voice * swell * np.sin(pan * np.pi / 2)
    out = highpass(out, 90)
    return fade_in(out, 120)


def sub_bass(m, rng, secs=3.0):
    f = mtof(m)
    n = int(secs * SR)
    t = np.arange(n) / SR
    sine = np.sin(2 * np.pi * f * t)
    grit = lowpass(saw(f, n, rng.uniform()), 3.5 * f + 120, 2) * 0.35
    x = np.tanh(1.4 * (sine + grit)) / np.tanh(1.4)
    x = np.stack([x, x], axis=1)
    return fade_in(x, 4)


def fm_epiano(m, vel, rng, secs=4.5):
    """Two-operator FM 'tine' piano. vel in 0..1 drives brightness and bark."""
    f = mtof(m)
    n = int(secs * SR)
    t = np.arange(n) / SR
    decay = 1.2 + 2.6 * (1 - (m - 36) / 60)                    # low notes ring longer
    amp = np.exp(-t / max(decay, 0.6)) * (1 - np.exp(-t / 0.002))
    idx = (0.6 + 2.4 * vel) * np.exp(-t / 0.35)                  # body 'bark'
    body = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
    tine_idx = (0.4 + 1.2 * vel) * np.exp(-t / 0.04)
    tine = np.sin(2 * np.pi * f * t + tine_idx * np.sin(2 * np.pi * 14 * f * t)) - np.sin(2 * np.pi * f * t)
    x = (body + 0.6 * tine) * amp
    trem = 1 + 0.0 * t
    l = x * trem
    r = np.roll(x, int(0.0004 * SR))
    return np.stack([l, r], axis=1)


# ---------------------------------------------------------------- batch 2

def _ramp_filter(x, f_start, f_end, t_sweep, steps=24):
    """Piecewise sweep of a 2-pole low-pass (baked filter envelope)."""
    n = len(x)
    out = np.zeros_like(x)
    edges = np.unique(np.concatenate([np.geomspace(1, max(2, int(t_sweep * SR)), steps).astype(int), [n]]))
    edges = np.concatenate([[0], edges[edges < n], [n]])
    zi = None
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        t = min(a / SR / t_sweep, 1.0)
        fc = f_start * (f_end / f_start) ** t
        sos = butter(2, min(fc, SR * 0.45), "low", fs=SR, output="sos")
        if zi is None:
            from scipy.signal import sosfilt_zi
            zi = np.stack([sosfilt_zi(sos)] * x.shape[1], axis=-1) * 0
        out[a:b], zi = sosfilt(sos, x[a:b], axis=0, zi=zi)
    return out


def synth_brass(m, rng, secs=4.0):
    f = mtof(m)
    n = int(secs * SR)
    x = supersaw(f, n, 3, 9, rng)
    x = _ramp_filter(x, 300 + f, 2200 + 3 * f, 0.12)        # the brassy 'blat' on the attack
    x = highpass(x, 60)
    return fade_in(x, 12)


def drawbar_organ(m, rng, secs=3.0):
    f = mtof(m)
    n = int(secs * SR)
    t = np.arange(n) / SR
    bars = [(0.5, 0.6), (1, 1.0), (1.5, 0.45), (2, 0.7), (3, 0.3), (4, 0.35), (8, 0.15)]
    sig = np.zeros(n)
    for ratio, lvl in bars:
        fh = f * ratio
        if fh < SR * 0.45:
            sig += lvl * np.sin(2 * np.pi * fh * t + rng.uniform(0, 6.28))
    click = highpass(rng.standard_normal(n), 2000) * np.exp(-t / 0.004) * 0.25
    sig = (sig + click) / 3
    # gentle rotary-style shimmer: two slightly detuned copies across the field
    l = sig * (1 + 0.04 * np.sin(2 * np.pi * 5.8 * t))
    r = np.interp(t * (1 + 0.0007), t, sig) * (1 + 0.04 * np.sin(2 * np.pi * 5.8 * t + 1.6))
    return fade_in(np.stack([l, r], axis=1), 3)


def fm_bell(m, vel, rng, secs=6.0):
    f = mtof(m)
    n = int(secs * SR)
    t = np.arange(n) / SR
    amp = np.exp(-t / (2.2 - 1.0 * (m - 48) / 48)) * (1 - np.exp(-t / 0.0015))
    idx = (1.2 + 3.0 * vel) * np.exp(-t / 1.1)
    x = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * 3.5 * f * t))
    x += 0.35 * np.sin(2 * np.pi * 2.0 * f * t + 0.5 * idx * np.sin(2 * np.pi * 7.0 * f * t)) * np.exp(-t / 0.7)
    x *= amp
    return np.stack([x, np.roll(x, int(0.0006 * SR))], axis=1)


def synth_pluck(m, vel, rng, secs=2.2):
    f = mtof(m)
    n = int(secs * SR)
    t = np.arange(n) / SR
    x = supersaw(f, n, 3, 7, rng) + 0.4 * np.sign(np.sin(2 * np.pi * f * t))[:, None]
    x = _ramp_filter(x, 2500 + 6000 * vel + 4 * f, 300 + f, 0.35)
    x *= (np.exp(-t / 0.55) * (1 - np.exp(-t / 0.001)))[:, None]
    return x


VOWEL_OOH = [(320, 60, 1.0), (800, 80, 0.25), (2300, 110, 0.06), (3000, 130, 0.04)]


def ooh_choir(m, rng, secs=7.0):
    return formant_choir(m, rng, secs=secs, formants=VOWEL_OOH)


def trance_chord(m, rng, secs=1.6):
    """Minor-seventh supersaw stab; plays as a chord from one key."""
    f = mtof(m)
    n = int(secs * SR)
    t = np.arange(n) / SR
    x = sum(supersaw(f * 2 ** (iv / 12), n, 5, 18, rng) for iv in (0, 3, 7, 10, 12)) / 2.2
    x = _ramp_filter(x, 9000, 1800 + 2 * f, 0.6)
    x *= (np.exp(-t / 0.5) * (1 - np.exp(-t / 0.002)))[:, None]
    return x


def riser(m, rng, secs=4.0):
    n = int(secs * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal((n, 2))
    sweep = _ramp_filter(noise, 300, 12000, secs, steps=48) * 0.6
    pitch = mtof(m) * 2 ** (24 * (t / secs) ** 2 / 12)
    tone = supersaw(pitch, n, 5, 25, rng) * 0.35
    env = (t / secs) ** 1.8
    return (sweep + tone) * env[:, None]


def downlifter(m, rng, secs=3.0):
    n = int(secs * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal((n, 2))
    sweep = _ramp_filter(noise, 12000, 200, secs, steps=48) * 0.6
    pitch = mtof(m) * 2 ** (-24 * (t / secs) ** 0.7 / 12)
    tone = supersaw(pitch, n, 5, 25, rng) * 0.35
    env = np.exp(-t / (secs / 3)) * (1 - np.exp(-t / 0.01))
    return (sweep + tone) * env[:, None]
