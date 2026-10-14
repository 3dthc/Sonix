# Device test (v0.1.0, milestone 1: single-layer sampler)

Goal: confirm the first Sonix build loads on the MPC Live II, plays every instrument in tune, keeps held notes
sustaining, and stays light on the CPU. The screen is a plain test page (Volume, Instrument, ADSR, Tune, A4 Ref);
the real Sonix faceplate comes in a later milestone.

## 1. Build (Ubuntu in WSL)
Start Docker Desktop, then paste this into Ubuntu (it clones the repo the first time; GitHub asks you to sign in
because the repo is private):

```sh
cd ~ && { [ -d Sonix ] || git clone https://github.com/3dthc/Sonix.git; } && cd Sonix && git pull && bash install.sh
```

`install.sh` checks Docker and the toolchain (`~/mpc-vst-plugins`, cloned if missing), then builds the 28
instruments from their open sources the first time (it downloads the sample sets from GitHub, about 2 GB, and runs
the same pipeline that made the demo clips; allow 10-20 minutes), then builds the plugin and copies
`Sonix-0.1.0-mpc-armv7.zip` into your Windows Downloads. Later runs skip the instruments. Expected near the end:
- `exported: VSTPluginMain`
- `highest glibc: GLIBC_2.x` (2.31 or lower is ideal)
- `dist/Sonix-0.1.0-mpc-armv7.zip`, about 210 MB

If the build fails, send me the last ~30 lines of output.

## 2. Install
Same as your other ports: back up `MPC.settings`, then the zip's `install.sh` (MPC is stopped, then restarted) or the
MPC plugin installer app. The plugin folder is `3dthc - VST - Sonix` with `sonix.so`, `Plugin Skins/` and `engine/`.

RAM: an instrument is decoded into RAM the first time you pick it and kept while any Sonix has it picked (and for 20 s
after). The grand piano is the largest at ~155 MB, the upright ~120 MB, the guitars 50-65 MB, most others 5-40 MB.

## 3. Checks on the MPC
| # | Check | How | Expect |
| --- | --- | --- | --- |
| 1 | It loads | Plugin browser → Sonix on a new track | the test page appears, no crash |
| 2 | Samples load | Wait a few seconds after inserting | Grand Piano plays (silence for the first seconds is expected) |
| 3 | Velocity | Pads soft → hard (Full Level off) | soft = quieter and darker, hard = louder and brighter |
| 4 | Tuning | Play A4 against a tuner or another plugin | in tune; TUNE moves in semitones, A4 REF 432 / 440 / 442 audibly shifts the pitch |
| 5 | Held notes | Warm Pad, Strings, Choir, Organ: hold a note 10 s | keeps sounding with no gap or click |
| 6 | Instruments | INSTRUMENT list → try all 28 | each loads after a short wait, plays in tune at a similar loudness |
| 7 | ADSR | Raise ATTACK, lower SUSTAIN, raise RELEASE | slow fade-in, quieter while held, tail after release |
| 8 | Sustain pedal | If you use one: hold, release keys, lift | notes ring until you lift it |
| 9 | Polyphony | Mash many pads at once | no crackles; the oldest notes give way smoothly |
| 10 | CPU | Busy chords on a pad or the strings for a minute | no dropouts (note the CPU meter if you can) |
| 11 | Q-Links | Turn Q-Links 1-8 | Volume, Instrument, Attack, Decay, Sustain, Release, Tune, A4 Ref follow |
| 12 | Save / reload | Save project, reload | instrument and settings come back |
| 13 | Two instances | Sonix on two tracks, different instruments | both play; nothing crashes when you remove one |

Tell me what passed, what didn't, and anything that sounded off (an instrument too loud or quiet, out of tune, a
click). Those notes decide the next round.
