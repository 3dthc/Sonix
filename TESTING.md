# Device test (v0.3.7: live text at its true size, big sound names on the LAYERS page)

Goal: confirm on the MPC Live II that the 128 presets show in MPC's PRESET list and switch instantly, the three
pages (MAIN, LAYERS, SETTINGS) work in all three colorways, and the page and the engine stay light on MPC's CPU.

## 1. Build (Ubuntu in WSL)
Start Docker Desktop, then paste this into Ubuntu:

```sh
cd ~ && { [ -d Sonix ] || git clone https://github.com/3dthc/Sonix.git; } && cd Sonix && git pull && bash install.sh
```

v0.3 rebuilds the instruments (45 now, from the same open sources plus a few more folders: about 300 MB of new
downloads, 10-20 minutes). Expected near the end:
- `exported: VSTPluginMain`
- `highest glibc: GLIBC_2.x` (2.31 or lower is ideal)
- `dist/Sonix-0.3.7-mpc-armv7.zip`, about 140 MB (eleven colorways) (v0.2 was 220 MB), also copied to your Windows Downloads

If the build fails, send me the last ~30 lines of output.

## 2. Install
Back up `MPC.settings`, then install the zip like your other plugins. It replaces the old version in the same folder.
**Restart the MPC afterwards** (it keeps the old faceplate pictures until then). The parameter list changed again, so
open a fresh Sonix rather than a project saved with an earlier version.

## 3. Checks on the MPC
| # | Check | How | Expect |
| --- | --- | --- | --- |
| 1 | It loads | Plugin browser → Sonix on a new track | MAIN page, Concert Grand on the LCD, plays at once |
| 2 | PRESET list | MPC's PRESET button / menu for the track | 128 presets by name; picking one changes the sound and the LCD |
| 3 | Instant presets | Pick presets while playing pads | each one plays right away, no "Loading" pause (after the first few seconds) |
| 4 | ◀ ▶ on the LCD | Tap the arrows, several times at a normal pace | one preset per tap, inside the bank, wraps around; no tap is missed |
| 5 | Bank browser (text size) | BROWSE tab: tap STRINGS on the left, then a patch on the right | the bank lights, its patches list on the right; a tap loads the patch and lights it; names read easily from arm's length |
| 5b | Data wheel | Tap a patch, turn the wheel; tap a bank, turn it; tap BROWSE, LAYERS or ★ FAV, turn it; touch the preset name, turn it; Q-Link 16 | after a patch, a tab, FAV or the name: one preset per click inside the bank; after a bank: one bank per click; never flips a tab or FAV |
| 5c | LAYERS tab | Tap LAYERS, then a layer's box | the waveform and four big layer boxes; a tap on a box turns that layer on / off |
| 5d | Colorways | SETTINGS → THEME ◀ PREV / NEXT ▶ through all eleven | each faceplate changes at once; list rows are easy to read on every one |
| 5e | Memory | MPC's own memory / CPU readout before and after loading Sonix, then after stepping through all themes | note the numbers; nothing slows down |
| 6 | PRESET list stays open | Open MPC's PRESET list and play pads | the list doesn't close while the waveform moves |
| 7 | Waveform | LAYERS tab: play, soft and hard | the LCD wave moves smoothly while notes sound, taller when louder, flat when silent |
| 8 | Favourites | ★ FAV on a few presets, then ★ FAVORITES; ◀ ▶ and the wheel; ★ FAVORITES again | the list shows every favourite (11 a page, PAGE x / y on the top line); ◀ ▶ and the wheel go through them; again: back to the bank |
| 9 | Edited | Turn CUTOFF | LCD top line says EDITED; the bottom line shows CUTOFF and its value for a moment |
| 10 | Q-Links, MAIN | Q-Link button through the four columns | FILTER, AMP ENVELOPE, LFO, FX & OUTPUT: four knobs each, outlined on screen |
| 11 | LAYERS page | MPC's LAYERS tab | four strips; ◀ ▶ change a layer's sound one per tap, ON / SOLO work, the LEDs light while a layer sounds |
| 12 | Q-Links, LAYERS | Turn them | each column is one layer: LEVEL, PAN, OCTAVE, FINE |
| 13 | SETTINGS page | THEME ◀ ▶ | the whole plugin changes colorway at once, its name on the small LCD |
| 14 | Tuning | A4 REF 432 / 440, TRANSPOSE, BEND | pitch moves; A4 and transpose show on the MAIN LCD |
| 15 | Playing | POLYPHONY 4, VELOCITY curve SOFT / HARD | at most 4 notes; soft pads louder on SOFT |
| 16 | Mod wheel | MOD WHEEL VIBRATO / FILTER, move the wheel (or a CC1 from a controller) | vibrato, or the filter opens |
| 17 | Animation | ANIMATION OFF / LOW / SMOOTH | no wave / a slower wave / the smooth wave |
| 18 | CPU | A 4-layer preset (e.g. Pad Stack, Epic Octaves), busy chords | no crackles; note the CPU bar in the header |
| 19 | Screen load | Play busy parts with the page open | the screen stays responsive |
| 20 | Save / reload | Save the project, reload | the preset, your edits, layers, settings and favourites come back |
| 21 | Text size | Turn knobs on all three pages, from arm's length | values big and bright under every knob, nothing cut off or touching the next name |

Tell me what passed, what didn't, and anything that looked or sounded off (a preset too loud or quiet, text that
doesn't fit, a button that does nothing). Those notes decide the next round.
