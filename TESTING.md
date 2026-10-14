# Device test (v0.2.0: the Sonix faceplate)

Goal: confirm the Sonix faceplate loads on the MPC Live II in all three colorways, every control on it works, the
four layers and the effects sound right, and the page stays light on MPC's CPU.

## 1. Build (Ubuntu in WSL)
Start Docker Desktop, then paste this into Ubuntu:

```sh
cd ~ && { [ -d Sonix ] || git clone https://github.com/3dthc/Sonix.git; } && cd Sonix && git pull && bash install.sh
```

The first time, `install.sh` builds the 28 instruments from their open sources (about 2 GB of downloads, 10-20
minutes); if you already built v0.1.0, it reuses them. The faceplate uses the toolchain's browser renderer, the same
one Fretty's skin uses (its Docker image is built once). Expected near the end:
- `exported: VSTPluginMain`
- `highest glibc: GLIBC_2.x` (2.31 or lower is ideal)
- `dist/Sonix-0.2.0-mpc-armv7.zip`, about 220 MB, also copied to your Windows Downloads

If the build fails, send me the last ~30 lines of output.

## 2. Install
Back up `MPC.settings`, then install the zip like your other plugins. It replaces v0.1.0 in the same folder
(`3dthc - VST - Sonix`). The parameter list changed since v0.1.0, so open a fresh Sonix rather than a project saved
with the test build.

## 3. Checks on the MPC
| # | Check | How | Expect |
| --- | --- | --- | --- |
| 1 | It loads | Plugin browser → Sonix on a new track | the Midnight faceplate, Grand Piano on the LCD |
| 2 | Plays | Pads, soft to hard | piano, darker and quieter when soft; MIDI LED lit while a pad is held |
| 3 | LCD wave | Play a few notes, then stop | a wave appears on the LCD while you play, flat again ~half a second after |
| 4 | Categories | Tap PAD, STRINGS, BELLS ... | that button lights; LCD shows the category's first instrument |
| 5 | PREV / NEXT | Tap them | steps through the instruments of the lit category (wraps around) |
| 6 | Layers | LAYER B on, play | piano + Warm Pad together; the LCD's B cell shows WARM PAD |
| 7 | Edit layer | Tap the B cell on the LCD, then a category | the B cell outlines; only layer B's instrument changes |
| 8 | FAV | Tap FAV | a star after the LCD name; it stays when you come back to that instrument |
| 9 | Filter | CUTOFF down, RESONANCE up, ENV AMT either way | darker, a resonant peak, a sweep on each note |
| 10 | LFO | DEPTH up, RATE up and down | the filter wobbles; the LED by LFO lights while DEPTH is above 0 |
| 11 | Effects | CHORUS, DELAY, REVERB | wider, echoes in time with the project tempo (dotted eighths), a room |
| 12 | Knob readout | Turn any knob | the LCD's bottom line shows its name and value, then goes back after ~1.5 s |
| 13 | Tuning | TUNE, A4 REF 432 / 440 / 442 | pitch moves; the LCD top line shows the A4 value |
| 14 | Themes | Tap THEME three times | Platinum, Studio Grey, back to Midnight; the whole page changes |
| 15 | Q-Links | Turn Q-Links 1-16 | 1-7 filter and envelope, 8 the instrument, 9-16 LFO, effects, tune, A4, volume |
| 16 | CPU | All four layers on, a few busy chords, effects up | no crackles; the CPU bar in the header moves (it updates every 2 s) |
| 17 | Screen load | Play busy parts with the page open | the screen stays responsive (pictures change only on notes and taps) |
| 18 | Save / reload | Save project, reload | layers, instruments, knobs, theme and favourites come back |

Tell me what passed, what didn't, and anything that looked or sounded off (text that doesn't fit, a button that
does nothing, an instrument too loud or quiet). Those notes decide the next round.
