"""The Sonix factory presets: 128 sounds in the panel's category order. tools/sonix_params.py writes them into
vst/params.json (the PRESET list MPC shows) and src/presets.h (what the engine sets when one is picked).

A preset sets every layer and every sound parameter (filter, envelope, LFO, effects and their settings); the
global settings (volume, tuning, playing, system) stay as they are. Values are written in musical units here and
converted to the engine's knob positions below."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pack_sonix import ORDER, CATEGORIES  # noqa: E402

INST = [n for n, _, _ in ORDER]


# ---- the engine's knob curves, inverted (src/engine.c)
def attack(sec):
    return round(min(1.0, max(0.0, math.log10(sec / 0.001) / 4)), 3)


def dr(sec):                       # decay and release
    return round(min(1.0, max(0.0, math.log(sec / 0.005) / math.log(2000))), 3)


def cutoff(hz):
    return round(min(1.0, max(0.0, math.log(hz / 20) / math.log(1000))), 3)


def lfo(hz):
    return round(min(1.0, max(0.0, math.log(hz / 0.05) / math.log(400))), 3)


def level(db):                     # layer level: gain (level / 0.8)^2
    return round(min(1.0, 0.8 * 10 ** (db / 40)), 3)


BASE = dict(cutoff=1.0, resonance=0.0, env_amt=0.5, key_track=0.0, attack=attack(0.001), decay=0.5, sustain=1.0,
            release=dr(0.5), lfo_rate=lfo(4.0), lfo_filter=0.0, lfo_pitch=0.0, lfo_amp=0.0, chorus=0.0, delay=0.0,
            reverb=0.2, delay_time="1/8.", delay_fb=0.4, delay_tone=0.55, delay_width=0.8, rev_size=0.6, rev_damp=0.4,
            rev_predelay=0.1, rev_width=1.0)
PRESETS = []


def L(inst, db=0.0, pan=0.0, octave=0, fine=0):
    assert inst in INST, inst
    return (inst, level(db), pan, octave, fine)


def P(cat, name, desc, layers, **kw):
    assert cat in CATEGORIES and len(name) <= 20 and len(desc) <= 48 and 1 <= len(layers) <= 4, (name, desc)
    for k in kw:
        assert k in BASE, k
    v = dict(BASE, **kw)
    for i, l in enumerate("abcd"):
        if i < len(layers):
            inst, lvl, pan, octv, fine = layers[i] if isinstance(layers[i], tuple) else L(layers[i])
            v.update({"layer_" + l: 1, "inst_" + l: INST.index(inst), "level_" + l: lvl, "pan_" + l: pan,
                      "octave_" + l: octv, "fine_" + l: fine})
        else:
            v.update({"layer_" + l: 0, "inst_" + l: 0, "level_" + l: 0.8, "pan_" + l: 0.0, "octave_" + l: 0, "fine_" + l: 0})
    PRESETS.append(dict(cat=cat, name=name, desc=desc, values=v))


# =============================================================== PIANO
P("PIANO", "Concert Grand", "A full grand piano in a warm hall", ["grand_piano"],
  release=dr(0.6), reverb=0.24, rev_size=0.65)
P("PIANO", "Velvet Grand", "Soft grand over a slow pad and wide strings",
  [L("grand_piano"), L("warm_pad", -9), L("string_ensemble", -12)], cutoff=cutoff(9000), release=dr(0.9), chorus=0.15,
  reverb=0.35, rev_size=0.75)
P("PIANO", "Bright Stage", "A bright, close grand that cuts through", ["grand_piano"],
  key_track=0.3, release=dr(0.4), reverb=0.14, rev_size=0.4, rev_damp=0.25)
P("PIANO", "Felt Piano", "A dark, hushed grand with a long room", ["grand_piano"],
  cutoff=cutoff(1400), env_amt=0.58, key_track=0.4, release=dr(0.8), reverb=0.42, rev_size=0.8, rev_damp=0.6)
P("PIANO", "Lo-Fi Upright", "Worn upright with a gentle tape wobble", ["upright_piano"],
  cutoff=cutoff(3200), lfo_rate=lfo(0.6), lfo_pitch=0.08, chorus=0.25, reverb=0.25, rev_damp=0.6)
P("PIANO", "Upright Ballad", "Upright piano with soft strings behind",
  [L("upright_piano"), L("string_ensemble", -10)], release=dr(0.8), reverb=0.32)
P("PIANO", "Piano & Strings", "Grand piano doubled by a string section",
  [L("grand_piano"), L("string_ensemble", -5)], release=dr(0.9), reverb=0.35, rev_size=0.75)
P("PIANO", "Octave Grand", "Grand doubled an octave up for sparkle",
  [L("grand_piano"), L("grand_piano", -8, octave=1)], release=dr(0.6), reverb=0.28)
P("PIANO", "Cinematic Piano", "Piano with air, glass and a long echo",
  [L("grand_piano"), L("air_pad", -10), L("glass_pad", -12, octave=1)], release=dr(1.2), delay=0.2,
  delay_time="1/4", delay_fb=0.35, reverb=0.5, rev_size=0.9, rev_predelay=0.25)
P("PIANO", "Pad Layer Grand", "Grand over an analog pad and string machine",
  [L("grand_piano"), L("analog_pad", -8), L("synth_strings", -11)], release=dr(1.0), chorus=0.2, reverb=0.32)
P("PIANO", "Saloon Upright", "Three detuned uprights: honky-tonk",
  [L("upright_piano"), L("upright_piano", -3, pan=-0.4, fine=12), L("upright_piano", -3, pan=0.4, fine=-12)],
  release=dr(0.4), reverb=0.16, rev_size=0.35)

# =============================================================== KEYS
P("KEYS", "FM Tine EP", "Bell-toned electric piano with chorus", ["fm_epiano"],
  release=dr(0.45), chorus=0.35, reverb=0.2)
P("KEYS", "Soul EP", "Electric piano with a slow tremolo", ["fm_epiano"],
  release=dr(0.4), lfo_rate=lfo(4.5), lfo_amp=0.35, chorus=0.2, reverb=0.22)
P("KEYS", "EP & Pad", "Electric piano over a warm pad", [L("fm_epiano"), L("warm_pad", -8)],
  release=dr(0.9), chorus=0.3, reverb=0.3)
P("KEYS", "Funky Clav", "Snappy clav with a filter bite", ["clavisynth"],
  cutoff=cutoff(1800), resonance=0.3, env_amt=0.72, decay=dr(0.35), sustain=0.55, release=dr(0.12), reverb=0.1)
P("KEYS", "Harpsichord", "Baroque harpsichord in a small hall", ["harpsichord"],
  release=dr(0.3), reverb=0.28, rev_size=0.55)
P("KEYS", "Harpsi 4-Foot", "Harpsichord with its octave stop drawn",
  [L("harpsichord"), L("harpsichord", -7, octave=1)], release=dr(0.3), reverb=0.28)
P("KEYS", "Gospel Drawbars", "Drawbar organ with a rotary shimmer", ["drawbar_organ"],
  attack=attack(0.004), release=dr(0.08), lfo_rate=lfo(6.2), lfo_amp=0.15, chorus=0.4, reverb=0.18)
P("KEYS", "Church Organ", "Pipe organ in a cathedral", ["pipe_organ"],
  attack=attack(0.03), release=dr(0.6), reverb=0.6, rev_size=0.95, rev_predelay=0.2)
P("KEYS", "Dark Cathedral", "Low organ pipes with a distant choir",
  [L("pipe_organ", octave=-1), L("ooh_choir", -6)], attack=attack(0.08), release=dr(1.2), reverb=0.7, rev_size=0.95)
P("KEYS", "Organ Pad", "Drawbars swelling into a pad", [L("drawbar_organ"), L("warm_pad", -6)],
  attack=attack(0.3), release=dr(0.9), chorus=0.35, reverb=0.3)
P("KEYS", "Bell EP", "Electric piano with a bell an octave up",
  [L("fm_epiano"), L("fm_bells", -9, octave=1)], release=dr(0.6), chorus=0.3, reverb=0.28)

# =============================================================== GUITAR
P("GUITAR", "Nylon Classic", "Classical nylon-string guitar", ["nylon_guitar"],
  release=dr(0.4), reverb=0.24)
P("GUITAR", "Steel Acoustic", "Bright steel-string acoustic", ["steel_guitar"],
  release=dr(0.4), chorus=0.1, reverb=0.2)
P("GUITAR", "Twelve String", "Steel strings doubled an octave up",
  [L("steel_guitar"), L("steel_guitar", -6, fine=6, octave=1)], release=dr(0.45), chorus=0.2, reverb=0.24)
P("GUITAR", "Clean Electric", "Clean electric with a touch of chorus", ["clean_electric"],
  release=dr(0.3), chorus=0.3, reverb=0.2)
P("GUITAR", "Jazz Box", "Warm, rounded hollow-body tone", ["clean_electric"],
  cutoff=cutoff(1500), key_track=0.3, release=dr(0.3), reverb=0.24, rev_size=0.5)
P("GUITAR", "Dream Chorus Gtr", "Washed-out chorus guitar with echoes", ["clean_electric"],
  release=dr(0.6), chorus=0.7, delay=0.28, delay_time="1/8.", reverb=0.32)
P("GUITAR", "Palm Muted", "Tight palm-muted picking", ["muted_electric"],
  release=dr(0.08), delay=0.12, delay_time="1/16", delay_fb=0.25, reverb=0.12)
P("GUITAR", "Funk Mute", "Muted scratch with a clean ring", [L("muted_electric"), L("clean_electric", -10)],
  cutoff=cutoff(2400), env_amt=0.65, release=dr(0.1), reverb=0.12)
P("GUITAR", "Nylon & Strings", "Nylon guitar with a soft string bed",
  [L("nylon_guitar"), L("string_ensemble", -9)], release=dr(0.7), reverb=0.32)
P("GUITAR", "Ambient Guitar", "Clean guitar dissolving into air",
  [L("clean_electric"), L("air_pad", -9)], release=dr(1.5), chorus=0.3, delay=0.35, delay_time="1/4.",
  delay_fb=0.55, reverb=0.55, rev_size=0.9)

# =============================================================== STRINGS
P("STRINGS", "String Ensemble", "A full string section, legato", ["string_ensemble"],
  attack=attack(0.12), release=dr(0.6), reverb=0.35, rev_size=0.75)
P("STRINGS", "Slow Strings", "Strings that swell in and linger", ["string_ensemble"],
  attack=attack(0.9), release=dr(1.6), reverb=0.45, rev_size=0.85)
P("STRINGS", "Solo Violin", "Expressive solo violin with vibrato", ["solo_violin"],
  attack=attack(0.06), release=dr(0.4), reverb=0.35, rev_size=0.7)
P("STRINGS", "Pizzicato", "Plucked string section", ["pizz_strings"],
  release=dr(0.25), reverb=0.3)
P("STRINGS", "Tremolo Strings", "Tense bowed tremolo", ["trem_strings"],
  attack=attack(0.1), release=dr(0.5), reverb=0.4, rev_size=0.8)
P("STRINGS", "Spiccato", "Short bouncing bows for ostinatos", ["spic_strings"],
  release=dr(0.15), reverb=0.3)
P("STRINGS", "Synth Strings", "A vintage string machine", ["synth_strings"],
  attack=attack(0.2), release=dr(0.8), chorus=0.5, reverb=0.3)
P("STRINGS", "Strings & Brass", "String section with a horn line", [L("string_ensemble"), L("orch_brass", -5)],
  attack=attack(0.1), release=dr(0.6), reverb=0.4, rev_size=0.8)
P("STRINGS", "Epic Octaves", "Low octaves and tremolo for big moments",
  [L("string_ensemble"), L("string_ensemble", -4, octave=-1), L("trem_strings", -8)], attack=attack(0.15),
  release=dr(0.9), reverb=0.5, rev_size=0.9)
P("STRINGS", "Pizz & Spic", "Pizzicato and spiccato together", [L("pizz_strings"), L("spic_strings", -4)],
  release=dr(0.2), reverb=0.3)
P("STRINGS", "Machine Strings", "String machine over real strings",
  [L("synth_strings"), L("string_ensemble", -6)], attack=attack(0.25), release=dr(1.0), chorus=0.35, reverb=0.35)

# =============================================================== BRASS
P("BRASS", "Orchestral Brass", "Trombones, horns and trumpets", ["orch_brass"],
  attack=attack(0.05), release=dr(0.35), reverb=0.35, rev_size=0.75)
P("BRASS", "Horn Swell", "Brass that blooms slowly", ["orch_brass"],
  attack=attack(0.6), release=dr(0.7), reverb=0.45, rev_size=0.85)
P("BRASS", "Synth Brass", "Analog brass with a punchy filter", ["synth_brass"],
  cutoff=cutoff(1500), env_amt=0.72, attack=attack(0.02), decay=dr(0.6), sustain=0.7, release=dr(0.3), reverb=0.2)
P("BRASS", "Big Synth Brass", "Two detuned synth brass layers", [L("synth_brass"), L("synth_brass", -2, fine=10)],
  cutoff=cutoff(2400), env_amt=0.68, attack=attack(0.02), release=dr(0.35), chorus=0.3, reverb=0.25)
P("BRASS", "Tenor Sax", "Breathy tenor saxophone", ["tenor_sax"],
  attack=attack(0.02), release=dr(0.25), reverb=0.25)
P("BRASS", "Smoky Sax", "Dark late-night sax with an echo", ["tenor_sax"],
  cutoff=cutoff(2600), release=dr(0.35), chorus=0.1, delay=0.15, delay_time="1/4", reverb=0.4)
P("BRASS", "Horn Section", "Sax and brass playing together", [L("tenor_sax"), L("orch_brass", -4)],
  attack=attack(0.03), release=dr(0.3), reverb=0.28)
P("BRASS", "Brass Pad", "Soft brass over a warm pad", [L("orch_brass"), L("warm_pad", -6)],
  attack=attack(0.4), release=dr(0.9), reverb=0.4)
P("BRASS", "Brass Stab", "Short, bright synth-brass hits", ["synth_brass"],
  cutoff=cutoff(3000), env_amt=0.7, decay=dr(0.25), sustain=0.25, release=dr(0.12), reverb=0.22)
P("BRASS", "Fanfare", "Brass in octaves with synth brass behind",
  [L("orch_brass"), L("orch_brass", -6, octave=1), L("synth_brass", -9)], attack=attack(0.04), release=dr(0.5),
  reverb=0.42, rev_size=0.85)

# =============================================================== CHOIR
P("CHOIR", "Aah Choir", "A choir singing 'aah'", ["aah_choir"],
  attack=attack(0.25), release=dr(0.8), reverb=0.45, rev_size=0.85)
P("CHOIR", "Ooh Choir", "Soft 'ooh' voices", ["ooh_choir"],
  attack=attack(0.25), release=dr(0.8), reverb=0.45, rev_size=0.85)
P("CHOIR", "Choir Ensemble", "Aah and ooh voices blended", [L("aah_choir"), L("ooh_choir", -3)],
  attack=attack(0.25), release=dr(0.9), reverb=0.5)
P("CHOIR", "Heaven Choir", "Choir and glass in a vast space", [L("aah_choir"), L("glass_pad", -9)],
  attack=attack(0.4), release=dr(1.5), reverb=0.7, rev_size=0.95, rev_predelay=0.25)
P("CHOIR", "Dark Choir", "Low, shadowed voices", [L("ooh_choir", octave=-1), L("aah_choir", -6)],
  cutoff=cutoff(1600), attack=attack(0.35), release=dr(1.2), reverb=0.6, rev_size=0.9)
P("CHOIR", "Vox Pad", "Voices melting into a pad", [L("aah_choir"), L("warm_pad", -6)],
  attack=attack(0.3), release=dr(1.0), chorus=0.3, reverb=0.45)
P("CHOIR", "Breathy Voices", "Whispered ooh with air", [L("ooh_choir"), L("air_pad", -6)],
  attack=attack(0.3), release=dr(1.0), reverb=0.5)
P("CHOIR", "Choir & Organ", "Voices with a pipe organ", [L("aah_choir"), L("pipe_organ", -6)],
  attack=attack(0.15), release=dr(1.0), reverb=0.6, rev_size=0.95)
P("CHOIR", "Choir Swell", "A slow rising choir", ["aah_choir"],
  attack=attack(1.5), release=dr(2.0), reverb=0.6, rev_size=0.9)
P("CHOIR", "Vocal Shimmer", "Voices in octaves with a glassy tail",
  [L("aah_choir"), L("aah_choir", -8, octave=1), L("glass_pad", -10)], attack=attack(0.3), release=dr(1.4),
  delay=0.22, delay_time="1/4", reverb=0.6, rev_size=0.9)

# =============================================================== PAD
P("PAD", "Warm Pad", "A soft, round analog pad", ["warm_pad"],
  attack=attack(0.3), release=dr(1.2), chorus=0.2, reverb=0.35)
P("PAD", "Analog Pad", "Drifting PWM poly pad", ["analog_pad"],
  attack=attack(0.25), release=dr(1.2), chorus=0.3, reverb=0.35)
P("PAD", "Glass Pad", "Clear, bell-tinged pad", ["glass_pad"],
  attack=attack(0.35), release=dr(1.5), reverb=0.45)
P("PAD", "Air Pad", "Breathy noise and soft tone", ["air_pad"],
  attack=attack(0.5), release=dr(1.6), reverb=0.5)
P("PAD", "Sweep Pad", "A pad with a slow filter sweep", ["analog_pad"],
  cutoff=cutoff(700), resonance=0.35, lfo_rate=lfo(0.12), lfo_filter=0.5, attack=attack(0.3), release=dr(1.3),
  chorus=0.3, reverb=0.4)
P("PAD", "Dark Matter", "Deep pad an octave down", [L("warm_pad", octave=-1), L("air_pad", -6)],
  cutoff=cutoff(1200), attack=attack(0.5), release=dr(1.8), reverb=0.5, rev_size=0.9)
P("PAD", "Shimmer Pad", "Glass in octaves with echoes", [L("glass_pad"), L("glass_pad", -6, octave=1)],
  attack=attack(0.4), release=dr(2.0), delay=0.3, delay_time="1/4.", delay_fb=0.5, reverb=0.7, rev_size=0.95)
P("PAD", "80s Poly", "PWM pad and string machine", [L("analog_pad"), L("synth_strings", -5)],
  attack=attack(0.15), release=dr(1.0), chorus=0.55, reverb=0.35)
P("PAD", "Evolving Air", "Air and glass that move slowly", [L("air_pad"), L("glass_pad", -7)],
  cutoff=cutoff(3500), lfo_rate=lfo(0.2), lfo_filter=0.3, attack=attack(0.8), release=dr(2.0), reverb=0.55)
P("PAD", "Pad Stack", "Three pads layered wide",
  [L("warm_pad", pan=-0.3), L("analog_pad", -2, pan=0.3), L("glass_pad", -8, octave=1)], attack=attack(0.35),
  release=dr(1.5), chorus=0.3, reverb=0.45)
P("PAD", "Silk Pad", "Pad and strings, wide and smooth", [L("warm_pad"), L("string_ensemble", -5)],
  attack=attack(0.4), release=dr(1.4), chorus=0.25, reverb=0.45)

# =============================================================== SYNTH
P("SYNTH", "Trance Chords", "Minor-seventh supersaw stab", ["trance_chords"],
  release=dr(0.25), delay=0.3, delay_time="1/8.", reverb=0.3)
P("SYNTH", "Dub Chord", "Dub-techno chord with a long echo", ["dub_chord"],
  cutoff=cutoff(3000), release=dr(0.3), delay=0.45, delay_time="1/4.", delay_fb=0.55, delay_tone=0.4, reverb=0.35)
P("SYNTH", "Deep Dub", "Dark filtered chords in the echo", ["dub_chord"],
  cutoff=cutoff(700), resonance=0.3, release=dr(0.3), delay=0.5, delay_time="1/8.", delay_fb=0.6, delay_tone=0.35,
  reverb=0.4, rev_size=0.8)
P("SYNTH", "Rave Stab", "Supersaw chord stab for rave", [L("trance_chords"), L("supersaw_lead", -8)],
  release=dr(0.2), reverb=0.25)
P("SYNTH", "Riser", "A white-noise riser", ["riser"], release=dr(0.4), reverb=0.4)
P("SYNTH", "Downlifter", "A falling noise sweep", ["downlifter"], release=dr(0.4), reverb=0.4)
P("SYNTH", "Supersaw Stack", "Supersaws in octaves", [L("supersaw_lead"), L("supersaw_lead", -5, octave=-1)],
  attack=attack(0.005), release=dr(0.3), chorus=0.3, reverb=0.3)
P("SYNTH", "Chip Square", "Short square blips", ["square_lead"],
  decay=dr(0.18), sustain=0.2, release=dr(0.08), reverb=0.12)
P("SYNTH", "Wobble Stab", "Chords with a fast filter wobble", ["dub_chord"],
  cutoff=cutoff(1200), resonance=0.4, lfo_rate=lfo(4.0), lfo_filter=0.6, release=dr(0.3), reverb=0.25)
P("SYNTH", "Tape Chords", "Lo-fi supersaw chords on worn tape", ["trance_chords"],
  cutoff=cutoff(1800), lfo_rate=lfo(0.4), lfo_pitch=0.06, chorus=0.3, release=dr(0.3), reverb=0.3, rev_damp=0.6)
P("SYNTH", "Space Chord", "Dub chord drifting through glass", [L("dub_chord"), L("glass_pad", -8)],
  release=dr(1.0), delay=0.4, delay_time="1/4", delay_fb=0.5, reverb=0.65, rev_size=0.95)

# =============================================================== LEAD
P("LEAD", "Supersaw Lead", "Wide detuned saw lead", ["supersaw_lead"],
  release=dr(0.25), delay=0.2, reverb=0.25)
P("LEAD", "Square Lead", "Hollow pulse lead", ["square_lead"], release=dr(0.2), reverb=0.2)
P("LEAD", "Flute", "Concert flute with vibrato", ["flute"],
  attack=attack(0.04), release=dr(0.25), reverb=0.32)
P("LEAD", "Breathy Flute", "Flute with extra air", [L("flute"), L("air_pad", -9)],
  attack=attack(0.05), release=dr(0.35), reverb=0.38)
P("LEAD", "Saw & Square", "Saw and pulse lead together", [L("supersaw_lead"), L("square_lead", -3)],
  release=dr(0.25), reverb=0.22)
P("LEAD", "Vibrato Lead", "Singing pulse lead with vibrato", ["square_lead"],
  lfo_rate=lfo(5.5), lfo_pitch=0.25, release=dr(0.25), delay=0.25, reverb=0.25)
P("LEAD", "Octave Lead", "Supersaw doubled an octave up", [L("supersaw_lead"), L("supersaw_lead", -6, octave=1)],
  release=dr(0.25), delay=0.2, reverb=0.28)
P("LEAD", "Mellow Square", "Soft, round square lead", ["square_lead"],
  cutoff=cutoff(1100), key_track=0.5, release=dr(0.25), reverb=0.25)
P("LEAD", "Trance Lead", "Supersaw with dotted echoes", ["supersaw_lead"],
  release=dr(0.3), delay=0.4, delay_time="1/8.", delay_fb=0.45, reverb=0.45, rev_size=0.85)
P("LEAD", "Flute & Strings", "Flute melody with strings", [L("flute"), L("string_ensemble", -8)],
  attack=attack(0.05), release=dr(0.4), reverb=0.38)
P("LEAD", "Detuned Lead", "Saw and square pulling apart",
  [L("supersaw_lead", pan=-0.3, fine=-8), L("square_lead", -2, pan=0.3, fine=8)], release=dr(0.25), chorus=0.2,
  reverb=0.25)

# =============================================================== PLUCK
P("PLUCK", "Concert Harp", "Orchestral harp", ["concert_harp"], release=dr(0.6), reverb=0.35)
P("PLUCK", "Kalimba", "Thumb piano", ["kalimba"], release=dr(0.4), reverb=0.28)
P("PLUCK", "Synth Pluck", "Bright supersaw pluck", ["synth_pluck"], release=dr(0.3), reverb=0.25)
P("PLUCK", "Harp & Strings", "Harp with a soft string bed", [L("concert_harp"), L("string_ensemble", -10)],
  release=dr(0.8), reverb=0.4)
P("PLUCK", "Pluck Delay", "Pluck with dotted-eighth echoes", ["synth_pluck"],
  release=dr(0.3), delay=0.45, delay_time="1/8.", delay_fb=0.5, reverb=0.3)
P("PLUCK", "Kalimba Dream", "Kalimba over glass in a big space", [L("kalimba"), L("glass_pad", -10)],
  release=dr(0.9), delay=0.3, delay_time="1/4", reverb=0.5, rev_size=0.9)
P("PLUCK", "Bright Pluck", "A snappy, resonant pluck", ["synth_pluck"],
  cutoff=cutoff(2500), resonance=0.3, env_amt=0.7, decay=dr(0.25), sustain=0.4, release=dr(0.2), reverb=0.22)
P("PLUCK", "Celestial Harp", "Harp with FM bells", [L("concert_harp"), L("fm_bells", -9)],
  release=dr(0.8), chorus=0.2, reverb=0.45)
P("PLUCK", "Muted Pluck", "Dark, soft pluck", ["synth_pluck"],
  cutoff=cutoff(900), key_track=0.4, release=dr(0.25), reverb=0.25)
P("PLUCK", "Pizz & Pluck", "Pizzicato with a synth pluck", [L("pizz_strings"), L("synth_pluck", -6)],
  release=dr(0.3), reverb=0.3)
P("PLUCK", "Mbira Keys", "Kalimba and marimba together", [L("kalimba"), L("marimba", -4)],
  release=dr(0.5), reverb=0.3)

# =============================================================== BASS
P("BASS", "Sub Bass", "Deep, pure sub", ["sub_bass"], release=dr(0.08), reverb=0.0)
P("BASS", "Upright Bass", "Plucked double bass", ["upright_bass"], release=dr(0.15), reverb=0.12)
P("BASS", "Analog Bass", "Classic mono-synth bass", ["analog_bass"], release=dr(0.1), reverb=0.0)
P("BASS", "FM Bass", "Punchy FM bass", ["fm_bass"], release=dr(0.1), reverb=0.0)
P("BASS", "Sub & Saw", "Sub with an analog edge", [L("sub_bass"), L("analog_bass", -5)],
  release=dr(0.1), reverb=0.0)
P("BASS", "Jazz Upright", "Warm upright for walking lines", ["upright_bass"],
  cutoff=cutoff(2200), release=dr(0.2), reverb=0.18, rev_size=0.45)
P("BASS", "Acid Bass", "Squelchy resonant bass", ["analog_bass"],
  cutoff=cutoff(300), resonance=0.62, env_amt=0.82, decay=dr(0.3), sustain=0.3, release=dr(0.08), reverb=0.0)
P("BASS", "Deep FM", "FM bass with sub weight", [L("fm_bass"), L("sub_bass", -4)], release=dr(0.1), reverb=0.0)
P("BASS", "Picked Bass", "Picked electric bass tone", [L("muted_electric", octave=-1)],
  cutoff=cutoff(2500), release=dr(0.12), reverb=0.0)
P("BASS", "Big Bass", "Analog, FM and sub stacked",
  [L("analog_bass"), L("fm_bass", -5), L("sub_bass", -5)], release=dr(0.1), reverb=0.0)

# =============================================================== BELLS
P("BELLS", "Vibraphone", "Vibes with the motor on", ["vibraphone"],
  release=dr(0.8), lfo_rate=lfo(5.0), lfo_amp=0.3, reverb=0.3)
P("BELLS", "Marimba", "Warm rosewood marimba", ["marimba"], release=dr(0.4), reverb=0.25)
P("BELLS", "Glockenspiel", "Bright orchestral glockenspiel", ["glockenspiel"], release=dr(0.6), reverb=0.32)
P("BELLS", "Tubular Bells", "Concert chimes", ["tubular_bells"], release=dr(1.0), reverb=0.4, rev_size=0.85)
P("BELLS", "FM Bells", "Digital FM bells", ["fm_bells"], release=dr(0.9), chorus=0.2, reverb=0.35)
P("BELLS", "Music Box", "A tiny music box", [L("glockenspiel"), L("fm_bells", -6)],
  cutoff=cutoff(6000), release=dr(0.6), reverb=0.38)
P("BELLS", "Crystal Bells", "FM bells over glass with echoes", [L("fm_bells"), L("glass_pad", -8)],
  release=dr(1.2), delay=0.3, delay_time="1/4.", reverb=0.5, rev_size=0.9)
P("BELLS", "Mallet Stack", "Vibes and marimba together", [L("vibraphone"), L("marimba", -4)],
  release=dr(0.7), reverb=0.3)
P("BELLS", "Celeste", "A soft celesta-like bell", [L("glockenspiel", octave=-1), L("fm_epiano", -9)],
  cutoff=cutoff(5000), release=dr(0.6), reverb=0.35)
P("BELLS", "Church Bells", "Chimes ringing over an organ", [L("tubular_bells"), L("pipe_organ", -10)],
  release=dr(1.5), reverb=0.7, rev_size=0.95)
P("BELLS", "Dream Vibes", "Vibraphone in a haze", [L("vibraphone"), L("air_pad", -9)],
  release=dr(1.2), lfo_rate=lfo(5.0), lfo_amp=0.25, chorus=0.3, reverb=0.5)

assert len(PRESETS) == 128, len(PRESETS)
_order = {c: i for i, c in enumerate(CATEGORIES)}
assert [p["cat"] for p in PRESETS] == sorted((p["cat"] for p in PRESETS), key=_order.get), "keep category order"
assert len({p["name"] for p in PRESETS}) == len(PRESETS), "duplicate preset names"


def write_header(params, path):
    import sonix_params as sp
    keys = [p["key"] for p in params if p.get("preset")]
    rows = []
    for pr in PRESETS:
        v = pr["values"]
        vals = []
        for k in keys:
            x = v[k]
            if k == "delay_time":
                x = sp.DELAY_TIMES.index(x)
            vals.append(sp.cf(x))
        rows.append("    { " + ", ".join(vals) + " },  /* %s */" % pr["name"])
    L = ["/* Generated by tools/sonix_params.py from tools/presets.py: the factory presets. */",
         "#define NPRESET %d" % len(PRESETS),
         "#define NPRESET_KEYS %d" % len(keys),
         "static const char *const PRESET_KEYS[NPRESET_KEYS] = { " + ", ".join(sp.c_str(k) for k in keys) + " };",
         "static const char *const PRESET_NAME[NPRESET] = {\n" + "".join("    %s,\n" % sp.c_str(p["name"]) for p in PRESETS) + "};",
         "static const char *const PRESET_DESC[NPRESET] = {\n" + "".join("    %s,\n" % sp.c_str(p["desc"]) for p in PRESETS) + "};",
         "static const unsigned char PRESET_CAT[NPRESET] = { " + ", ".join(str(CATEGORIES.index(p["cat"])) for p in PRESETS) + " };",
         "static const float PRESET_VAL[NPRESET][NPRESET_KEYS] = {\n" + "\n".join(rows) + "\n};"]
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")
