#!/usr/bin/env python3
"""The Sonix parameter list, in one place. Writes vst/params.json (the VST parameters, for mpc-vst-plugins' gen_vst.py)
and src/params.h (the engine's matching enum, keys, defaults, ranges and value formats), plus src/presets.h from
tools/presets.py.

    python3 tools/sonix_params.py

Order is the VST parameter index. Once a version is public, only append (a saved project stores values by index).
v0.3 starts a new list (v0.2 was a device test)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from pack_sonix import ORDER, CATEGORIES  # noqa: E402

INSTRUMENTS = [d for _, d, _ in ORDER]
LAYERS = "abcd"
DELAY_TIMES = ["1/16", "1/8T", "1/8", "1/8.", "1/4T", "1/4", "1/4.", "1/2"]


def knob(key, name, default, lo=0.0, hi=1.0, fmt="PCT", integer=False, preset=True):
    return dict(key=key, name=name, kind="knob", min=lo, max=hi, default=default, fmt=fmt, integer=integer, preset=preset)


def option(key, name, options, default, preset=True, fmt="OPT"):
    return dict(key=key, name=name, kind="option", options=options, default=default, preset=preset, fmt=fmt)


def momentary(key, name, step_of=None, delta=0):
    return dict(key=key, name=name, kind="momentary", step_of=step_of, delta=delta)


def display(key, name, hi, kind="int"):
    return dict(key=key, name=name, kind="display", max=hi, disp=kind)


def spec():
    from presets import PRESETS
    P = [knob("volume", "Volume", 0.8, fmt="VOL", preset=False),           # index 0: MPC toggles it on insert / STOP
         option("preset", "Preset", [p["name"] for p in PRESETS], PRESETS[0]["name"], preset=False)]
    P += [momentary("cat_" + c.lower(), "Category " + c.title()) for c in CATEGORIES]
    P += [momentary("prev", "Previous Preset"), momentary("next", "Next Preset"),
          option("fav", "Favourite", ["Off", "On"], "Off", preset=False),
          option("fav_only", "Favourites Only", ["Off", "On"], "Off", preset=False)]
    P += [knob("cutoff", "Cutoff", 1.0, fmt="CUTOFF"), knob("resonance", "Resonance", 0.0),
          knob("env_amt", "Env Amount", 0.5, fmt="BIPOLAR"), knob("key_track", "Key Track", 0.0),
          knob("attack", "Attack", 0.0, fmt="ATTACK"), knob("decay", "Decay", 0.5, fmt="TIME"),
          knob("sustain", "Sustain", 1.0), knob("release", "Release", 0.55, fmt="TIME"),
          knob("lfo_rate", "LFO Rate", 0.5, fmt="LFO"), knob("lfo_filter", "LFO Filter", 0.0),
          knob("lfo_pitch", "LFO Pitch", 0.0, fmt="VIB"), knob("lfo_amp", "LFO Amp", 0.0),
          knob("chorus", "Chorus", 0.0), knob("delay", "Delay", 0.0), knob("reverb", "Reverb", 0.2)]
    for i, l in enumerate(LAYERS):
        L = l.upper()
        P += [option("layer_" + l, "Layer %s" % L, ["Off", "On"], "On" if i == 0 else "Off"),
              option("inst_" + l, "Layer %s Sound" % L, INSTRUMENTS, INSTRUMENTS[0]),
              knob("level_" + l, "Layer %s Level" % L, 0.8, fmt="LEVEL"),
              knob("pan_" + l, "Layer %s Pan" % L, 0.0, -1.0, 1.0, fmt="PAN"),
              knob("octave_" + l, "Layer %s Octave" % L, 0, -3, 3, fmt="OCT", integer=True),
              knob("fine_" + l, "Layer %s Fine" % L, 0, -100, 100, fmt="CENTS", integer=True),
              option("solo_" + l, "Layer %s Solo" % L, ["Off", "On"], "Off", preset=False),
              momentary("inst_%s_prev" % l, "Layer %s Previous" % L, "inst_" + l, -1),
              momentary("inst_%s_next" % l, "Layer %s Next" % L, "inst_" + l, 1)]
    P += [knob("a4_ref", "A4 Ref", 440, 400, 480, fmt="HZINT", integer=True, preset=False),
          knob("tune", "Tune", 0, -100, 100, fmt="CENTS", integer=True, preset=False),
          knob("transpose", "Transpose", 0, -24, 24, fmt="SEMI", integer=True, preset=False),
          knob("bend_range", "Bend Range", 2, 0, 24, fmt="SEMI", integer=True, preset=False),
          knob("polyphony", "Polyphony", 16, 1, 32, fmt="INT", integer=True, preset=False),
          knob("vel_sens", "Velocity", 0.8, preset=False),
          knob("vel_filter", "Vel > Filter", 0.25, preset=False),
          knob("spread", "Spread", 0.3, preset=False),
          option("delay_time", "Delay Time", DELAY_TIMES, "1/8."),
          knob("delay_fb", "Delay Feedback", 0.4), knob("delay_tone", "Delay Tone", 0.55, fmt="TONE"),
          knob("delay_width", "Delay Width", 0.8),
          knob("rev_size", "Reverb Size", 0.6), knob("rev_damp", "Reverb Damping", 0.4),
          knob("rev_predelay", "Reverb Pre-Delay", 0.1, fmt="PREDELAY"), knob("rev_width", "Reverb Width", 1.0),
          option("theme", "Theme", ["Midnight", "Platinum", "Studio Grey"], "Midnight", preset=False),
          option("anim", "Animation", ["Off", "Low", "Smooth"], "Smooth", preset=False),
          option("mod_wheel", "Mod Wheel", ["Vibrato", "Filter", "Off"], "Vibrato", preset=False),
          option("vel_curve", "Velocity Curve", ["Soft", "Normal", "Hard"], "Normal", preset=False)]
    P += [display("category", "Category", len(CATEGORIES) - 1), display("wave", "Waveform", 32), display("midi_led", "MIDI LED", 1), display("lfo_led", "LFO LED", 1),
          display("cpu", "CPU", 10)]
    P += [display("act_" + l, "Layer %s Active" % l.upper(), 1) for l in LAYERS]
    P += [display(k, n, 1, "string") for k, n in (
        ("lcd_top", "LCD Top"), ("lcd_ref", "LCD Reference"), ("lcd_name", "LCD Preset"),
        ("lcd_a", "LCD Layer A"), ("lcd_b", "LCD Layer B"), ("lcd_c", "LCD Layer C"), ("lcd_d", "LCD Layer D"),
        ("lcd_line", "LCD Line"))]
    P += [display("lcat_" + l, "Layer %s Category" % l.upper(), 1, "string") for l in LAYERS]
    P += [display("lname_" + l, "Layer %s Name" % l.upper(), 1, "string") for l in LAYERS]
    P += [display("fav_%d" % i, "Favourites %d" % (i + 1), 8191) for i in range(10)]
    return P


def to_json(P):
    out = []
    for p in P:
        q = {"key": p["key"], "name": p["name"]}
        if p["kind"] == "knob":
            q.update(min=p["min"], max=p["max"], default=p["default"], dynamic_display=True)
            if p["integer"]:
                q["display"] = "int"
        elif p["kind"] == "option":
            q.update(options=p["options"], default=p["default"])
            if p["key"] == "preset":
                q["qlink_ticks"] = 3
        elif p["kind"] == "momentary":
            q["momentary"] = True
            if p["step_of"]:
                q.update(step_of=p["step_of"], step_delta=p["delta"])
        else:
            q.update(min=0, max=p["max"], default=0, display=p["disp"])
        out.append(q)
    return {"name": "Sonix", "params": out}


def cf(x):
    """A C float literal."""
    return "%.6gf" % x if "." in "%.6g" % x or "e" in "%.6g" % x else "%.6g.0f" % x


def c_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_header(P):
    eng = [p for p in P if not (p["kind"] == "momentary" and p["step_of"])]   # the wrapper handles step_of itself
    L = ["/* Generated by tools/sonix_params.py: the engine's parameters (same order as vst/params.json). */",
         "enum {"]
    for p in P:
        L.append("    P_%s," % p["key"].upper())
    L += ["    NP", "};"]
    L.append("static const char *const PKEY[NP] = {\n" + "".join("    %s,\n" % c_str(p["key"]) for p in P) + "};")
    L.append("static const char *const PNAME[NP] = {\n" + "".join("    %s,\n" % c_str(p["name"].upper()) for p in P) + "};")
    kinds = {"knob": "K_KNOB", "option": "K_OPTION", "momentary": "K_MOMENTARY", "display": "K_DISPLAY"}
    L.append("enum { K_KNOB, K_OPTION, K_MOMENTARY, K_DISPLAY, K_TEXT };")
    fmts = sorted({p.get("fmt", "NONE") for p in P} | {"NONE"})
    L.append("enum { " + ", ".join("F_" + f for f in fmts) + " };")
    rows = []
    for p in P:
        kind = kinds[p["kind"]] if not (p["kind"] == "display" and p.get("disp") == "string") else "K_TEXT"
        if p["kind"] == "knob":
            lo, hi, d = p["min"], p["max"], p["default"]
        elif p["kind"] == "option":
            lo, hi, d = 0, len(p["options"]) - 1, p["options"].index(p["default"])
        elif p["kind"] == "display":
            lo, hi, d = 0, p["max"], 0
        else:
            lo, hi, d = 0, 1, 0
        rows.append("    { %s, F_%s, %s, %s, %s, %d, %d },  /* %s */" % (
            kind, p.get("fmt", "NONE"), cf(lo), cf(hi), cf(d), int(bool(p.get("integer")) or p["kind"] == "option"),
            int(bool(p.get("preset"))), p["key"]))
    L.append("typedef struct { int kind, fmt; float lo, hi, def; int whole, in_preset; } pinfo_t;")
    L.append("static const pinfo_t PINFO[NP] = {\n" + "\n".join(rows) + "\n};")
    L.append("#define NDELAY_TIMES %d" % len(DELAY_TIMES))
    L.append("static const float DELAY_BEATS[NDELAY_TIMES] = { 0.25f, 1.0f / 3, 0.5f, 0.75f, 2.0f / 3, 1.0f, 1.5f, 2.0f };")
    return "\n".join(L) + "\n"


def main():
    P = spec()
    keys = [p["key"] for p in P]
    assert len(keys) == len(set(keys))
    with open(os.path.join(REPO, "vst", "params.json"), "w") as f:
        json.dump(to_json(P), f, indent=2)
        f.write("\n")
    with open(os.path.join(REPO, "src", "params.h"), "w") as f:
        f.write(to_header(P))
    import presets
    presets.write_header(P, os.path.join(REPO, "src", "presets.h"))
    print("%d parameters, %d presets" % (len(P), len(presets.PRESETS)))


if __name__ == "__main__":
    main()
