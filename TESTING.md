# Device test (v0.3.0: presets, layers, settings and the new faceplate)

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
- `dist/Sonix-0.3.0-mpc-armv7.zip`, about 125 MB (v0.2 was 220 MB), also copied to your Windows Downloads

If the build fails, send me the last ~30 lines of output.

## 2. Install
Back up `MPC.settings`, then install the zip like your other plugins. It replaces v0.2 in the same folder. The
parameter list changed again, so open a fresh Sonix rather than a project saved with v0.2.

## 3. Checks on the MPC
| # | Check | How | Expect |
| --- | --- | --- | --- |
| 1 | It loads | Plugin browser → Sonix on a new track | MAIN page, Concert Grand on the LCD, plays at once |
| 2 | PRESET list | MPC's PRESET button / menu for the track | 128 presets by name; picking one changes the sound and the LCD |
| 3 | Instant presets | Pick presets while playing pads | each one plays right away, no "Loading" pause (after the first few seconds) |
| 4 | ◀ ▶ on the LCD | Tap the arrows | previous / next preset, across categories, wraps around |
| 5 | Preset list | Tap STRINGS, then a name in the list; tap STRINGS again | the LCD lists the category's presets; a tap loads one (lit); the lit button again closes the list |
| 5b | Scrolling | Touch the preset name on the LCD, turn the data wheel; turn Q-Link 16 | presets step one by one (two clicks each); the list follows into the next category |
| 6 | PRESET list stays open | Open MPC's PRESET list and play pads | the list doesn't close while the waveform moves |
| 7 | Waveform | Play, soft and hard | the LCD wave moves smoothly while notes sound, taller when louder, flat when silent |
| 8 | FAV / LIST | ★ FAV on a few presets, then ★ LIST on, then ◀ ▶ | a ★ on the LCD's top line; ◀ ▶ only visit the favourites |
| 9 | Edited | Turn CUTOFF | LCD top line says EDITED; the bottom line shows CUTOFF and its value for a moment |
| 10 | Q-Links, MAIN | Q-Link button through the four columns | FILTER, AMP ENVELOPE, LFO, FX & OUTPUT: four knobs each, outlined on screen |
| 11 | LAYERS page | MPC's LAYERS tab | four strips; ◀ ▶ change a layer's sound, ON / SOLO work, the LEDs light while a layer sounds |
| 12 | Q-Links, LAYERS | Turn them | each column is one layer: LEVEL, PAN, OCTAVE, FINE |
| 13 | SETTINGS page | Tap THEME buttons | the whole plugin changes colorway at once |
| 14 | Tuning | A4 REF 432 / 440, TRANSPOSE, BEND | pitch moves; A4 and transpose show on the MAIN LCD |
| 15 | Playing | POLYPHONY 4, VELOCITY curve SOFT / HARD | at most 4 notes; soft pads louder on SOFT |
| 16 | Mod wheel | MOD WHEEL VIBRATO / FILTER, move the wheel (or a CC1 from a controller) | vibrato, or the filter opens |
| 17 | Animation | ANIMATION OFF / LOW / SMOOTH | no wave / a slower wave / the smooth wave |
| 18 | CPU | A 4-layer preset (e.g. Pad Stack, Epic Octaves), busy chords | no crackles; note the CPU bar in the header |
| 19 | Screen load | Play busy parts with the page open | the screen stays responsive |
| 20 | Save / reload | Save the project, reload | the preset, your edits, layers, settings and favourites come back |
| 21 | Text size | Look at the three pages | names and values easy to read without looking oversized |

Tell me what passed, what didn't, and anything that looked or sounded off (a preset too loud or quiet, text that
doesn't fit, a button that does nothing). Those notes decide the next round.
