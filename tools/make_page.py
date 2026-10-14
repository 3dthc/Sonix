"""Generate the Sonix Demo Pack audition page from the library keymaps."""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prep import LIB, SOURCES, midi_name  # noqa: E402

OUT = os.path.join(LIB, "library")
DEST = sys.argv[1]
BUDGET_LO, BUDGET_HI = 300, 500

ORDER = ["PIANO", "KEYS", "PAD", "STRINGS", "BRASS", "CHOIR", "LEAD", "PLUCK", "BASS", "BELLS", "SYNTH", "FX"]
NEW = {"vibraphone", "glockenspiel", "tubular_bells", "marimba", "concert_harp", "kalimba", "harpsichord",
       "pipe_organ", "clavisynth", "steel_guitar", "orch_brass", "synth_brass", "drawbar_organ", "fm_bells",
       "synth_pluck", "ooh_choir", "trance_chords", "riser", "downlifter"}

NOTES = {
    "grand_piano": "Every minor third, 4 of its 16 velocity layers.",
    "upright_piano": "Every minor third of the 88-key upright, all 4 velocity layers.",
    "harpsichord": "Flemish harpsichord, lower 8-foot choir.",
    "pipe_organ": "Quiet flute stop below velocity 65, full open stop above it.",
    "clavisynth": "Recorded from a Yamaha TX81Z FM module: bright, plucky clav tone.",
    "drawbar_organ": "Seven sine drawbars, key click and a light rotary shimmer.",
    "fm_epiano": "Two-operator FM tine piano. Hard hits bark more than soft ones.",
    "warm_pad": "Detuned saw stack with a sub-octave, filtered warm.",
    "string_ensemble": "Cello, viola and violin sections across the range, sustain with vibrato. Bars 3–4 switch to the louder layer.",
    "orch_brass": "Trombone low, horn middle, trumpet high: sustains, soft and loud layers.",
    "synth_brass": "Detuned saws with a baked filter swell on the attack.",
    "aah_choir": "Eight formant-synthesized \"aah\" voices with their own vibrato, drift and breath.",
    "ooh_choir": "The same eight-voice choir on an \"ooh\" vowel: darker and rounder.",
    "supersaw_lead": "Seven detuned saws spread across the stereo field.",
    "nylon_guitar": "Fingerstyle for soft notes, light and firm pick above.",
    "steel_guitar": "Steel-string acoustic, three dynamics, one string per note in first position.",
    "concert_harp": "Pedal harp across almost its whole range, two dynamics.",
    "kalimba": "Traditional tuning corrected per tine to equal temperament so it plays in key.",
    "synth_pluck": "Saw and square through a fast filter snap.",
    "sub_bass": "Sine sub with a little filtered saw grit. Headphones or a sub help here.",
    "vibraphone": "Hard mallets, two dynamics, motor off.",
    "glockenspiel": "Three dynamics. Sounds two octaves above written pitch, mapped where it sounds.",
    "tubular_bells": "Two dynamics, each bell retuned to equal temperament.",
    "marimba": "Three dynamics across five octaves.",
    "fm_bells": "Inharmonic FM bell with a long ring.",
    "trance_chords": "A whole minor-seventh chord from each key, for gated chord stabs.",
    "riser": "Noise sweep and rising saw, one sample across the keys.",
    "downlifter": "Falling noise sweep and saw, one sample across the keys.",
}


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    insts = []
    for name in sorted(os.listdir(OUT)):
        kp = os.path.join(OUT, name, "keymap.json")
        if os.path.exists(kp):
            insts.append(json.load(open(kp)))
    total = sum(k["total_bytes"] for k in insts) / 1e6
    os.makedirs(os.path.join(DEST, "clips"), exist_ok=True)

    units = []
    for cat in ORDER:
        group = [k for k in insts if k["category"] == cat]
        if not group:
            continue
        units.append(f'    <h2 id="{cat.lower()}">{cat.title()}</h2>')
        for k in group:
            n = k["name"]
            zs = k["zones"]
            layers = len({z["lovel"] for z in zs})
            roots = [z["root"] for z in zs]
            tags = [f"{layers} VEL LAYER{'S' if layers > 1 else ''}"] if layers > 1 else []
            if k["loop"]:
                tags.append("LOOPED")
            if n in NEW:
                tags.append("NEW")
            src = SOURCES[k["source"]]
            rng = midi_name(min(roots)) if len(set(roots)) == 1 else f"{midi_name(min(roots))}–{midi_name(max(roots))}"
            shutil.copy(os.path.join(LIB, "clips", n + ".mp3"), os.path.join(DEST, "clips", n + ".mp3"))
            license = "Original to Sonix" if k["source"] == "sonix" else src["license"].split(" ")[0]
            units.append(f'''    <article class="unit{' new' if n in NEW else ''}">
      <div class="lcd"><div class="top"><span>{cat}</span><span>{' · '.join(tags) or '&nbsp;'}</span></div><div class="name">{esc(n.replace('_', ' ').upper())}</div><div class="spec">{len(zs)} ZONES · {k['total_bytes'] / 1e6:.1f} MB · {rng}</div></div>
      <div class="side">
        <p class="src"><strong>{esc(src['name'])}</strong> · {esc(license)}. {esc(NOTES.get(n, ''))}</p>
        <audio id="a-{n}" controls preload="none" src="clips/{n}.mp3"></audio>
      </div>
    </article>''')

    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_template.html")).read()
    html = (tpl.replace("{{TOTAL}}", f"{total:.1f}")
               .replace("{{COUNT}}", str(len(insts)))
               .replace("{{FILL}}", f"{min(100, total / BUDGET_HI * 100):.1f}")
               .replace("{{UNITS}}", "\n".join(units))
               .replace("{{NAV}}", " · ".join(f'<a href="#{c.lower()}">{c.title()}</a>' for c in ORDER
                                               if any(k["category"] == c for k in insts))))
    open(os.path.join(DEST, "index.html"), "w").write(html)
    print(f"{len(insts)} instruments, {total:.1f} MB")


if __name__ == "__main__":
    main()
